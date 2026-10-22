"""Checked legacy migration against owned PostgreSQL/PostGIS and private S3."""
import io
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, insert, select, text, update


@pytest.fixture
def migration_case(object_case, tmp_path, request):
    from dotenv import dotenv_values
    from lzo.artifacts.service import ArtifactService
    from lzo.artifacts.store import ArtifactStore
    from lzo.auth import models
    from lzo.storage.blobs import get_blob_store
    from lzo.storage.migrate import migrate_domain
    from lzo.storage.migration import MigrationService
    s = object_case
    schema = s.app.connect()
    try:
        source_schema = schema.execute(text('SELECT current_schema()')).scalar_one()
    finally:
        schema.close()
    source_url = str(s.app.url.set(query={'options': '-csearch_path=' + source_schema}))
    # SQLAlchemy string rendering hides passwords; render only into private Settings.
    source_url = s.app.url.set(query={'options': '-csearch_path=' + source_schema}).render_as_string(hide_password=False)
    owner_url = create_engine(s.config.migration_database_url).url.set(
        query={'options': '-csearch_path=' + source_schema}).render_as_string(hide_password=False)
    s.source = s.config.model_copy(update={'storage_mode': 'legacy', 'blob_provider': 'local',
        'database_url': source_url, 'migration_database_url': owner_url})
    s.config = s.source
    s.client.app.state.settings = s.source
    with s.transaction() as uow:
        for name, actions in [('alice', ['view', 'storage.manage']), ('admin', ['view', 'storage.review'])]:
            role = uuid4()
            uow.session.execute(insert(models.roles).values(id=role, name='migration-' + name, actions=actions, revision=1))
            uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=s.users[name],
                role_id=role, security_class='storage', global_scope=True, revision=1))
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version + 1))
        uow.commit()
    s.identifier = UUID(s.create_object().json()['revision']['resource_id'])
    def geometry(revision, x):
        body = {'resource_id': str(s.identifier), 'expected_revision': revision, 'source_crs': 'EPSG:4326',
            'geojson': {'type': 'Point', 'coordinates': [x, 30.2]},
            'source_support': s.assertion(s.identifier)['support'], 'decision_ref': None}
        response = s.client.put(f'/api/v1/geometry/{s.identifier}', headers=s.headers(revision),
            json={'geometry': body, 'bundle_id': str(s.bundle_id)})
        assert response.status_code == 200, response.text
    s.geometry = geometry
    geometry(1, 114.5)
    s.blobs = []
    def artifact(payload, legacy=False):
        store = ArtifactStore(s.source.file_root if legacy else s.source.file_root / 'artifacts', max_upload_bytes=1000000)
        blob = store.put(io.BytesIO(payload), 'text/plain')
        with s.transaction() as uow:
            result = ArtifactService(uow, get_blob_store(s.source), s.source).register_upload(
                s.actor('alice'), s.ids_by_name['object_a'], blob, filename='synthetic.txt', metadata={'synthetic': True})
            uow.commit()
        s.blobs.append((result.version_id, payload))
        return result
    s.artifact = artifact
    artifact(b'canonical migration bytes')
    artifact(b'legacy migration bytes', True)
    from lzo.artifacts.uploads import UploadService
    import hashlib
    import asyncio
    s.source = s.source.model_copy(update={'artifact_upload_part_bytes': 4})
    s.config = s.source
    s.client.app.state.settings = s.source
    async def first_part():
        yield b'abcd'
    with s.transaction() as uow:
        uploads = UploadService(uow, get_blob_store(s.source), s.source)
        session = uploads.create(s.actor('alice'), resource_id=s.ids_by_name['object_a'],
            filename='resumed.txt', media_type='text/plain', size_bytes=8,
            sha256=hashlib.sha256(b'abcdefgh').hexdigest(), idempotency_key='migration-session')
        s.upload_session = session['id']
        uploads.part(s.actor('alice'), s.upload_session, 1,
            asyncio.run(get_blob_store(s.source).stage_async(first_part(), 'text/plain')))
        uow.commit()
    target_schema = 'migration_' + uuid4().hex
    admin = create_engine(s.source.gis_migration_database_url)
    with admin.begin() as c:
        c.execute(text(f'CREATE SCHEMA "{target_schema}" AUTHORIZATION lzo_gis_owner'))
        c.execute(text(f'GRANT USAGE ON SCHEMA "{target_schema}" TO lzo_gis_app'))
    target_owner_url = admin.url.update_query_dict({'options': '-csearch_path=' + target_schema}).render_as_string(hide_password=False)
    target_app_url = create_engine(s.source.gis_database_url).url.update_query_dict(
        {'options': '-csearch_path=' + target_schema}).render_as_string(hide_password=False)
    migrate_domain('gis', target_owner_url)
    object_env = dotenv_values(Path(__file__).resolve().parents[3] / '.runtime/object-store/services.env')
    s.target = s.source.model_copy(update={'storage_mode': 'split', 'gis_database_url': target_app_url,
        'gis_migration_database_url': target_owner_url, 'blob_provider': 's3',
        's3_key_prefix': 'migration-tests/' + uuid4().hex,
        **{key[4:].lower(): value for key, value in object_env.items() if key.startswith('LZO_S3_')},
        'blob_staging_path': tmp_path / 'target-staging'})
    # Native credentials are restricted to the provisioned bucket, so each plan owns
    # a fresh GIS schema and its own immutable content set in that private bucket.
    s.target = type(s.target).model_validate(s.target.model_dump())
    s.owner = create_engine(owner_url)
    s.target_owner = create_engine(target_owner_url)
    s.extra_owners = {domain: create_engine(getattr(s.target, domain + '_migration_database_url'))
        for domain in ('timeseries', 'catalog')}
    s.catalog_schema = None
    if getattr(request, 'param', None) == 'isolated_catalog':
        s.catalog_schema = 'migration_' + uuid4().hex
        with s.extra_owners['catalog'].begin() as c:
            c.exec_driver_sql('CREATE SCHEMA ' + s.catalog_schema + ' AUTHORIZATION lzo_catalog_owner')
            c.exec_driver_sql('GRANT USAGE ON SCHEMA ' + s.catalog_schema + ' TO lzo_catalog_app')
        overrides = {}
        for kind in ('database_url', 'migration_database_url'):
            key = 'catalog_' + kind
            from sqlalchemy.engine import make_url
            overrides[key] = make_url(getattr(s.target, key)).update_query_dict(
                {'options': '-csearch_path=' + s.catalog_schema}).render_as_string(hide_password=False)
        s.target = s.target.model_copy(update=overrides)
        migrate_domain('catalog', s.target.catalog_migration_database_url)
        s.extra_owners['catalog'].dispose()
        s.extra_owners['catalog'] = create_engine(s.target.catalog_migration_database_url)
    s.migration = MigrationService(s.owner, s.target_owner, s.source, s.target)
    s.plan = s.migration.preview(s.actor('alice'))
    yield s
    s.owner.dispose()
    s.target_owner.dispose()
    for engine in s.extra_owners.values():
        engine.dispose()
    if s.catalog_schema:
        engine = create_engine(s.source.catalog_migration_database_url)
        with engine.begin() as c:
            for suffix in ('', '_original', '_replacement'):
                c.exec_driver_sql('DROP SCHEMA IF EXISTS ' + s.catalog_schema + suffix + ' CASCADE')
        engine.dispose()
    with admin.begin() as c:
        c.execute(text(f'DROP SCHEMA "{target_schema}" CASCADE'))
    admin.dispose()


