"""Bounded non-destructive archives; immutable bytes, current dependency rights."""
import io
import json
from uuid import UUID, uuid4
from sqlalchemy import select, text, update
from lzo.artifacts.service import ArtifactService, _scope
from lzo.auth.models import resource_scopes
from lzo.contracts import BlobRef, ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.models import revisions
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.risk import digest_change
from lzo.ingestion.sources import SourceService, add_scoped, bind
from lzo.storage.blobs import get_blob_store
from lzo.synchronization.contracts import SyncEnvelope
from lzo.telemetry.schemas import ArchiveInput


def authorize_archive(uow, actor, identifier):
    """Also used by generic artifact/history paths before exposing capture data."""
    row = uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id == identifier,
        revisions.c.event_type == 'telemetry.archive.created')).scalar_one_or_none()
    if row is None:
        raise ApiError('NOT_FOUND', 404, 'Archive not found')
    from lzo.telemetry.service import TelemetryService
    svc = TelemetryService(uow)
    selector = row['selector']
    svc._object(actor, UUID(selector['object_id']))
    SourceService(uow).read(actor, UUID(selector['source_id']))
    records, events = {}, {}
    selected = set(row['record_hashes'])
    expected_events = {UUID(value) for values in row['accepted_events'].values() for value in values}
    for wire in uow.session.execute(text("SELECT envelope FROM store_deliveries WHERE target='timeseries' AND event_id=ANY(:events)"),
            {'events': list(expected_events)}).scalars():
        envelope = SyncEnvelope.model_validate(wire)
        if envelope.payload['source_id'] != selector['source_id']:
            continue
        for record in envelope.payload['records']:
            if (record['id'] in selected and str(envelope.event_id) in row['accepted_events'][record['id']]
                    and record['content_hash'] == row['record_hashes'][record['id']]):
                svc._references(actor, svc._input(record))
                if envelope.payload.get('mapping_version_id'):
                    SourceService(uow).get_mapping(actor, UUID(selector['source_id']), UUID(envelope.payload['mapping_version_id']))
                if envelope.payload.get('input_artifact_version_id'):
                    ArtifactService(uow, get_blob_store(svc.settings), svc.settings)._version(actor, UUID(envelope.payload['input_artifact_version_id']))
                records[record['id']] = record
                events.setdefault(record['id'], []).append(str(envelope.event_id))
    if set(records) != selected:
        raise Conflict('TELEMETRY_ARCHIVE_REFERENCE_MISSING', 'Exact retained acceptance is missing')
    return row, records, events


