"""Real coordinated backups and reads from fresh owned restore destinations."""
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

from test_multistore_migration import migration_case, stopped_writer_markers


@pytest.fixture
def recovery_case(migration_case, stopped_writer_markers, tmp_path):
    from lzo.storage.backup import BackupService
    from lzo.storage.maintenance import MaintenanceWindow
    s = migration_case
    s.migration.backfill(s.plan, s.actor('alice'))
    s.migration.reconcile(s.plan, s.actor('alice'))
    root = Path(__file__).resolve().parents[3]
    owner = json.loads((root / '.runtime/multistore/ownership.json').read_text())
    assert Path(owner['worktree']).resolve() == root
    admin = json.loads((root / '.runtime/multistore/admin.json').read_text())
    s.admin_engine = create_engine(URL.create('postgresql+psycopg', username='postgres',
        password=admin['password'], host='127.0.0.1', port=owner['port'], database='postgres'))
    s.pg_bin = Path(owner['bin'])
    s.extra_owners = {domain: create_engine(getattr(s.target, domain + '_migration_database_url'))
        for domain in ('timeseries', 'catalog')}
    s.authority = tmp_path / 'backup-authority-key'
    s.backup_service = BackupService(s.target, s.pg_bin, s.authority)
    s.destination = tmp_path / 'backup'
    actor = s.actor('alice')
    s.app.dispose()
    with MaintenanceWindow(s.owner, s.target_owner, stopped_writer_markers, tmp_path,
            additional=s.extra_owners) as window:
        s.backup_manifest = s.backup_service.create(s.destination, window, actor, migration_plan=s.plan)
    yield s
    s.admin_engine.dispose()
    for engine in s.extra_owners.values():
        engine.dispose()


def test_backup_verifies_real_material_and_rejects_a_missing_referenced_part(recovery_case, stopped_writer_markers, tmp_path):
    from lzo.storage.backup import verify_backup_material, BackupError
    s = recovery_case
    report = verify_backup_material(s.backup_manifest, s.authority, s.pg_bin)
    assert report['status'] == 'verified'
    assert report['references']['blob_versions'] == 5
    assert report['references']['upload_parts'] == 1
    assert report['references']['gis_versions'] == 2
    assert report['references']['rdf_heads'] > 0
    manifest = json.loads(s.backup_manifest.read_text())
    blob = s.destination / manifest['blob_files'][0]['path']
    original = blob.read_bytes()
    blob.write_bytes(b'corrupt retained target bytes')
    with pytest.raises(BackupError, match='CHECKSUM_MISMATCH'):
        verify_backup_material(s.backup_manifest, s.authority, s.pg_bin)
    blob.write_bytes(original)
    assert verify_backup_material(s.backup_manifest, s.authority, s.pg_bin)['status'] == 'verified'
    # A real target receipt corruption must prevent the producer from issuing
    # another authoritative complete snapshot, even though dump creation works.
    with s.target_owner.begin() as c:
        row = c.execute(text('SELECT event_id,envelope_hash FROM store_inbox LIMIT 1')).mappings().one()
        c.execute(text('UPDATE store_inbox SET envelope_hash=:bad WHERE event_id=:id'), {'bad': '0' * 64, 'id': row['event_id']})
    try:
        with pytest.raises(BackupError, match='DOMAIN_RECEIPT_REFERENCE_MISSING'):
            _backup(s, stopped_writer_markers, tmp_path, 'refused-reference-backup')
        assert json.loads((tmp_path / 'refused-reference-backup/state.json').read_text())['state'] == 'failed'
    finally:
        with s.target_owner.begin() as c:
            c.execute(text('UPDATE store_inbox SET envelope_hash=:digest WHERE event_id=:id'), {'digest': row['envelope_hash'], 'id': row['event_id']})