def test_interrupted_duplicate_backfill_preserves_versions_and_both_artifact_roots(migration_case):
    s = migration_case
    first = s.migration.backfill(s.plan, s.actor('alice'), limit=1)
    assert first['remaining'] > 0
    # A newly constructed controller resumes only from durable core checkpoints.
    from lzo.storage.migration import MigrationService
    resumed = MigrationService(s.owner, s.target_owner, s.source, s.target)
    result = resumed.backfill(s.plan, s.actor('alice'))
    assert result['remaining'] == 0
    assert resumed.backfill(s.plan, s.actor('alice'))['copied'] == 0
    report = resumed.reconcile(s.plan, s.actor('alice'))
    assert report['status'] == 'reconciled'
    with s.target_owner.connect() as c:
        assert c.execute(text('SELECT count(*) FROM gis_geometry_versions WHERE resource_id=:id'),
            {'id': s.identifier}).scalar_one() == 2
    from lzo.storage.blobs import get_blob_store
    from lzo.contracts import BlobRef
    from lzo.artifacts.models import artifact_versions
    for identifier, payload in s.blobs:
        with s.transaction() as uow:
            row = uow.session.execute(select(artifact_versions).where(artifact_versions.c.id == identifier)).mappings().one()
        with get_blob_store(s.target).open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], identifier)) as stream:
            assert stream.read() == payload
    assert report['counts'] == {'geometry': 2, 'blob': 6}  # Includes three schema versions and one resumable part.
    store = get_blob_store(s.target)
    assert store.key(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], identifier)).startswith(s.target.s3_key_prefix + '/')
    # The actual application reads the checked target configuration. This is an
    # isolated read check, not a claim that offline activation has been certified.
    s.client.app.state.settings = s.target
    response = s.client.get(f'/api/v1/geometry/{s.identifier}')
    assert response.status_code == 200
    assert response.json()['gis_status'] == 'current'
    assert response.json()['features'][0]['geometry']['coordinates'] == [114.5, 30.2]
    s.login('nobody')
    assert s.client.get(f'/api/v1/geometry/{s.identifier}').status_code == 404
    s.login('alice')
    from lzo.artifacts.uploads import UploadService
    import asyncio
    async def final_part():
        yield b'efgh'
    with s.transaction() as uow:
        uploads = UploadService(uow, store, s.target)
        uploads.part(s.actor('alice'), s.upload_session, 2, asyncio.run(store.stage_async(final_part(), 'text/plain')))
        result = uploads.complete(s.actor('alice'), s.upload_session)
        uow.commit()
    assert result.version_id == s.upload_session
    with store.open(BlobRef(result.sha256, result.size_bytes, result.media_type, result.version_id)) as stream:
        assert stream.read() == b'abcdefgh'


