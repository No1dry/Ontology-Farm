"""Governed batch acceptance; every query rechecks core and linked authority."""
import base64
import binascii
import json
from datetime import datetime, timezone
from decimal import Decimal, localcontext
from uuid import UUID, uuid4, uuid5
from sqlalchemy import insert, select, text
from sqlalchemy.exc import DBAPIError
from lzo.artifacts.service import ArtifactService, _permission, _scope
from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, validate_actor
from lzo.catalog.service import CatalogService
from lzo.contracts import ResourceScope, BlobRef
from lzo.errors import ApiError, Conflict
from lzo.governance.models import revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.risk import digest_change
from lzo.ingestion.models import sources
from lzo.ingestion.sources import SourceService, add_scoped, bind
from lzo.ontology.drafts import settings_for
from lzo.storage.blobs import get_blob_store
from lzo.storage.registry import StorageRegistry
from lzo.synchronization.contracts import SyncEnvelope
from lzo.synchronization.delivery import enqueue_delivery, delivery_status, DeliveryConflict
from lzo.telemetry.repository import batches, identities, TelemetryRepository
from lzo.telemetry.schemas import ObservationInput, BatchInput


def record_id(source_id, key, version):
    return uuid5(source_id, json.dumps([key, version], separators=(',', ':')))


def record_content(record):
    value = record.model_dump(mode='json')
    # Equivalent UTC offsets identify the same instant, without losing any of
    # the three distinct device/report/receipt clocks.
    for name in ('sampled_at', 'reported_at', 'end_at'):
        if value[name] is not None:
            value[name] = getattr(record, name).astimezone(timezone.utc).isoformat()
    if value['position'] is not None:
        value['position']['observed_at'] = record.position.observed_at.astimezone(timezone.utc).isoformat()
    return value