def test_restore_is_new_reference_complete_and_readable_without_touching_source(recovery_case, tmp_path, monkeypatch):
    from lzo.storage.restore import RestoreService, load_restored_settings
    from lzo.storage.backup import BackupError
    from lzo.storage.registry import StorageRegistry
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.storage.blobs import get_blob_store
    from lzo.contracts import BlobRef
    from lzo.main import create_app
    from fastapi.testclient import TestClient
    s = recovery_case
    namespace = 'lzo_restore_' + uuid4().hex[:16]
    restore = RestoreService(s.admin_engine, s.pg_bin, s.authority)
    original = restore._restore_database
    def interrupted(*args):
        original(*args)
        raise RuntimeError('owned restore interruption after a real database restore')
    monkeypatch.setattr(restore, '_restore_database', interrupted)
    with pytest.raises(RuntimeError, match='owned restore interruption'):
        restore.restore(s.backup_manifest, namespace, tmp_path / 'interrupted')
    assert json.loads((tmp_path / 'interrupted/ownership.json').read_text())['state'] == 'failed'
    assert not (tmp_path / 'interrupted/configuration.json').exists()
    monkeypatch.setattr(restore, '_restore_database', original)
    with pytest.raises(BackupError, match='TARGET_EXISTS'):
        restore.restore(s.backup_manifest, namespace, tmp_path / 'cannot-reuse')
    namespace = 'lzo_restore_' + uuid4().hex[:16]
    result = restore.restore(s.backup_manifest, namespace, tmp_path / 'restored')
    settings = load_restored_settings(result['configuration'])
    assert settings.database_url != s.target.database_url
    assert settings.s3_key_prefix != s.target.s3_key_prefix
    assert settings.fuseki_url != s.target.fuseki_url
    with pytest.raises(BackupError, match='TARGET_EXISTS'):
        restore.restore(s.backup_manifest, namespace, tmp_path / 'other-destination')
    registry = StorageRegistry.from_settings(settings)
    try:
        with registry.engine('core').connect() as c:
            assert c.execute(text('SELECT iri FROM semantic_objects WHERE id=:id'), {'id': s.identifier}).scalar_one() == 'urn:lzo:object:' + str(s.identifier)
            assert c.execute(text('SELECT count(*) FROM revisions WHERE resource_id=:id'), {'id': s.identifier}).scalar_one() == 2
            assert c.execute(text('''SELECT count(*) FROM revisions r JOIN revision_metadata m USING(event_id)
                WHERE r.resource_id=:id AND r.event_type='evidence.attached'
                AND jsonb_array_length(m.evidence_refs)=1'''), {'id': s.ids_by_name['object_a']}).scalar_one() == 2
            assert c.execute(text("SELECT has_table_privilege(current_user,'revisions','UPDATE')")).scalar_one() is False
            rows = list(c.execute(text('SELECT * FROM artifact_versions')).mappings())
            heads = list(c.execute(text('SELECT * FROM projection_heads')).mappings())
            upload = c.execute(text('SELECT state,parts FROM artifact_upload_sessions WHERE id=:id'), {'id': s.upload_session}).mappings().one()
            assert upload['state'] == 'active' and set(upload['parts']) == {'1'}
        for row in rows:
            with get_blob_store(settings).open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])):
                pass
        rdf = FusekiRepository(settings.fuseki_url)
        for head in heads:
            current = rdf.current_revision(head['resource_id'])
            assert current['revision'] == head['revision'] and current['digest'] == head['digest']
            rdf._verify_generation(current)
        import os
        from unittest.mock import patch
        with patch.dict(os.environ, {'LZO_ACTIVATION_CONFIG': str(result['configuration'])}):
            app = create_app()
        with TestClient(app, base_url='https://testserver') as client:
            client.cookies.update(s.client.cookies)
            response = client.get(f'/api/v1/geometry/{s.identifier}')
            assert response.status_code == 200, response.text
            assert response.json()['gis_status'] == 'current'
            assert response.json()['features'][0]['geometry']['coordinates'] == [114.5, 30.2]
            assert client.get(f'/api/v1/history/{s.identifier}/revisions/1').status_code == 200
            # Authorization is read from restored current governance state.
            from lzo.auth.session import SessionService, SESSION_COOKIE
            from lzo.db import PgUnitOfWork
            with PgUnitOfWork(registry.engine('core')) as uow:
                cookie = SessionService(uow, settings).create(s.users['nobody'], {'synthetic': True})
                uow.commit()
            client.cookies.set(SESSION_COOKIE, cookie)
            assert client.get(f'/api/v1/geometry/{s.identifier}').status_code == 404
            # Revoke the original reader in this isolated restored core only.
            owner = create_engine(settings.migration_database_url)
            try:
                with owner.begin() as c:
                    c.execute(text('UPDATE permission_grants SET active=false WHERE user_id=:id'), {'id': s.users['alice']})
                    c.execute(text('UPDATE authorization_state SET version=version+1'))
            finally:
                owner.dispose()
            client.cookies.update(s.client.cookies)
            assert client.get(f'/api/v1/geometry/{s.identifier}').status_code == 404
    finally:
        registry.close()
    with s.owner.connect() as c:
        assert c.execute(text('SELECT count(*) FROM semantic_objects WHERE id=:id'), {'id': s.identifier}).scalar_one() == 1
    assert s.backup_service.verify(s.backup_manifest)['status'] == 'verified'