def test_new_events_and_target_corruption_invalidate_reconciliation(migration_case):
    from lzo.storage.migration import MigrationError
    s = migration_case
    s.migration.backfill(s.plan, s.actor('alice'))
    old = s.migration.reconcile(s.plan, s.actor('alice'))
    s.geometry(2, 114.6)
    with pytest.raises(MigrationError, match='BACKFILL_REQUIRED'):
        s.migration.reconcile(s.plan, s.actor('alice'))
    s.migration.backfill(s.plan, s.actor('alice'))
    new = s.migration.reconcile(s.plan, s.actor('alice'))
    assert new['data_digest'] != old['data_digest']
    with s.target_owner.begin() as c:
        c.execute(text("UPDATE gis_geometry_versions SET payload_hash=repeat('0',64) WHERE resource_id=:id AND revision=3"), {'id': s.identifier})
    with pytest.raises(MigrationError, match='DIGEST_MISMATCH'):
        s.migration.reconcile(s.plan, s.actor('alice'))


def test_wrong_target_and_unauthorized_controller_are_refused(migration_case):
    from lzo.errors import ApiError
    from lzo.storage.migration import MigrationError, MigrationService
    s = migration_case
    # Another plan cannot claim this prefix even through a different GIS
    # connection. The immutable namespace owner, not just object existence,
    # makes the destination exclusive before any backfill has run.
    from lzo.storage.migration import verify_blob_namespace
    verify_blob_namespace(s.target, 'migration', s.plan)
    competing = MigrationService(s.owner, create_engine(s.source.gis_migration_database_url),
        s.source, s.target.model_copy(update={'gis_database_url': s.source.gis_database_url,
            'gis_migration_database_url': s.source.gis_migration_database_url}))
    try:
        with pytest.raises(MigrationError, match='TARGET_BLOB_NAMESPACE_NOT_NEW'):
            competing.preview(s.actor('alice'))
    finally:
        competing.gis.dispose()
    from lzo.storage.blobs import get_blob_store
    from lzo.artifacts.models import artifact_versions
    from lzo.contracts import BlobRef
    with s.transaction() as uow:
        assert uow.session.execute(text('SELECT count(*) FROM storage_migration_plans')).scalar_one() == 1
        rows = {r['id']: r for r in uow.session.execute(select(artifact_versions)).mappings()}
    for identifier, payload in s.blobs:
        row = rows[identifier]
        with get_blob_store(s.source).open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], identifier)) as original:
            assert original.read() == payload
    with pytest.raises(ApiError):
        s.migration.backfill(s.plan, s.actor('nobody'))
    wrong = MigrationService(s.owner, create_engine(s.source.gis_migration_database_url), s.source, s.target)
    with pytest.raises(MigrationError, match='TARGET_CHANGED'):
        wrong.backfill(s.plan, s.actor('alice'))