class ArchiveService:
    def __init__(self, telemetry):
        self.telemetry, self.uow, self.settings = telemetry, telemetry.uow, telemetry.settings

    def _selection(self, actor, command):
        """Check all accepted selected references before visibility can hide rows."""
        query = """SELECT d.event_id,d.status,r.value AS record,
            d.envelope->'payload'->>'mapping_version_id' AS mapping_version_id,
            d.envelope->'payload'->>'input_artifact_version_id' AS input_artifact_version_id
            FROM store_deliveries d,
            LATERAL jsonb_array_elements(d.envelope->'payload'->'records') r(value)
            WHERE d.target='timeseries' AND d.envelope->'payload'->>'source_id'=:source
            AND r.value->>'object_id'=:object AND r.value->>'kind'=:kind
            AND CAST(r.value->>'sampled_at' AS timestamptz)<:before """
        params = {'source': str(command.source_id), 'object': str(command.object_id), 'kind': command.kind, 'before': command.before}
        if command.start is not None:
            query += ("AND CAST(r.value->>'end_at' AS timestamptz)>:start " if command.kind == 'media'
                else "AND CAST(r.value->>'sampled_at' AS timestamptz)>=:start ")
            params['start'] = command.start
        if command.quality is not None:
            query += "AND r.value->>'quality'=:quality "; params['quality'] = command.quality
        if command.record_ids is not None:
            query += "AND r.value->>'id'=ANY(:ids) "; params['ids'] = list(map(str, command.record_ids))
        rows = self.uow.session.execute(text(query + 'ORDER BY d.event_id LIMIT 10001'), params).mappings()
        records, events, refs = {}, {}, {command.object_id, command.source_id}
        scanned, scanned_bytes = 0, 0
        for row in rows:
            record = row['record']
            scanned += 1
            scanned_bytes += len(json.dumps(record, ensure_ascii=False).encode())
            if scanned > 10000 or scanned_bytes > 2_000_000:
                raise ApiError('TELEMETRY_ARCHIVE_TOO_LARGE', 422, 'Narrow the selection; accepted-reference scan budget exceeded')
            refs.update(self.telemetry._references(actor, self.telemetry._input(record)))
            if row['mapping_version_id']:
                mapping = UUID(row['mapping_version_id'])
                SourceService(self.uow).get_mapping(actor, command.source_id, mapping)
                refs.add(mapping)
            if row['input_artifact_version_id']:
                artifact = UUID(row['input_artifact_version_id'])
                version = ArtifactService(self.uow, get_blob_store(self.settings), self.settings)._version(actor, artifact)
                refs.update((artifact, version['resource_id']))
            if row['status'] not in ('applied', 'superseded'):
                raise Conflict('TELEMETRY_ARCHIVE_INCOMPLETE', 'Only fully acknowledged selections can be archived')
            records[record['id']] = record
            events.setdefault(record['id'], [str(row['event_id'])])
        return records, events, refs

    def create(self, actor, command, key):
        command = ArchiveInput.model_validate(command.model_dump(mode='json'))
        selector = command.model_dump(mode='json')
        def capture():
            lock_ledger(self.uow)
            self.telemetry._object(actor, command.object_id, 'edit')
            source = SourceService(self.uow).read(actor, command.source_id)
            expected, events, refs = self._selection(actor, command)
            if not expected:
                raise ApiError('TELEMETRY_ARCHIVE_SELECTION_MISSING', 422, 'No exact records match the selected scope')
            if len(expected) > min(1000, self.settings.list_max_page_size):
                raise ApiError('TELEMETRY_ARCHIVE_TOO_LARGE', 422, 'Narrow the interval or supply an explicit record subset')
            result = self.telemetry.query(actor, object_id=command.object_id, source_id=command.source_id,
                start=command.start, end=command.before, kind=command.kind, quality=command.quality,
                history=True, record_ids=tuple(map(UUID, expected)), limit=min(1000, self.settings.list_max_page_size))
            if result['status'] != 'current':
                raise Conflict('TELEMETRY_ARCHIVE_INCOMPLETE', 'Only fully acknowledged selections can be archived')
            if result['next_cursor']:
                raise ApiError('TELEMETRY_ARCHIVE_TOO_LARGE', 422, 'Narrow the interval or supply an explicit record subset')
            records = result['items']
            if (not records or {r['id']: r for r in records} != expected
                    or (command.record_ids is not None and {UUID(r['id']) for r in records} != set(command.record_ids))):
                raise ApiError('TELEMETRY_ARCHIVE_SELECTION_MISSING', 422, 'All selected exact records must be visible and captured')
            identifier = uuid4()
            scope = _scope(self.uow, command.object_id)
            add_scoped(self.uow, actor, identifier, ResourceScope(None, scope['owner_scope_id'],
                tuple(map(UUID, scope['related_scope_ids'])), scope['security_class']), 'telemetry.archive.pending', 'edit')
            audit = {'archive_version': 1, 'selector': selector, 'record_hashes': {r['id']: r['content_hash'] for r in records},
                'accepted_events': events,
                'source_scope': source.configuration.model_dump(mode='json')['scope'],
                'policy': 'retain-history', 'automatic_deletion': False, 'complete_selection': command.record_ids is None}
            ref = append_revision(self.uow, actor, identifier, 0, audit, 'telemetry.archive.created')
            bind(self.uow, ref, refs)
            authorize_archive(self.uow, actor, identifier)
            manifest = audit | {'records': records}
            payload = json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
            # Explicit bytes bound independent of large deployment upload allowances.
            if len(payload) > min(2_000_000, self.settings.artifact_max_upload_bytes):
                raise ApiError('TELEMETRY_ARCHIVE_TOO_LARGE', 413, 'Narrow the interval or supply an explicit record subset')
            store = get_blob_store(self.settings)
            # Verify every referenced immutable artifact, with no provider fallback.
            artifacts = ArtifactService(self.uow, store, self.settings)
            for dependency in refs:
                from lzo.artifacts.models import artifact_versions
                row = self.uow.session.execute(select(artifact_versions).where(artifact_versions.c.id == dependency)).mappings().first()
                if row is not None:
                    artifacts._version(actor, dependency)
                    with store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])):
                        pass
            blob = store.put(io.BytesIO(payload), 'application/json')
            detail = artifacts.register_upload(actor, identifier, blob, filename='telemetry-archive-v1.json', metadata={
                'archive_version': 1, 'manifest_sha256': blob.sha256, 'non_destructive': True})
            self.uow.session.execute(update(resource_scopes).where(resource_scopes.c.resource_id == identifier).values(resource_kind='telemetry.archive'))
            return {'id': str(identifier), 'artifact_version_id': str(detail.version_id), 'sha256': detail.sha256,
                'record_count': len(records), 'policy': 'retain-history', 'automatic_deletion': False}
        result = execute_idempotent(self.uow, actor, key, digest_change({'operation': 'telemetry.archive', 'selector': selector}), capture)
        authorize_archive(self.uow, actor, UUID(result['id']))
        return result

    def read(self, actor, identifier):
        from lzo.artifacts.models import artifact_versions
        from lzo.artifacts.service import _permission
        _permission(self.uow, actor, identifier, 'view')
        authorize_archive(self.uow, actor, identifier)
        version = self.uow.session.execute(select(artifact_versions.c.id).where(artifact_versions.c.resource_id == identifier)).scalar_one()
        store = get_blob_store(self.settings)
        row = ArtifactService(self.uow, store, self.settings)._version(actor, version)
        with store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])) as stream:
            return json.loads(stream.read())


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('telemetry.archive.created', 'telemetry-archive-v1')