def _approve(s, action):
    from lzo.governance.approvals import ApprovalService
    command = s.migration.command(s.plan, s.actor('alice'), action)
    with s.transaction() as uow:
        service = ApprovalService(uow)
        change = service.preview(s.actor('alice'), 'storage.activate', (s.plan,), command)
        approval = service.submit(s.actor('alice'), 'storage.activate', (s.plan,), change.change_digest,
            change.baseline, {'command': command})
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(Exception, match='SEPARATION_OF_DUTIES'):
            ApprovalService(uow).decide(s.actor('alice'), approval.id, 'approve', 'Self review refused')
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'), approval.id, 'approve', 'Independent synthetic recovery review')
        uow.commit()
    return approval.id


def _backup(s, markers, tmp_path, name):
    from lzo.storage.maintenance import MaintenanceWindow
    actor = s.actor('alice')
    s.app.dispose()
    s.client.app.state.storage_registry.close()
    with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path, additional=s.extra_owners) as window:
        return s.backup_service.create(tmp_path / name, window, actor, migration_plan=s.plan)


def _cutover(s, markers, tmp_path):
    from lzo.storage.maintenance import MaintenanceWindow
    from lzo.storage.migration import load_activation
    s.migration.reconcile(s.plan, s.actor('alice'), s.backup_manifest)
    approval = _approve(s, 'cutover')
    # The approved record does not freeze a reviewer's authority. Exercise the
    # exact executable-approval gate used below with a revoked current reviewer.
    from lzo.governance.approvals import ApprovalService
    from lzo.storage.migration import _policy
    with s.owner.begin() as c:
        reviewer_grant = c.execute(text('''SELECT g.id FROM permission_grants g
            JOIN platform_roles r ON r.id=g.role_id
            WHERE g.user_id=:id AND r.actions ? 'storage.review' '''), {'id': s.users['admin']}).scalar_one()
        c.execute(text('UPDATE permission_grants SET active=false WHERE id=:id'), {'id': reviewer_grant})
    try:
        with s.transaction() as uow:
            command = s.migration.command(s.plan, s.actor('alice'))
            change = _policy(uow, (s.plan,), command)
            from lzo.errors import ApiError
            with pytest.raises(ApiError, match='NOT_FOUND'):
                ApprovalService(uow).require_executable(s.actor('alice'), approval, change.change_digest, change.baseline)
    finally:
        with s.owner.begin() as c:
            c.execute(text('UPDATE permission_grants SET active=true WHERE id=:id'), {'id': reviewer_grant})
    actor = s.actor('alice')
    s.app.dispose()
    path = tmp_path / 'split-activation.json'
    with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path) as window:
        result = s.migration.cutover(s.plan, actor, approval, window, path)
        assert result['running_processes_switched'] is False
        assert not path.exists()
    target = load_activation(path)
    import os
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from lzo.main import create_app
    with patch.dict(os.environ, {'LZO_ACTIVATION_CONFIG': str(path)}):
        with TestClient(create_app(), base_url='https://testserver') as checked:
            checked.cookies.update(s.client.cookies)
            response = checked.get(f'/api/v1/geometry/{s.identifier}')
            assert response.status_code == 200 and response.json()['gis_status'] == 'current'
    from lzo.storage.registry import StorageRegistry
    s.client.app.state.settings = target
    s.client.app.state.storage_registry.close()
    s.client.app.state.storage_registry = StorageRegistry.from_settings(target)
    s.config = target
    response = s.client.get(f'/api/v1/geometry/{s.identifier}')
    assert response.status_code == 200 and response.json()['gis_status'] == 'current'
    assert response.json()['features'][0]['geometry']['coordinates'] == [114.5, 30.2]
    return target