@pytest.mark.parametrize('boundary', ['geometry', 'blob'])
def test_real_target_commit_before_checkpoint_loss_replays_without_duplicate_versions(migration_case, monkeypatch, boundary):
    from lzo.storage import migration
    from lzo.storage.s3 import S3BlobStore
    s = migration_case
    if boundary == 'geometry':
        original = migration._acknowledge
        def interrupted(*args):
            raise RuntimeError('owned synthetic process loss after target commit')
        monkeypatch.setattr(migration, '_acknowledge', interrupted)
    else:
        original = S3BlobStore.put
        def interrupted(*args):
            original(*args)
            raise RuntimeError('owned synthetic process loss after target commit')
        monkeypatch.setattr(S3BlobStore, 'put', interrupted)
    with pytest.raises(RuntimeError, match='process loss after target commit'):
        s.migration.backfill(s.plan, s.actor('alice'))
    if boundary == 'geometry':
        monkeypatch.setattr(migration, '_acknowledge', original)
        with s.target_owner.connect() as c:
            assert c.execute(text('SELECT count(*) FROM gis_geometry_versions')).scalar_one() == 1
    else:
        monkeypatch.setattr(S3BlobStore, 'put', original)
    resumed = migration.MigrationService(s.owner, s.target_owner, s.source, s.target)
    assert resumed.backfill(s.plan, s.actor('alice'))['remaining'] == 0
    assert resumed.backfill(s.plan, s.actor('alice'))['copied'] == 0
    assert resumed.reconcile(s.plan, s.actor('alice'))['counts'] == {'geometry': 2, 'blob': 6}


