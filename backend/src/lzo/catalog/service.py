"""Governed core acceptance with asynchronous, permission-free catalog projection."""
import base64
import binascii
import json
from uuid import UUID, uuid4
from sqlalchemy import insert, select, update
from sqlalchemy.exc import DBAPIError
from lzo.artifacts.models import artifact_versions
from lzo.artifacts.service import ArtifactService, _permission, _scope
from lzo.auth.models import authorization_state
from lzo.auth.permissions import authorized_statement, validate_actor
from lzo.contracts import ResourceScope, RevisionRef
from lzo.errors import ApiError, Conflict
from lzo.governance.approvals import ApprovalService
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.models import resource_heads, revisions
from lzo.governance.history import revision_visibility_dependencies
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import ChangeDescriptor, digest_change, register_policy
from lzo.ingestion.sources import add_scoped, bind
from lzo.ontology.drafts import settings_for, protected_closure
from lzo.storage.blobs import get_blob_store
from lzo.storage.registry import StorageRegistry
from lzo.synchronization.contracts import SyncEnvelope
from lzo.synchronization.delivery import enqueue_delivery, require_deliveries, DeliveriesPending, DeliveriesFailed
from lzo.catalog.repository import entries, device_models, CatalogRepository
from lzo.catalog.schemas import (CreateModel, CreateDocument, VersionModel, VersionDocument,
    ModelMetadata, DocumentMetadata, CatalogView, CatalogPage, DeviceModelView)


def _metadata(kind, value):
    return (ModelMetadata if kind == 'model' else DocumentMetadata).model_validate(value)


def _ids(metadata):
    if isinstance(metadata, ModelMetadata):
        evidence = set(metadata.evidence_version_ids)
        for capability in metadata.capabilities:
            evidence.update(capability.evidence_version_ids)
        return evidence | set(metadata.attachment_version_ids), evidence, set()
    return {metadata.artifact_version_id}, set(), set(metadata.model_ids) | set(metadata.entity_ids)


def _dependencies(uow, metadata):
    artifacts, _, resources = _ids(metadata)
    if isinstance(metadata, DocumentMetadata):
        for value in metadata.entity_iris:
            try:
                prefix = 'urn:lzo:object:'
                if not value.startswith(prefix):
                    raise ValueError()
                identifier = UUID(value[len(prefix):])
                if value != prefix + str(identifier):
                    raise ValueError()
            except ValueError:
                raise ApiError('CATALOG_ENTITY_IRI_REQUIRED', 422, 'Registered native entity IRI required') from None
            if _scope(uow, identifier)['resource_kind'] != 'object':
                raise ApiError('NOT_FOUND', 404, 'Entity not found')
            resources.add(identifier)
    for identifier in artifacts:
        owner = uow.session.execute(select(artifact_versions.c.resource_id).where(artifact_versions.c.id == identifier)).scalar_one_or_none()
        if owner is None:
            raise ApiError('NOT_FOUND', 404, 'Artifact not found')
        resources.update((identifier, owner))
    return protected_closure(uow, resources)


def _policy(uow, targets, command):
    if set(command) != {'id', 'kind', 'expected_revision', 'metadata'} or command['kind'] not in ('model', 'document'):
        raise ValueError('Invalid catalog descriptor')
    identifier = UUID(command['id'])
    row = uow.session.execute(select(entries).where(entries.c.resource_id == identifier)).mappings().first()
    if row is None or row['kind'] != command['kind']:
        raise ApiError('NOT_FOUND', 404, 'Catalog record not found')
    metadata = _metadata(command['kind'], command['metadata'])
    references = _dependencies(uow, metadata) - {identifier}
    expected_targets = (identifier, *sorted(references, key=str))
    if tuple(targets) != expected_targets:
        raise ValueError('Catalog targets differ')
    baseline = {str(r['resource_id']): r['revision'] for r in uow.session.execute(
        select(resource_heads).where(resource_heads.c.resource_id.in_(expected_targets))).mappings()}
    baseline['authorization'] = uow.session.execute(select(authorization_state.c.version)).scalar_one()
    return ChangeDescriptor(kind='catalog.version', target_ids=expected_targets, command=command,
        baseline=baseline, impact={'catalog_id': str(identifier), 'state': metadata.state},
        submit_action='catalog.manage', review_action='catalog.review', operation_target_ids=(identifier,),
        operation_key='catalog.version:' + str(identifier))