def test_real_governed_cutover_and_post_write_compatible_rollback(recovery_case, stopped_writer_markers, tmp_path):
    from lzo.storage.maintenance import MaintenanceWindow
    from lzo.storage.migration import load_activation, MigrationError
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.blobs import get_blob_store
    from lzo.artifacts.service import ArtifactService
    from lzo.contracts import BlobRef
    from lzo.synchronization.delivery import deliver_once
    from lzo.gis.repository import apply_geometry
    import io
    s = recovery_case
    target = _cutover(s, stopped_writer_markers, tmp_path)
    s.geometry(2, 114.7)
    registry = StorageRegistry.from_settings(target)
    try:
        assert deliver_once(registry, 'gis', apply_geometry).delivered == 1
    finally:
        registry.close()
    payload = b'synthetic post-cutover evidence retained by compatible rollback'
    with s.transaction() as uow:
        blob = get_blob_store(target).put(io.BytesIO(payload), 'text/plain')
        accepted = ArtifactService(uow, get_blob_store(target), target).register_upload(
            s.actor('alice'), s.ids_by_name['object_a'], blob, filename='post-cutover.txt', metadata={'synthetic': True})
        uow.commit()
    with pytest.raises(MigrationError, match='BACKUP_NOT_VERIFIED'):
        s.migration.reconcile(s.plan, s.actor('alice'), s.backup_manifest)
    s.migration.reconcile(s.plan, s.actor('alice'))
    manifest = _backup(s, stopped_writer_markers, tmp_path, 'post-write-backup')
    s.migration.reconcile(s.plan, s.actor('alice'), manifest)
    approval = _approve(s, 'rollback')
    actor = s.actor('alice')
    s.app.dispose()
    path = tmp_path / 'legacy-activation.json'
    with MaintenanceWindow(s.owner, s.target_owner, stopped_writer_markers, tmp_path) as window:
        s.migration.rollback(s.plan, actor, approval, window, path)
        assert not path.exists()
    restored = load_activation(path)
    s.client.app.state.settings = restored
    response = s.client.get(f'/api/v1/geometry/{s.identifier}')
    assert response.status_code == 200
    assert response.json()['features'][0]['geometry']['coordinates'] == [114.7, 30.2]
    with get_blob_store(restored).open(BlobRef(blob.sha256, blob.size_bytes, blob.media_type, accepted.version_id)) as source:
        assert source.read() == payload
    with s.owner.connect() as c:
        assert c.execute(text('SELECT count(*) FROM revisions WHERE resource_id=:id'), {'id': s.identifier}).scalar_one() == 3
    s.login('nobody')
    assert s.client.get(f'/api/v1/geometry/{s.identifier}').status_code == 404