@pytest.fixture
def stopped_writer_markers(tmp_path):
    import subprocess
    import sys
    markers = tmp_path / 'writer-records'
    env = dict(os.environ, LZO_WRITER_OWNERSHIP_DIR=str(markers), LZO_WRITER_WORKSPACE=str(tmp_path),
        PYTHONPATH=str(Path(__file__).resolve().parents[2] / 'src'))
    # These processes really start/record/exit in this owned fixture. Core/GIS
    # table locks below fence the in-process test controller's writes as well.
    for name in ('api', 'worker'):
        result = subprocess.run([sys.executable, '-c',
            f"from lzo.storage.maintenance import record_writer;record_writer('{name}')"],
            env=env, cwd=tmp_path, capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
    return markers


def test_backfill_cannot_regress_a_concurrently_activated_plan(migration_case, monkeypatch):
    """Negative race fixture only; this state edit does not certify activation."""
    from lzo.storage.s3 import S3BlobStore
    from lzo.storage.migration import MigrationError
    s = migration_case
    original = S3BlobStore.put
    def concurrent_state_change(*args):
        result = original(*args)
        with s.owner.begin() as c:
            c.execute(text("UPDATE storage_migration_plans SET state='activated' WHERE id=:id"), {'id': s.plan})
        return result
    monkeypatch.setattr(S3BlobStore, 'put', concurrent_state_change)
    with pytest.raises(MigrationError, match='PLAN_ALREADY_ACTIVATED'):
        s.migration.backfill(s.plan, s.actor('alice'))
    with s.owner.connect() as c:
        assert c.execute(text('SELECT state FROM storage_migration_plans WHERE id=:id'), {'id': s.plan}).scalar_one() == 'activated'


def test_preexisting_orphan_gis_rows_are_preserved_and_refused(migration_case):
    from lzo.storage.migration import MigrationService, MigrationError
    s = migration_case
    orphan = uuid4()
    # Owned negative fixture: no inbox receipt or namespace tag, but real
    # professional data already exists. It is not an empty new destination.
    with s.target_owner.begin() as c:
        c.execute(text('DROP TABLE storage_migration_namespace'))
        c.execute(text('''INSERT INTO gis_geometry_versions
            (resource_id,revision,event_id,payload_hash,active) VALUES(:id,1,:event,:hash,false)'''),
            {'id': orphan, 'event': uuid4(), 'hash': '0' * 64})
    target = s.target.model_copy(update={'s3_key_prefix': 'migration-tests/' + uuid4().hex})
    with pytest.raises(MigrationError, match='TARGET_NOT_NEW'):
        MigrationService(s.owner, s.target_owner, s.source, target).preview(s.actor('alice'))
    with s.target_owner.connect() as c:
        assert c.execute(text('SELECT count(*) FROM gis_geometry_versions WHERE resource_id=:id'), {'id': orphan}).scalar_one() == 1


def test_reconciliation_preserves_winning_terminal_state(migration_case, monkeypatch):
    from lzo.storage.migration import MigrationError
    s = migration_case
    s.migration.backfill(s.plan, s.actor('alice'))
    original = s.migration._verify
    def winning_transition(values):
        original(values)
        with s.owner.begin() as c:
            c.execute(text("UPDATE storage_migration_plans SET state='rolled_back',epoch=epoch+1 WHERE id=:id"), {'id': s.plan})
    monkeypatch.setattr(s.migration, '_verify', winning_transition)
    with pytest.raises(MigrationError, match='RECONCILIATION_STATE_CHANGED'):
        s.migration.reconcile(s.plan, s.actor('alice'))
    monkeypatch.setattr(s.migration, '_verify', original)
    with s.owner.connect() as c:
        before = c.execute(text('SELECT state,epoch,reconciliation FROM storage_migration_plans WHERE id=:id'), {'id': s.plan}).one()
    assert before.state == 'rolled_back'
    with pytest.raises(MigrationError, match='PLAN_TERMINAL'):
        s.migration.reconcile(s.plan, s.actor('alice'))
    with s.owner.connect() as c:
        assert c.execute(text('SELECT state,epoch,reconciliation FROM storage_migration_plans WHERE id=:id'), {'id': s.plan}).one() == before


def test_maintenance_refuses_writers_and_really_blocks_database_writes(migration_case, stopped_writer_markers, tmp_path):
    from lzo.storage.maintenance import MaintenanceWindow, record_writer
    from lzo.storage.migration import MigrationError
    from sqlalchemy.exc import DBAPIError
    s = migration_case
    actor = s.actor('alice')
    markers = stopped_writer_markers
    old = (markers / 'api.json').read_bytes()
    record_writer('api', directory=markers, workspace=tmp_path)
    with pytest.raises(MigrationError, match='WRITER_RUNNING'):
        with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path):
            pass
    (markers / 'api.json').write_bytes(old)
    with s.app.connect():
        with pytest.raises(MigrationError, match='WRITER_CONNECTIONS'):
            with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path):
                pass
    s.app.dispose()
    app_target = create_engine(s.target.gis_database_url)
    try:
        with app_target.connect():
            with pytest.raises(MigrationError, match='WRITER_CONNECTIONS'):
                with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path):
                    pass
    finally:
        app_target.dispose()
    with MaintenanceWindow(s.owner, s.target_owner, markers, tmp_path, additional=s.extra_owners) as window:
        assert window.evidence['core']['locked_tables'] > 20
        assert window.evidence['gis']['locked_tables'] >= 8
        with pytest.raises(DBAPIError):
            with s.owner.begin() as writer:
                writer.execute(text("SET LOCAL lock_timeout='100ms'"))
                writer.execute(text('UPDATE semantic_geometry SET active=false WHERE resource_id=:id'), {'id': s.identifier})
        with pytest.raises(MigrationError, match='ACTIVATION_STATE_CONFLICT'):
            s.migration.cutover(s.plan, actor, uuid4(), window, tmp_path / 'refused.json')
        assert window.core.in_transaction()
        # A refused inner UoW must not release the outer maintenance locks.
        with pytest.raises(DBAPIError):
            with s.owner.begin() as writer:
                writer.execute(text("SET LOCAL lock_timeout='100ms'"))
                writer.execute(text('UPDATE semantic_geometry SET active=false WHERE resource_id=:id'), {'id': s.identifier})
    # The refused writer changed neither current geometry nor permissions.
    with s.owner.connect() as c:
        assert c.execute(text('SELECT active FROM semantic_geometry WHERE resource_id=:id'), {'id': s.identifier}).scalar_one() is True
        assert c.execute(text("SELECT has_table_privilege('lzo_app','semantic_geometry','UPDATE')")).scalar_one() is True