register_policy('catalog.version', _policy)


class CatalogService:
    def __init__(self, uow, registry=None):
        self.uow, self.registry = uow, registry
        self.settings = settings_for(uow)

    def _references(self, actor, metadata, target=None):
        artifacts, evidence, resources = _ids(metadata)
        service = ArtifactService(self.uow, get_blob_store(self.settings), self.settings)
        from lzo.ingestion.evidence import EvidenceService
        evidence_service = EvidenceService(self.uow, service.store, self.settings)
        for identifier in evidence:
            item = evidence_service.read(actor, identifier)
            if target is not None:
                service.authorize_link(actor, identifier, target)
            verified = metadata.state == 'verified' or any(
                c.state == 'verified' and identifier in c.evidence_version_ids for c in metadata.capabilities)
            if verified and item.review_state != 'accepted':
                raise ApiError('CATALOG_EVIDENCE_NOT_ACCEPTED', 422, 'Verified capabilities require accepted controlled evidence')
        for identifier in artifacts:
            row = service._version(actor, identifier)
            from lzo.contracts import BlobRef
            with service.store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], identifier)):
                pass
        for identifier in resources:
            _permission(self.uow, actor, identifier, 'view')
        if isinstance(metadata, DocumentMetadata):
            for identifier in metadata.model_ids:
                self._row(actor, identifier, kind='model')
        else:
            for capability in metadata.capabilities:
                if capability.state == 'verified' and (not capability.evidence_version_ids or metadata.state != 'verified'):
                    raise ApiError('CATALOG_VERIFICATION_REQUIRED', 422, 'Verified capability requires evidence and governed verified model')
            if metadata.state == 'verified' and not evidence:
                raise ApiError('CATALOG_VERIFICATION_REQUIRED', 422, 'Verified model requires controlled evidence')
        refs = _dependencies(self.uow, metadata)
        for identifier in refs:
            _permission(self.uow, actor, identifier, 'view')
        return refs

    def _row(self, actor, identifier, action='view', kind=None, revision=None):
        _permission(self.uow, actor, identifier, action)
        entry = self.uow.session.execute(select(entries).where(entries.c.resource_id == identifier)).mappings().first()
        if entry is None or (kind is not None and kind != entry['kind']):
            raise ApiError('NOT_FOUND', 404, 'Catalog record not found')
        query = select(revisions).where(revisions.c.resource_id == identifier)
        if revision is not None:
            query = query.where(revisions.c.revision == revision)
        row = self.uow.session.execute(query.order_by(revisions.c.revision.desc()).limit(1)).mappings().first()
        if row is None:
            raise ApiError('NOT_FOUND', 404, 'Catalog version not found')
        for dependency in self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).where(
                revision_visibility_dependencies.c.event_id == row['event_id'])).scalars():
            _permission(self.uow, actor, dependency, 'view')
        return row

    def _status(self, row):
        try:
            require_deliveries(self.uow, row['event_id'], ('catalog',))
        except DeliveriesFailed:
            return 'failed'
        except DeliveriesPending:
            return 'pending'
        registry = self.registry or StorageRegistry.from_settings(self.settings)
        try:
            rows = CatalogRepository(registry).versions([row['resource_id']])
            return 'current' if any(r['event_id'] == row['event_id'] and r['payload'] == row['payload'] for r in rows) else 'pending'
        except DBAPIError:
            return 'unavailable'
        finally:
            if self.registry is None:
                registry.close()

    def get(self, actor, identifier, revision=None, kind=None):
        row = self._row(actor, identifier, revision=revision, kind=kind)
        value = row['payload']
        return CatalogView(id=identifier, kind=value['kind'], revision=RevisionRef(identifier, row['revision'], row['event_id']),
            metadata=_metadata(value['kind'], value['metadata']), sha256=value.get('sha256'),
            recorded_at=row['recorded_at'], projection_status=self._status(row))

    def versions(self, actor, identifier, *, kind, cursor=None, limit=50):
        head = self._row(actor, identifier, kind=kind)
        if type(limit) is not int or not 1 <= limit <= min(self.settings.list_max_page_size, 1000):
            raise ApiError('INVALID_LIMIT', 422, 'Invalid catalog page size')
        context = {'id': str(identifier), 'actor': str(actor.subject_id), 'authorization': actor.authorization_version,
            'event': str(head['event_id'])}
        after = 0
        if cursor:
            try:
                if len(cursor) > 2048:
                    raise ValueError()
                value = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
                if not isinstance(value, dict) or set(value) != {'context', 'after'} or type(value['after']) is not int or value['after'] < 1:
                    raise ValueError()
                if value['context'] != context:
                    raise Conflict('CATALOG_CURSOR_STALE', 'Catalog or authorization changed; restart pagination')
                after = value['after']
            except (ValueError, TypeError, binascii.Error):
                raise ApiError('INVALID_CURSOR', 422, 'Invalid catalog cursor') from None
        query = select(revisions.c.revision).where(revisions.c.resource_id == identifier,
            revisions.c.revision > after).order_by(revisions.c.revision)
        output = []
        for ordinal in self.uow.session.execute(query).scalars():
            try:
                output.append(self.get(actor, identifier, ordinal, kind=kind))
            except ApiError as error:
                if error.status in (403, 404):
                    continue
                raise
            if len(output) > limit:
                break
        next_cursor = None
        if len(output) > limit:
            next_cursor = base64.urlsafe_b64encode(json.dumps({'context': context,
                'after': output[limit-1].revision.revision}).encode()).decode().rstrip('=')
        statuses = {item.projection_status for item in output[:limit]}
        status = next((s for s in ('failed', 'unavailable', 'pending') if s in statuses), 'current')
        return CatalogPage(items=output[:limit], next_cursor=next_cursor, projection_status=status)

    def _record(self, actor, identifier, expected, kind, metadata, references):
        sha256 = None
        if isinstance(metadata, DocumentMetadata):
            sha256 = self.uow.session.execute(select(artifact_versions.c.sha256).where(
                artifact_versions.c.id == metadata.artifact_version_id)).scalar_one()
        payload = {'kind': kind, 'metadata': metadata.model_dump(mode='json'), 'sha256': sha256}
        ref = append_revision(self.uow, actor, identifier, expected, payload, 'catalog.version.accepted')
        bind(self.uow, ref, references - {identifier})
        enqueue_delivery(self.uow, SyncEnvelope(event_id=ref.event_id, entity_id=identifier,
            aggregate_revision=ref.revision, target='catalog', operation='catalog.index', payload=payload,
            data_nature='synthetic' if isinstance(metadata, ModelMetadata) and metadata.synthetic else 'imported_unverified',
            source_refs=tuple(sorted(map(str, references)))))
        return {'id': str(identifier), 'revision': ref.revision}

    def _create(self, actor, command, key, kind):
        validate_actor(self.uow, actor)
        if command.scope.resource_id is not None:
            raise ApiError('INVALID_SCOPE', 422, 'A new scoped identity is required')
        if self.settings.storage_mode != 'split':
            raise Conflict('CATALOG_SPLIT_REQUIRED', 'Catalog requires split storage')
        def accept():
            lock_ledger(self.uow)
            if command.source_identity:
                previous = self.uow.session.execute(select(entries).where(entries.c.source_identity == command.source_identity)).mappings().first()
                if previous:
                    row = self._row(actor, previous['resource_id'], 'catalog.manage', revision=1)
                    scope = _scope(self.uow, previous['resource_id'])
                    if (row['payload']['metadata'] != command.metadata.model_dump(mode='json') or previous['kind'] != kind or
                        (scope['owner_scope_id'], scope['related_scope_ids'], scope['security_class']) !=
                        (command.scope.owner_scope_id, list(map(str, command.scope.related_scope_ids)), command.scope.security_class)):
                        raise Conflict('CATALOG_SOURCE_CONFLICT', 'Source identity has different content')
                    return {'id': str(previous['resource_id']), 'revision': 1}
            identifier = uuid4()
            add_scoped(self.uow, actor, identifier, command.scope, 'catalog.' + kind, 'catalog.manage')
            references = self._references(actor, command.metadata, identifier)
            if command.metadata.state != 'draft':
                raise Conflict('APPROVAL_REQUIRED', 'Create draft before governed state transition')
            self.uow.session.execute(insert(entries).values(resource_id=identifier, kind=kind, source_identity=command.source_identity))
            return self._record(actor, identifier, 0, kind, command.metadata, references)
        result = execute_idempotent(self.uow, actor, key, digest_change({'operation': 'catalog.create.' + kind,
            'command': command.model_dump(mode='json')}), accept)
        self._row(actor, UUID(result['id']), 'catalog.manage', revision=result['revision'])
        return self.get(actor, UUID(result['id']), revision=result['revision'])

    def create_model(self, actor, command: CreateModel, key):
        return self._create(actor, command, key, 'model')

    def create_document(self, actor, command: CreateDocument, key):
        return self._create(actor, command, key, 'document')

    def preview_version(self, actor, identifier, command):
        row = self._row(actor, identifier, 'catalog.manage')
        if row['revision'] != command.expected_revision:
            raise Conflict('REVISION_CONFLICT', 'Catalog revision changed')
        self._references(actor, command.metadata, identifier)
        descriptor = {'id': str(identifier), 'kind': row['payload']['kind'],
            **command.model_dump(mode='json', exclude={'approval_id'})}
        refs = _dependencies(self.uow, command.metadata) - {identifier}
        return ApprovalService(self.uow).preview(actor, 'catalog.version', (identifier, *sorted(refs, key=str)), descriptor)

    def _version(self, actor, identifier, command, key, kind):
        self._row(actor, identifier, 'catalog.manage', kind=kind)
        def accept():
            lock_ledger(self.uow)
            row = self._row(actor, identifier, 'catalog.manage', kind=kind)
            if row['revision'] != command.expected_revision:
                raise Conflict('REVISION_CONFLICT', 'Catalog revision changed')
            refs = self._references(actor, command.metadata, identifier)
            old = row['payload']['metadata']
            if old['state'] != 'draft' or command.metadata.state != 'draft':
                change = self.preview_version(actor, identifier, command)
                if command.approval_id is None:
                    raise Conflict('APPROVAL_REQUIRED', 'Independent approval required')
                ApprovalService(self.uow).require_executable(actor, command.approval_id, change.change_digest, change.baseline)
            return self._record(actor, identifier, command.expected_revision, kind, command.metadata, refs)
        result = execute_idempotent(self.uow, actor, key, digest_change({'operation': 'catalog.version.' + kind,
            'id': str(identifier), 'command': command.model_dump(mode='json')}), accept)
        return self.get(actor, identifier, revision=result['revision'], kind=kind)

    def version_model(self, actor, identifier, command: VersionModel, key):
        return self._version(actor, identifier, command, key, 'model')

    def version_document(self, actor, identifier, command: VersionDocument, key):
        return self._version(actor, identifier, command, key, 'document')

    def bind_device(self, actor, model_id, command, key):
        self._row(actor, model_id, 'catalog.manage', kind='model')
        _permission(self.uow, actor, command.entity_id, 'edit')
        if _scope(self.uow, command.entity_id)['resource_kind'] != 'object':
            raise ApiError('CATALOG_PHYSICAL_ENTITY_REQUIRED', 422, 'Physical object identity required')
        def accept():
            lock_ledger(self.uow)
            old = self.uow.session.execute(select(device_models).where(device_models.c.entity_id == command.entity_id)).mappings().first()
            identifier = old['resource_id'] if old else uuid4()
            if old:
                _permission(self.uow, actor, identifier, 'edit')
            else:
                scope = _scope(self.uow, command.entity_id)
                add_scoped(self.uow, actor, identifier, ResourceScope(None, scope['owner_scope_id'], (), scope['security_class']), 'catalog.device', 'edit')
            ref = append_revision(self.uow, actor, identifier, command.expected_revision,
                {'entity_id': str(command.entity_id), 'model_id': str(model_id)}, 'catalog.device.bound')
            bind(self.uow, ref, {command.entity_id, model_id})
            if old:
                self.uow.session.execute(update(device_models).where(device_models.c.entity_id == command.entity_id).values(model_id=model_id))
            else:
                self.uow.session.execute(insert(device_models).values(entity_id=command.entity_id, model_id=model_id, resource_id=identifier))
            return {'entity_id': str(command.entity_id), 'model_id': str(model_id),
                'revision': {'resource_id': str(identifier), 'revision': ref.revision, 'event_id': str(ref.event_id)}}
        result = execute_idempotent(self.uow, actor, key, digest_change({'operation': 'catalog.bind', 'model_id': str(model_id),
            'command': command.model_dump(mode='json')}), accept)
        _permission(self.uow, actor, UUID(result['revision']['resource_id']), 'edit')
        return DeviceModelView.model_validate(result)

    def devices(self, actor, model_id):
        self._row(actor, model_id, kind='model')
        validate_actor(self.uow, actor)
        statement = select(device_models, resource_heads.c.revision, resource_heads.c.event_id).join(resource_heads,
            resource_heads.c.resource_id == device_models.c.resource_id).where(device_models.c.model_id == model_id,
            device_models.c.entity_id.in_(authorized_statement(actor, 'view')),
            device_models.c.resource_id.in_(authorized_statement(actor, 'view'))).order_by(device_models.c.entity_id)
        return [DeviceModelView(entity_id=r['entity_id'], model_id=model_id, revision=RevisionRef(r['resource_id'], r['revision'], r['event_id']))
            for r in self.uow.session.execute(statement).mappings()]

    def list(self, actor, kind, *, cursor=None, limit=50, text=None, document_type=None, model_id=None, start=None, end=None, entity_id=None):
        validate_actor(self.uow, actor)
        if type(limit) is not int or not 1 <= limit <= min(self.settings.list_max_page_size, 1000):
            raise ApiError('INVALID_LIMIT', 422, 'Invalid catalog page size')
        if kind not in ('model', 'document') or (start and end and start > end):
            raise ApiError('INVALID_FILTER', 422, 'Invalid catalog filter')
        snapshot = self.uow.session.execute(select(entries.c.resource_id, resource_heads.c.event_id).join(
            resource_heads, resource_heads.c.resource_id == entries.c.resource_id).where(entries.c.kind == kind,
            entries.c.resource_id.in_(authorized_statement(actor, 'view'))).order_by(entries.c.resource_id)).all()
        context = {'actor': str(actor.subject_id), 'authorization': actor.authorization_version,
            'snapshot': digest_change([[str(identifier), str(event)] for identifier, event in snapshot]),
            'kind': kind, 'text': text, 'type': document_type, 'model': str(model_id) if model_id else None,
            'entity': str(entity_id) if entity_id else None,
            'start': str(start) if start else None, 'end': str(end) if end else None}
        after = None
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                value = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
                if not isinstance(value, dict) or set(value) != {'context', 'after'} or not isinstance(value['after'], str):
                    raise ValueError()
                after = UUID(value['after'])
                if value['context'] != context:
                    raise Conflict('CATALOG_CURSOR_STALE', 'Catalog or authorization changed; restart pagination')
            except (ValueError, TypeError, binascii.Error):
                raise ApiError('INVALID_CURSOR', 422, 'Invalid catalog cursor') from None
        query = select(entries.c.resource_id).where(entries.c.kind == kind,
            entries.c.resource_id.in_(authorized_statement(actor, 'view'))).order_by(entries.c.resource_id)
        if after:
            query = query.where(entries.c.resource_id > after)
        output = []
        for identifier in self.uow.session.execute(query).scalars():
            try:
                item = self.get(actor, identifier)
            except ApiError as error:
                if error.status in (403, 404):
                    continue
                raise
            metadata = item.metadata.model_dump(mode='json')
            if text and text.casefold() not in json.dumps(metadata, ensure_ascii=False).casefold():
                continue
            if document_type and metadata.get('document_type') != document_type:
                continue
            if model_id and str(model_id) not in metadata.get('model_ids', []):
                continue
            if entity_id and str(entity_id) not in metadata.get('entity_ids', []) and 'urn:lzo:object:'+str(entity_id) not in metadata.get('entity_iris', []):
                continue
            date = metadata.get('publication_date')
            if (start and (not date or date < str(start))) or (end and (not date or date > str(end))):
                continue
            output.append(item)
            if len(output) > limit:
                break
        next_cursor = None
        if len(output) > limit:
            next_cursor = base64.urlsafe_b64encode(json.dumps({'context': context, 'after': str(output[limit-1].id)}, separators=(',', ':')).encode()).decode().rstrip('=')
        statuses = {item.projection_status for item in output[:limit]}
        status = next((s for s in ('failed', 'unavailable', 'pending') if s in statuses), 'current')
        return CatalogPage(items=output[:limit], next_cursor=next_cursor, projection_status=status)


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('catalog.version.accepted', 'catalog-v1')
register_nonsemantic('catalog.device.bound', 'catalog-v1')
register_nonsemantic('catalog.example.created', 'catalog-v1')