def test_split_only_post_write_restore_preserves_catalog_telemetry_and_relationships(recovery_case, stopped_writer_markers, tmp_path):
    """A refused downgrade must have a tested recovery path containing new writes."""
    import io
    from datetime import datetime, timedelta, timezone
    from decimal import Decimal
    from sqlalchemy import select, update
    from lzo.auth.models import roles, authorization_state
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.restore import RestoreService, load_restored_settings
    from lzo.storage.maintenance import MaintenanceWindow
    from lzo.storage.migration import MigrationError
    from lzo.catalog.schemas import CreateModel, ModelMetadata, VersionModel, DeviceBinding, CreateDocument, DocumentMetadata
    from lzo.catalog.service import CatalogService
    from lzo.contracts import ResourceScope, BlobRef
    from lzo.ingestion.schemas import SourceCreate
    from lzo.ingestion.sources import SourceService
    from lzo.telemetry.schemas import ObservationInput, BatchInput, Supersedes
    from lzo.telemetry.service import TelemetryService
    from lzo.synchronization.delivery import poll_deliveries
    s = recovery_case
    target = _cutover(s, stopped_writer_markers, tmp_path)
    with s.transaction() as uow:
        actions = uow.session.execute(select(roles.c.actions).where(roles.c.id == s.roles['editor'])).scalar_one()
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(
            actions=sorted(set(actions) | {'source.manage', 'source.read', 'catalog.manage'})))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version + 1))
        uow.commit()
    payload = b'synthetic catalog attachment accepted after governed cutover'
    scope = ResourceScope(None, s.scopes['farm_a'], (), 'data')
    registry = StorageRegistry.from_settings(target)
    try:
        with s.transaction() as uow:
            uow.ontology_settings = target
            blob = get_blob_store(target).put(io.BytesIO(payload), 'text/plain')
            artifact = ArtifactService(uow, get_blob_store(target), target).register_upload(
                s.actor('alice'), s.ids_by_name['object_a'], blob, filename='model-post-cutover.txt', metadata={'synthetic': True})
            catalog = CatalogService(uow, registry)
            model = catalog.create_model(s.actor('alice'), CreateModel(scope=scope,
                metadata=ModelMetadata(exact_model='Synthetic recovery sensor', synthetic=True,
                    attachment_version_ids=(artifact.version_id,))), str(uuid4()))
            model = catalog.version_model(s.actor('alice'), model.id, VersionModel(expected_revision=1,
                metadata=model.metadata.model_copy(update={'firmware': 'recovery-v2'})), str(uuid4()))
            binding = catalog.bind_device(s.actor('alice'), model.id,
                DeviceBinding(entity_id=s.ids_by_name['object_a'], expected_revision=0), str(uuid4()))
            document = catalog.create_document(s.actor('alice'), CreateDocument(scope=scope,
                metadata=DocumentMetadata(title='Synthetic restored manual', document_type='manual',
                    artifact_version_id=artifact.version_id, source_uri='urn:synthetic:recovery:manual',
                    source_version='v1', license='synthetic-test-only', model_ids=(model.id,),
                    entity_ids=(s.identifier,))), str(uuid4()))
            source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='synthetic',
                source_instance=uuid4().hex, collection='post-cutover-recovery', kind='file', scope=scope, data_nature='synthetic'))
            uow.commit()
        sampled = datetime.now(timezone.utc) + timedelta(seconds=1)
        # Use the existing explicitly supported owner maintenance interface;
        # this is not verification of the deferred group-role recipe (M2).
        from lzo.telemetry.maintenance import maintain_partitions
        month = sampled.date().replace(day=1)
        next_month = (month.replace(day=28) + timedelta(days=4)).replace(day=1)
        maintain_partitions(target.timeseries_migration_database_url, month, next_month, owner=True)
        observation = ObservationInput(source_record_id='recovered-one', object_id=s.identifier,
            device_id=s.ids_by_name['object_a'], point_id='synthetic-water-1', metric_id='temperature',
            sampled_at=sampled, reported_at=sampled + timedelta(seconds=2), raw_value='12.345678901234567890',
            raw_unit='Cel', normalized_value='12.345678901234567890', normalized_unit='Cel', data_nature='synthetic',
            artifact_version_id=artifact.version_id, device_binding_ref=binding.revision, model_ref=model.revision)
        for record in (observation, observation.model_copy(update={'version': 2,
                'supersedes': Supersedes(source_record_id='recovered-one', version=1), 'normalized_value': Decimal('13')})):
            with s.transaction() as uow:
                uow.ontology_settings = target
                TelemetryService(uow, registry).accept(s.actor('alice'), BatchInput(source_id=source.id, records=(record,)), str(uuid4()))
                uow.commit()
        poll_deliveries(registry)
        with s.transaction() as uow:
            uow.ontology_settings = target
            assert CatalogService(uow, registry).get(s.actor('alice'), model.id).projection_status == 'current'
            assert len(TelemetryService(uow, registry).query(s.actor('alice'), kind='observation', history=True)['items']) == 2
    finally:
        registry.close()
    s.migration.reconcile(s.plan, s.actor('alice'))
    manifest = _backup(s, stopped_writer_markers, tmp_path, 'split-post-write-backup')
    s.migration.reconcile(s.plan, s.actor('alice'), manifest)
    approval = _approve(s, 'rollback')
    actor = s.actor('alice')
    s.app.dispose()
    path = tmp_path / 'forbidden-legacy-activation.json'
    with pytest.raises(MigrationError, match='RESTORE_REQUIRED'):
        with MaintenanceWindow(s.owner, s.target_owner, stopped_writer_markers, tmp_path) as window:
            s.migration.rollback(s.plan, actor, approval, window, path)
    assert not path.exists()
    with s.owner.connect() as c:
        assert c.execute(text('SELECT state FROM storage_migration_plans WHERE id=:id'), {'id': s.plan}).scalar_one() == 'activated'
    result = RestoreService(s.admin_engine, s.pg_bin, s.authority).restore(manifest,
        'lzo_restore_' + uuid4().hex[:16], tmp_path / 'post-write-restored')
    restored = load_restored_settings(result['configuration'])
    assert result['references']['catalog_versions'] == 3 and result['references']['telemetry_records'] == 2
    from fastapi.testclient import TestClient
    from lzo.main import create_app
    with TestClient(create_app(restored), base_url='https://testserver') as client:
        client.cookies.update(s.client.cookies)
        current = client.get(f'/api/v1/catalog/models/{model.id}')
        assert current.status_code == 200 and current.json()['metadata']['firmware'] == 'recovery-v2'
        assert current.json()['projection_status'] == 'current'
        versions = client.get(f'/api/v1/catalog/models/{model.id}/versions')
        assert versions.status_code == 200 and len(versions.json()['items']) == 2
        devices = client.get(f'/api/v1/catalog/models/{model.id}/devices')
        assert devices.status_code == 200 and devices.json()[0]['entity_id'] == str(s.ids_by_name['object_a'])
        manual = client.get(f'/api/v1/catalog/documents/{document.id}')
        assert manual.status_code == 200 and manual.json()['metadata']['artifact_version_id'] == str(artifact.version_id)
        observations = client.get('/api/v1/telemetry/observations', params={'object_id': str(s.identifier)})
        assert observations.status_code == 200 and observations.json()['status'] == 'current'
        assert [row['normalized_value'] for row in observations.json()['items']] == ['13']
        history = client.get('/api/v1/telemetry/observations', params={'object_id': str(s.identifier), 'history': True})
        assert history.status_code == 200 and {row['version'] for row in history.json()['items']} == {1, 2}
        assert {row['version']: row['normalized_value'] for row in history.json()['items']} == {
            1: '12.345678901234567890', 2: '13'}
        assert history.json()['items'][0]['device_binding_ref']['event_id'] == str(binding.revision.event_id)
    with get_blob_store(restored).open(BlobRef(blob.sha256, blob.size_bytes, blob.media_type, artifact.version_id)) as retained:
        assert retained.read() == payload
    restored_registry = StorageRegistry.from_settings(restored)
    try:
        with restored_registry.engine('timeseries').connect() as c:
            assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': source.id}).scalar_one() == 2
            assert c.execute(text('SELECT DISTINCT tableoid::regclass::text FROM telemetry_samples WHERE source_id=:id'), {'id': source.id}).scalar_one() == 'telemetry_samples_' + month.strftime('%Y%m')
    finally:
        restored_registry.close()