def test_public_semantic_object_attachment_exposes_legacy_head_repair_requirement(migration_case):
    """Preserve the confirmed integration defect; migration must fail closed.

    This does NOT assert that semantic-object attachment + later edit works.
    The attachment and edit behavior predates this task; 6C owns its repair.
    """
    from lzo.storage.migration import MigrationError
    s = migration_case
    response = s.client.post('/api/v1/artifacts', params={'resource_id': str(s.identifier), 'filename': 'object-evidence.txt'},
        headers={**s.headers(2), 'Content-Type': 'text/plain'}, content=b'synthetic semantic-object evidence')
    assert response.status_code == 201, response.text
    with s.owner.connect() as c:
        assert c.execute(text('SELECT revision FROM resource_heads WHERE resource_id=:id'), {'id': s.identifier}).scalar_one() == 3
        assert c.execute(text('SELECT revision FROM semantic_objects WHERE id=:id'), {'id': s.identifier}).scalar_one() == 2
    body = {'resource_id': str(s.identifier), 'expected_revision': 2, 'source_crs': 'EPSG:4326',
        'geojson': {'type': 'Point', 'coordinates': [114.6, 30.2]},
        'source_support': s.assertion(s.identifier)['support'], 'decision_ref': None}
    edited = s.client.put(f'/api/v1/geometry/{s.identifier}', headers=s.headers(2),
        json={'geometry': body, 'bundle_id': str(s.bundle_id)})
    assert edited.status_code == 409 and edited.json()['code'] == 'REVISION_CONFLICT'
    with pytest.raises(MigrationError, match='LEGACY_HEAD_REPAIR_REQUIRED'):
        s.migration.backfill(s.plan, s.actor('alice'))


def test_cutover_refuses_missing_backup_forged_receipt_and_stale_final_state(migration_case, stopped_writer_markers, tmp_path):
    from lzo.storage.maintenance import MaintenanceWindow
    from lzo.storage.migration import MigrationError
    s = migration_case
    s.migration.backfill(s.plan, s.actor('alice'))
    s.migration.reconcile(s.plan, s.actor('alice'))
    with pytest.raises(MigrationError, match='BACKUP_REQUIRED'):
        s.migration.command(s.plan, s.actor('alice'))
    false_receipt = tmp_path / 'not-a-backup.json'
    false_receipt.write_text(json.dumps({'status': 'verified'}))
    with pytest.raises(MigrationError, match='BACKUP_(IMPLEMENTATION_REQUIRED|NOT_VERIFIED)'):
        s.migration.reconcile(s.plan, s.actor('alice'), false_receipt)
    actor = s.actor('alice')
    s.app.dispose()
    artifact = tmp_path / 'activation.json'
    with pytest.raises(MigrationError, match='BACKUP_REQUIRED'):
        with MaintenanceWindow(s.owner, s.target_owner, stopped_writer_markers, tmp_path, additional=s.extra_owners) as window:
            s.migration.cutover(s.plan, actor, uuid4(), window, artifact)
    assert not artifact.exists()
    s.geometry(2, 114.6)
    actor = s.actor('alice')
    s.app.dispose()
    with pytest.raises(MigrationError, match='RECONCILIATION_STALE'):
        with MaintenanceWindow(s.owner, s.target_owner, stopped_writer_markers, tmp_path, additional=s.extra_owners) as window:
            s.migration.cutover(s.plan, actor, uuid4(), window, artifact)
    assert not artifact.exists()
    with s.owner.connect() as c:
        assert c.execute(text('SELECT state FROM storage_migration_plans WHERE id=:id'), {'id': s.plan}).scalar_one() == 'reconciled'