class TelemetryService:
    def __init__(self, uow, registry=None):
        self.uow, self.registry = uow, registry
        self.settings = settings_for(uow)

    def _object(self, actor, identifier, action='view'):
        _permission(self.uow, actor, identifier, action)
        if _scope(self.uow, identifier)['resource_kind'] != 'object':
            raise ApiError('NOT_FOUND', 404, 'Physical object not found')
        # Real ontology objects carry transitive visibility dependencies. Small
        # fixture/legacy objects may predate the semantic object table.
        from lzo.semantic.models import objects
        if self.uow.session.execute(select(objects.c.id).where(objects.c.id == identifier)).first():
            from lzo.semantic.facts import get_object_row
            get_object_row(self.uow, actor, identifier, action)

    def _pin(self, actor, ref):
        _permission(self.uow, actor, ref.resource_id, 'view')
        row = self.uow.session.execute(select(revisions).where(revisions.c.resource_id == ref.resource_id,
            revisions.c.revision == ref.revision, revisions.c.event_id == ref.event_id)).mappings().first()
        if row is None:
            raise ApiError('INVALID_REVISION_REFERENCE', 422, 'Exact version reference required')
        from lzo.governance.history import revision_visibility_dependencies
        for identifier in self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).where(
                revision_visibility_dependencies.c.event_id == ref.event_id)).scalars():
            _permission(self.uow, actor, identifier, 'view')
        return row

    def _references(self, actor, record, *, writing=False):
        self._object(actor, record.object_id, 'edit' if writing else 'view')
        self._object(actor, record.device_id)
        refs = {record.object_id, record.device_id}
        store = get_blob_store(self.settings)
        artifacts = ArtifactService(self.uow, store, self.settings)
        if record.artifact_version_id:
            row = artifacts._version(actor, record.artifact_version_id)
            refs.update((record.artifact_version_id, row['resource_id']))
            if writing:
                with store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])):
                    pass
        if record.evidence_version_ids:
            from lzo.ingestion.evidence import EvidenceService
            evidence = EvidenceService(self.uow, store, self.settings)
            for identifier in record.evidence_version_ids:
                item = evidence.read(actor, identifier)
                if writing:
                    artifacts.authorize_link(actor, identifier, record.object_id)
                if record.data_nature == 'verified' and item.review_state != 'accepted':
                    raise ApiError('TELEMETRY_EVIDENCE_REQUIRED', 422, 'Verified samples require accepted evidence')
                refs.update((identifier, artifacts._version(actor, identifier)['resource_id']))
        elif record.data_nature == 'verified':
            raise ApiError('TELEMETRY_EVIDENCE_REQUIRED', 422, 'Verified samples require controlled evidence')
        if record.location_ref:
            location = self._pin(actor, record.location_ref)
            if record.location_ref.resource_id != record.object_id or not location['payload'].get('geometry'):
                raise ApiError('TELEMETRY_LOCATION_REQUIRED', 422, 'Pinned object geometry required')
            refs.add(record.location_ref.resource_id)
        if record.device_binding_ref:
            binding = self._pin(actor, record.device_binding_ref)
            payload = binding['payload']
            if (binding['event_type'] != 'catalog.device.bound' or payload.get('entity_id') != str(record.device_id)
                    or payload.get('model_id') != str(record.model_ref.resource_id)):
                raise ApiError('TELEMETRY_BINDING_MISMATCH', 422, 'Historic physical device binding differs')
            next_time = self.uow.session.execute(select(revisions.c.recorded_at).where(
                revisions.c.resource_id == record.device_binding_ref.resource_id,
                revisions.c.revision > record.device_binding_ref.revision).order_by(revisions.c.revision).limit(1)).scalar_one_or_none()
            if binding['recorded_at'] > record.sampled_at or (next_time is not None and record.sampled_at >= next_time):
                raise ApiError('TELEMETRY_BINDING_TIME_MISMATCH', 422, 'Binding was not effective at capture')
            self._pin(actor, record.model_ref)
            CatalogService(self.uow, self.registry).get(actor, record.model_ref.resource_id,
                record.model_ref.revision, kind='model')
            refs.update((record.device_binding_ref.resource_id, record.model_ref.resource_id))
        return refs

    def accept(self, actor, command: BatchInput, key):
        validate_actor(self.uow, actor)
        if self.settings.storage_mode != 'split':
            raise Conflict('TELEMETRY_SPLIT_REQUIRED', 'Telemetry requires split storage')
        # Revalidate internal calls too; model_copy is deliberately not a trust boundary.
        command = BatchInput.model_validate(command.model_dump(mode='json'))
        serialized = command.model_dump(mode='json')
        if len(json.dumps(serialized).encode()) > 2_000_000:
            raise ApiError('TELEMETRY_BATCH_TOO_LARGE', 413, 'Batch exceeds byte budget')
        def accept():
            lock_ledger(self.uow)
            source = SourceService(self.uow).read(actor, command.source_id, 'source.manage')
            config = source.configuration
            mapping = command.mapping_version_id or source.mapping_version_id
            if mapping:
                if mapping != source.mapping_version_id:
                    raise Conflict('TELEMETRY_MAPPING_NOT_ACTIVE', 'Use the governed active mapping version')
                SourceService(self.uow).get_mapping(actor, source.id, mapping)
            refs = {source.id}
            if mapping:
                refs.add(mapping)
            if command.input_artifact_version_id:
                store = get_blob_store(self.settings)
                row = ArtifactService(self.uow, store, self.settings)._version(actor, command.input_artifact_version_id)
                if command.adapter == 'incremental-v1' or row['size_bytes'] > 2_000_000:
                    raise ApiError('TELEMETRY_PARSE_BINDING_MISMATCH', 422, 'Static origin requires a bounded registered format')
                from lzo.telemetry.replay import parse_records
                with store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])) as stream:
                    parsed = parse_records(stream.read(2_000_001), command.adapter[:-3])
                if [record_content(record) for record in parsed] != [record_content(record) for record in command.records]:
                    raise Conflict('TELEMETRY_PARSE_BINDING_MISMATCH', 'Normalized manifest differs from registered private bytes')
                refs.update((command.input_artifact_version_id, row['resource_id']))
            elif command.adapter != 'incremental-v1':
                raise ApiError('TELEMETRY_PARSE_BINDING_MISMATCH', 422, 'Static origin requires its registered artifact version')
            received = datetime.now(timezone.utc)
            rows, new_count = [], 0
            for record in command.records:
                if record.data_nature != config.data_nature:
                    raise ApiError('SOURCE_CONFIGURATION_MISMATCH', 422, 'Source and sample nature differ')
                refs.update(self._references(actor, record, writing=True))
                content = record_content(record)
                digest = digest_change(content)
                identity = dict(source_id=source.id, source_record_id=record.source_record_id, version=record.version)
                old = self.uow.session.execute(select(identities).where(*(identities.c[k] == v for k, v in identity.items()))).mappings().first()
                if old is not None and old['content_hash'] != digest:
                    raise Conflict('TELEMETRY_IDENTITY_CONFLICT', 'Source record/version already has different content')
                if record.version > 1:
                    previous = self.uow.session.execute(select(identities).where(identities.c.source_id == source.id,
                        identities.c.source_record_id == record.source_record_id, identities.c.version == record.version-1)).mappings().first()
                    if previous is None or previous['object_id'] != record.object_id:
                        raise Conflict('TELEMETRY_CORRECTION_REQUIRED', 'Correction requires accepted preceding object record')
                identifier = record_id(source.id, record.source_record_id, record.version)
                if old is None:
                    self.uow.session.execute(insert(identities).values(**identity, id=identifier,
                        object_id=record.object_id, content_hash=digest, received_at=received))
                    new_count += 1
                rows.append(content | {'id': str(identifier), 'content_hash': digest,
                    'received_at': (old['received_at'] if old else received).isoformat(),
                    'source_namespace': {'source_id': str(source.id), 'source_system': config.source_system,
                        'source_instance': config.source_instance, 'collection': config.collection},
                    'supersedes_id': str(record_id(source.id, record.source_record_id, record.version-1)) if record.version > 1 else None})
            identifier = uuid4()
            add_scoped(self.uow, actor, identifier, ResourceScope(None, config.scope.owner_scope_id,
                config.scope.related_scope_ids, config.scope.security_class), 'telemetry.batch', 'source.manage')
            manifest_hash = digest_change(serialized)
            # Only one ledger event per bounded manifest; raw samples are never
            # registered as individual ontology assertions/RDF projection jobs.
            ref = append_revision(self.uow, actor, identifier, 0, {'source_id': str(source.id),
                'manifest_hash': manifest_hash, 'count': len(rows), 'accepted_count': new_count,
                'adapter': command.adapter, 'input_artifact_version_id': str(command.input_artifact_version_id) if command.input_artifact_version_id else None},
                'telemetry.batch.accepted')
            bind(self.uow, ref, refs)
            self.uow.session.execute(insert(batches).values(id=identifier, source_id=source.id, manifest_hash=manifest_hash, event_id=ref.event_id))
            enqueue_delivery(self.uow, SyncEnvelope(event_id=ref.event_id, entity_id=identifier, aggregate_revision=1,
                target='timeseries', operation='telemetry.batch', payload={'source_id': str(source.id),
                    'manifest_hash': manifest_hash, 'records': rows,
                    'input_artifact_version_id': str(command.input_artifact_version_id) if command.input_artifact_version_id else None,
                    'mapping_version_id': str(mapping) if mapping else None}, data_nature=config.data_nature,
                source_refs=tuple(sorted(map(str, refs))), mapping_version=str(mapping) if mapping else None))
            return {'id': str(identifier), 'event_id': str(ref.event_id), 'accepted_count': new_count,
                'record_count': len(rows), 'status': 'pending'}
        result = execute_idempotent(self.uow, actor, key, digest_change({'operation': 'telemetry.accept', 'command': serialized}), accept)
        self._batch(actor, UUID(result['id']))
        return result

    def _batch(self, actor, identifier):
        _permission(self.uow, actor, identifier, 'view')
        row = self.uow.session.execute(select(batches).where(batches.c.id == identifier)).mappings().first()
        if row is None:
            raise ApiError('NOT_FOUND', 404, 'Batch not found')
        SourceService(self.uow).read(actor, row['source_id'])
        from lzo.governance.history import revision_visibility_dependencies
        for dependency in self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).where(
                revision_visibility_dependencies.c.event_id == row['event_id'])).scalars():
            _permission(self.uow, actor, dependency, 'view')
        return row

    def status(self, actor, identifier):
        row = self._batch(actor, identifier)
        receipt = delivery_status(self.uow, row['event_id'])[0]
        status = 'current' if receipt.status in ('applied', 'superseded') else 'failed' if receipt.status == 'failed' else 'pending'
        registry = self.registry or StorageRegistry.from_settings(self.settings)
        if status == 'current':
            try:
                with registry.engine('timeseries').connect() as c:
                    actual = c.execute(text('SELECT payload_hash FROM telemetry_batch_receipts WHERE batch_id=:id'), {'id': identifier}).scalar_one_or_none()
                if actual != receipt.payload_hash:
                    status = 'pending'
            except DBAPIError:
                status = 'unavailable'
            finally:
                if self.registry is None:
                    registry.close()
        elif self.registry is None:
            registry.close()
        return {'id': str(identifier), 'event_id': str(row['event_id']), 'status': status,
            'delivery_status': receipt.status, 'attempts': receipt.attempts, 'error': receipt.error}

    def reference_dependencies(self, actor, ref):
        """Exact acceptance provenance, independent of a later retry audit head.

        Pending target delivery is allowed at semantic command acceptance; the
        existing projection barrier waits on this exact event before publication.
        """
        row = self._batch(actor, ref.resource_id)
        if ref.revision != 1 or row['event_id'] != ref.event_id:
            raise ApiError('INVALID_REVISION_REFERENCE', 422, 'Exact telemetry acceptance reference required')
        from lzo.governance.history import revision_visibility_dependencies
        return set(self.uow.session.execute(select(revision_visibility_dependencies.c.resource_id).where(
            revision_visibility_dependencies.c.event_id == ref.event_id)).scalars())

    def retry(self, actor, identifier, key=None):
        row = self._batch(actor, identifier)
        _permission(self.uow, actor, identifier, 'source.manage')
        SourceService(self.uow).read(actor, row['source_id'], 'source.manage')
        return execute_idempotent(self.uow, actor, key or str(uuid4()),
            digest_change({'operation': 'telemetry.retry', 'id': str(identifier)}), lambda: self._retry(actor, identifier))

    def _retry(self, actor, identifier):
        row = self._batch(actor, identifier)
        _permission(self.uow, actor, identifier, 'source.manage')
        SourceService(self.uow).read(actor, row['source_id'], 'source.manage')
        lock_ledger(self.uow)
        stored = self.uow.session.execute(text("SELECT * FROM store_deliveries WHERE event_id=:id AND target='timeseries' FOR UPDATE"),
            {'id': row['event_id']}).mappings().one()
        envelope = SyncEnvelope.model_validate(stored['envelope'])
        if (envelope.envelope_hash != stored['envelope_hash'] or envelope.entity_id != identifier
                or envelope.event_id != row['event_id'] or envelope.aggregate_revision != 1
                or envelope.operation != 'telemetry.batch' or envelope.payload_hash != stored['payload_hash']):
            raise DeliveryConflict('Stored batch envelope differs from acceptance')
        for value in envelope.payload['records']:
            self._references(actor, self._input(value), writing=True)
        if stored['status'] == 'failed':
            self.uow.session.execute(text("UPDATE store_deliveries SET status='retry',attempts=0,error=NULL,available_at=clock_timestamp() WHERE event_id=:id AND target='timeseries'"), {'id': row['event_id']})
            append_revision(self.uow, actor, identifier,
                self.uow.session.execute(select(revisions.c.revision).where(revisions.c.resource_id == identifier).order_by(revisions.c.revision.desc()).limit(1)).scalar_one(),
                {'event_id': str(row['event_id']), 'envelope_hash': envelope.envelope_hash,
                    'previous_attempts': stored['attempts'], 'previous_failure': stored['error']}, 'telemetry.delivery.retried')
        return {'id': str(identifier), 'status': 'pending'}

    @staticmethod
    def _input(row):
        return ObservationInput.model_validate({key: value for key, value in row.items() if key in ObservationInput.model_fields})

    def query(self, actor, *, kind='observation', object_id=None, start=None, end=None, cursor=None,
              limit=50, history=False, latest=False, device_id=None, channel_id=None, mission_id=None, metric_id=None):
        validate_actor(self.uow, actor)
        if kind not in ('observation', 'event', 'media', 'track') or type(limit) is not int or not 1 <= limit <= min(1000, self.settings.list_max_page_size):
            raise ApiError('INVALID_FILTER', 422, 'Invalid telemetry query')
        if ((start and start.utcoffset() is None) or (end and end.utcoffset() is None) or (start and end and start >= end)):
            raise ApiError('INVALID_FILTER', 422, 'Timezone-aware increasing interval required')
        if latest and (history or cursor):
            raise ApiError('INVALID_FILTER', 422, 'Latest query cannot combine history or cursor')
        if object_id:
            self._object(actor, object_id)
        if device_id:
            self._object(actor, device_id)
        filters = {key: str(value) for key, value in dict(device_id=device_id, channel_id=channel_id,
            mission_id=mission_id, metric_id=metric_id).items() if value is not None}
        if any(not value or len(value) > 256 for value in filters.values()):
            raise ApiError('INVALID_FILTER', 422, 'Invalid telemetry selector')
        objects = list(self.uow.session.execute(select(resource_scopes.c.resource_id).where(
            resource_scopes.c.resource_kind == 'object', resource_scopes.c.resource_id.in_(authorized_statement(actor, 'view')))).scalars())
        if object_id:
            objects = [object_id]
        source_ids = list(self.uow.session.execute(select(sources.c.id).where(sources.c.id.in_(authorized_statement(actor, 'source.read')),
            sources.c.id.in_(authorized_statement(actor, 'view')))).scalars())
        permitted_sources = []
        for identifier in source_ids:
            try:
                SourceService(self.uow).read(actor, identifier)
                permitted_sources.append(identifier)
            except ApiError as error:
                if error.status not in (403, 404):
                    raise
        source_ids = permitted_sources
        context = {'actor': str(actor.subject_id), 'authorization': actor.authorization_version, 'kind': kind,
            'object': str(object_id) if object_id else None, 'start': str(start), 'end': str(end), 'history': history, 'filters': filters}
        after = None
        if cursor:
            try:
                if len(cursor) > 4096:
                    raise ValueError()
                value = json.loads(base64.b64decode(cursor + '=' * (-len(cursor) % 4), altchars=b'-_', validate=True))
                if value['context'] != context:
                    raise Conflict('TELEMETRY_CURSOR_STALE', 'Query or authorization changed')
                after = (datetime.fromisoformat(value['after'][0]), UUID(value['after'][1]))
                if after[0].utcoffset() is None:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, binascii.Error):
                raise ApiError('INVALID_CURSOR', 422, 'Invalid telemetry cursor') from None
        # Frozen core envelopes authenticate candidates and pending watermarks.
        wires = self.uow.session.execute(text("SELECT envelope,status FROM store_deliveries WHERE target='timeseries' AND entity_id IN (SELECT id FROM telemetry_batches)")).mappings()
        accepted, states, watermark, superseded = {}, [], None, set()
        for wire in wires:
            envelope = SyncEnvelope.model_validate(wire['envelope'])
            payload = envelope.payload
            if UUID(payload['source_id']) not in source_ids:
                continue
            # Core acceptance already invalidated the preceding source version.
            # A delayed/failed withdrawal must never resurrect its old latest
            # value while the separate target projection catches up.
            superseded.update(row['supersedes_id'] for row in payload['records'] if row.get('supersedes_id'))
            try:
                if payload.get('mapping_version_id'):
                    SourceService(self.uow).get_mapping(actor, UUID(payload['source_id']), UUID(payload['mapping_version_id']))
                if payload.get('input_artifact_version_id'):
                    ArtifactService(self.uow, get_blob_store(self.settings), self.settings)._version(actor,
                        UUID(payload['input_artifact_version_id']))
            except ApiError as error:
                if error.status in (403, 404):
                    continue
                raise
            records = {}
            for row in payload['records']:
                if UUID(row['object_id']) not in objects or row['kind'] != kind:
                    continue
                record = self._input(row)
                if (start and ((record.end_at <= start) if kind == 'media' else record.sampled_at < start)) or (end and record.sampled_at >= end):
                    continue
                if any(str(row.get(key)) != value for key, value in filters.items()):
                    continue
                try:
                    self._references(actor, record)
                except ApiError as error:
                    if error.status in (403, 404):
                        continue
                    raise
                records[row['id']] = row
            if records:
                state = wire['status']
                states.append('current' if state in ('applied', 'superseded') else 'failed' if state == 'failed' else 'pending')
                if state in ('applied', 'superseded'):
                    accepted[str(envelope.event_id)] = records
                    recorded = envelope.recorded_at.isoformat()
                    watermark = max(watermark or recorded, recorded)
        status = next((state for state in ('failed', 'pending') if state in states), 'current')
        registry = self.registry or StorageRegistry.from_settings(self.settings)
        output, scanned = [], 0
        try:
            repository = TelemetryRepository(registry)
            while True:
                candidates = repository.page(objects, source_ids, kind=kind, start=start, end=end,
                    after=after, limit=1000, history=history, filters=filters)
                for candidate in candidates:
                    row = candidate['payload']
                    after = (datetime.fromisoformat(row['sampled_at']), UUID(row['id']))
                    scanned += 1
                    if scanned > 10000:
                        raise ApiError('TELEMETRY_QUERY_BUDGET', 422, 'Narrow the interval before querying more samples',
                            {'suggested_start': output[-1]['sampled_at'] if output else None})
                    if accepted.get(str(candidate['event_id']), {}).get(row['id']) != row:
                        continue
                    if not history and row['id'] in superseded:
                        continue
                    if latest and (row['quality'] != 'valid' or row['normalized_value'] is None):
                        continue
                    output.append(row)
                    if not latest and len(output) > limit:
                        break
                if (not latest and len(output) > limit) or len(candidates) < 1000:
                    break
        except DBAPIError:
            return {'status': 'unavailable', 'items': [], 'next_cursor': None, 'watermark': watermark,
                'aggregate': self._aggregate([]), 'aggregate_scope': 'page'}
        finally:
            if self.registry is None:
                registry.close()
        if latest:
            grouped = {}
            for row in output:
                grouped[(row['source_namespace']['source_id'], row['object_id'], row['device_id'], row['point_id'], row['metric_id'])] = row
            output = sorted(grouped.values(), key=lambda row: (row['sampled_at'], row['id']))
            if len(output) > limit:
                raise ApiError('TELEMETRY_QUERY_BUDGET', 422, 'Narrow latest query to fewer series',
                    {'suggested_start': output[-1]['sampled_at']})
        next_cursor = None
        if len(output) > limit:
            last = output[limit-1]
            next_cursor = base64.urlsafe_b64encode(json.dumps({'context': context,
                'after': [last['sampled_at'], last['id']]}).encode()).decode().rstrip('=')
        output = output[:limit]
        return {'status': status, 'items': output, 'next_cursor': next_cursor, 'watermark': watermark,
            'aggregate': self._aggregate(output), 'aggregate_scope': 'page'}

    @staticmethod
    def _aggregate(rows):
        numbers = [Decimal(row['normalized_value']) for row in rows if row['normalized_value'] is not None]
        with localcontext() as context:
            context.prec = 140
            return {'count': len(rows), 'numeric_count': len(numbers), 'min': str(min(numbers)) if numbers else None,
                'max': str(max(numbers)) if numbers else None, 'sum': str(sum(numbers, Decimal(0))) if numbers else None}

    def retention_preview(self, actor, object_id, before):
        if before.utcoffset() is None:
            raise ApiError('INVALID_FILTER', 422, 'Timezone-aware cutoff required')
        result = self.query(actor, object_id=object_id, end=before, history=True,
            limit=min(1000, self.settings.list_max_page_size))
        return {'status': result['status'], 'policy': 'retain-history', 'automatic_deletion': False,
            'visible_page_count': len(result['items']), 'more': result['next_cursor'] is not None,
            'evidence_action': 'retain-all-referenced-versions'}


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('telemetry.batch.accepted', 'telemetry-v1')
register_nonsemantic('telemetry.delivery.retried', 'telemetry-v1')
