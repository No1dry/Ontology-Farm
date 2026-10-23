"""Bounded retained captures on actual core/domain stores and private S3."""
from uuid import UUID, uuid4
from datetime import datetime, timezone
import pytest
from sqlalchemy import text, update, insert
from test_timeseries import telemetry_case


def owned_s3_settings(settings):
    from pathlib import Path
    from dotenv import dotenv_values
    values = dotenv_values(Path(__file__).resolve().parents[3]/'.runtime/object-store/services.env')
    return type(settings).model_validate(settings.model_dump() | {
        **{key[4:].lower(): value for key, value in values.items() if key.startswith('LZO_S3_')},
        'blob_provider': 's3', 'blob_staging_path': settings.file_root/'staging',
        's3_key_prefix': 'whole-fix/'+uuid4().hex})


def test_archive_retains_exact_history_and_enforces_current_dependencies(telemetry_case):
    from lzo.telemetry.archive import ArchiveService
    from lzo.telemetry.schemas import ArchiveInput
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.artifacts.service import ArtifactService
    from lzo.governance.history import HistoryService
    from lzo.storage.blobs import get_blob_store
    from lzo.auth.models import roles, authorization_state
    from lzo.errors import ApiError
    s = telemetry_case
    s.config = owned_s3_settings(s.config)
    assert get_blob_store(s.config).__class__.__name__ == 'S3BlobStore'
    s.accept([s.observation('archive-a'), s.observation('archive-b', quality='suspect')])
    command = ArchiveInput(object_id=s.ids_by_name['object_a'], source_id=s.source.id,
        before=datetime(2026, 2, 1, tzinfo=timezone.utc), kind='observation', quality='valid')
    with s.transaction() as uow:
        svc = ArchiveService(s.telemetry(uow))
        with pytest.raises(ApiError, match='TELEMETRY_ARCHIVE_INCOMPLETE'):
            svc.create(s.actor('alice'), command, 'pending')
    poll_deliveries(s.registry)
    # Object edit and source view without source.read must never suffice.
    with s.transaction() as uow:
        role=uuid4();s.roles['archive-without-source']=role
        uow.session.execute(insert(roles).values(id=role,name='archive-without-source',actions=['view','edit'],revision=1))
        uow.commit()
    s.grant('nobody','archive-without-source',scope='farm_a')
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            ArchiveService(s.telemetry(uow)).create(s.actor('nobody'),command,'no-source-right')
    baseline = s.query(history=True)
    with s.transaction() as uow:
        svc = ArchiveService(s.telemetry(uow))
        captured = svc.create(s.actor('alice'), command, 'capture')
        assert svc.create(s.actor('alice'), command, 'capture') == captured
        uow.commit()
    with s.transaction() as uow:
        svc = ArchiveService(s.telemetry(uow))
        manifest = svc.read(s.actor('alice'), UUID(captured['id']))
        assert manifest['selector']['quality'] == 'valid'
        assert manifest['source_scope']['owner_scope_id'] == str(s.scopes['farm_a'])
        assert manifest['complete_selection'] is True
        assert len(manifest['records']) == 1 and manifest['records'][0] == baseline['items'][0 if baseline['items'][0]['quality']=='valid' else 1]
        assert manifest['accepted_events'][manifest['records'][0]['id']]
        assert ArtifactService(uow, get_blob_store(s.config), s.config).get(s.actor('alice'), UUID(captured['artifact_version_id']))
    assert s.query(history=True) == baseline
    from professional_http import scenario_client
    with scenario_client(s) as client:
        public=client.post('/api/v1/telemetry/archives',headers={'Idempotency-Key':'http-capture'},json=command.model_dump(mode='json'))
        assert public.status_code==201,public.text
        public_manifest=client.get('/api/v1/telemetry/archives/'+public.json()['id'])
        assert public_manifest.status_code==200 and public_manifest.json()['records']==manifest['records'],public_manifest.text
        assert client.post('/api/v1/telemetry/archives',headers={'Idempotency-Key':'invalid-kind'},json={
            **command.model_dump(mode='json'),'kind':'invented'}).status_code==422
    previous_bytes=s.config
    s.config=s.config.model_copy(update={'artifact_max_upload_bytes':200})
    with s.transaction() as uow:
        with pytest.raises(ApiError,match='TELEMETRY_ARCHIVE_TOO_LARGE'):
            ArchiveService(s.telemetry(uow)).create(s.actor('alice'),command,'byte-budget')
    s.config=previous_bytes
    # Refuse a complete query beyond the declared synchronous record bound.
    previous = s.config
    s.config = s.config.model_copy(update={'list_max_page_size': 1})
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='TELEMETRY_ARCHIVE_TOO_LARGE'):
            ArchiveService(s.telemetry(uow)).create(s.actor('alice'), command.model_copy(update={'quality': None}), 'too-large')
        selected = ArchiveService(s.telemetry(uow)).create(s.actor('alice'), command.model_copy(update={
            'quality': None, 'record_ids': (UUID(baseline['items'][0]['id']),)}), 'subset')
        uow.commit()
    s.config = previous
    # Current source/object access alone cannot authorize a partial capture
    # when one selected accepted record has a hidden artifact dependency.
    import io
    grant=s.grant('alice','editor',scope='farm_b')
    with s.transaction() as uow:
        store=get_blob_store(s.config)
        hidden=ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_b'],
            store.put(io.BytesIO(b'private selected dependency'),'text/plain'),filename='hidden.txt',metadata={'synthetic':True})
        uow.commit()
    s.accept([s.observation('hidden-dependency',artifact_version_id=hidden.version_id)])
    poll_deliveries(s.registry)
    s.revoke(grant)
    with s.transaction() as uow:
        with pytest.raises(ApiError) as denied:
            ArchiveService(s.telemetry(uow)).create(s.actor('alice'),command,'hidden-dependency')
        assert denied.value.status in (403,404)
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=['view', 'edit', 'source.manage']))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1))
        uow.commit()
    with s.transaction() as uow:
        svc = ArchiveService(s.telemetry(uow))
        for read in (lambda: svc.read(s.actor('alice'), UUID(captured['id'])),
                     lambda: ArtifactService(uow, get_blob_store(s.config), s.config).get(s.actor('alice'), UUID(captured['artifact_version_id'])),
                     lambda: HistoryService(uow).get(s.actor('alice'), UUID(captured['id']), 1)):
            with pytest.raises(ApiError): read()
    with scenario_client(s) as client:
        for path in ('/api/v1/telemetry/archives/'+captured['id'],
                     '/api/v1/artifacts/'+captured['artifact_version_id'],
                     '/api/v1/history/'+captured['id']+'/revisions/1'):
            assert client.get(path).status_code in (403,404)
