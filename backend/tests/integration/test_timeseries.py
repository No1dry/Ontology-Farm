"""Real PostgreSQL telemetry acceptance; removing atomic identity/current ACL checks breaks these tests."""
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID, uuid4
import os

import pytest
from sqlalchemy import text, update, select


def test_telemetry_routes_are_registered(settings):
    from lzo.main import create_app
    app = create_app(settings)
    try:
        paths = app.openapi()['paths']
        for path in ('batches', 'observations', 'events', 'media', 'tracks'):
            assert '/api/v1/telemetry/' + path in paths
        assert '/api/v1/objects/{id}/data' in paths
        assert paths['/api/v1/telemetry/observations']['get']['responses']['200']['content']['application/json']['schema']['$ref'].endswith('/TelemetryPage')
    finally:
        app.state.storage_registry.close()


@pytest.fixture
def telemetry_case(scope_scenario, settings, tmp_path):
    from lzo.auth.models import roles
    from lzo.contracts import ResourceScope
    from lzo.ingestion.schemas import SourceCreate
    from lzo.ingestion.sources import SourceService
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.migrate import migrate_domain
    from lzo.telemetry.service import TelemetryService
    from lzo.telemetry.schemas import ObservationInput, BatchInput
    from lzo.telemetry.maintenance import maintain_partitions
    s = scope_scenario
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=[
            'view', 'edit', 'source.manage', 'source.read', 'catalog.manage', 'evidence.manage', 'evidence.read']))
        uow.commit()
    s.writer = s.grant('alice', 'editor', scope='farm_a')
    s.config = settings.model_copy(update={'file_root': tmp_path, 'blob_provider': 'local'})
    migrate_domain('timeseries', s.config.timeseries_migration_database_url, core_url=s.config.database_url)
    maintain_partitions(os.environ['LZO_TIMESERIES_MAINTENANCE_DATABASE_URL'], '2026-01-01', '2026-04-01')
    s.registry = StorageRegistry.from_settings(s.config)
    s.registry._engines['core'].dispose()
    s.registry._engines['core'] = s.app
    s.scope = ResourceScope(None, s.scopes['farm_a'], (), 'data')
    with s.transaction() as uow:
        s.source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='synthetic',
            source_instance=uuid4().hex, collection='field-pond-orchard', kind='file', scope=s.scope, data_nature='synthetic'))
        uow.commit()
    def service(uow):
        uow.ontology_settings = s.config
        return TelemetryService(uow, s.registry)
    s.telemetry = service
    def observation(record='one', **changes):
        return ObservationInput(source_record_id=record, object_id=s.ids_by_name['object_a'],
            device_id=s.ids_by_name['object_a'], point_id='water-1', metric_id='temperature',
            sampled_at='2026-01-15T12:00:00Z', reported_at='2026-01-15T12:00:02Z',
            raw_value='12.345678901234567890', raw_unit='Cel', normalized_value='12.345678901234567890',
            normalized_unit='Cel', data_nature='synthetic', **changes)
    s.observation = observation
    def accept(records, key=None):
        with s.transaction() as uow:
            result = service(uow).accept(s.actor('alice'), BatchInput(source_id=s.source.id, records=tuple(records)), key or str(uuid4()))
            uow.commit()
            return result
    s.accept = accept
    def query(**kwargs):
        with s.transaction() as uow:
            return service(uow).query(s.actor('alice'), **kwargs)
    s.query = query
    yield s
    s.registry._engines.pop('core')
    s.registry.close()


def test_late_duplicates_global_timestamp_conflict_decimal_unknown_zero_and_interval(telemetry_case):
    from lzo.errors import ApiError
    from lzo.synchronization.delivery import poll_deliveries
    s = telemetry_case
    first = s.observation()
    early = first.model_copy(update={'source_record_id': 'early', 'sampled_at': datetime(2026, 1, 1, tzinfo=timezone.utc)})
    zero = first.model_copy(update={'source_record_id': 'zero', 'normalized_value': Decimal('0'), 'raw_value': '0'})
    unknown = first.model_copy(update={'source_record_id': 'unknown', 'normalized_value': None, 'raw_value': None, 'quality': 'unknown'})
    result = s.accept([first, early, zero, unknown])
    duplicate = s.accept([first])
    assert duplicate['accepted_count'] == 0
    with pytest.raises(ApiError, match='TELEMETRY_IDENTITY_CONFLICT'):
        s.accept([first.model_copy(update={'sampled_at': datetime(2026, 2, 15, tzinfo=timezone.utc)})])
    poll_deliveries(s.registry)
    rows = s.query(kind='observation', object_id=s.ids_by_name['object_a'])['items']
    assert len(rows) == 4
    values = {r['source_record_id']: r['normalized_value'] for r in rows}
    assert values == {'one': '12.345678901234567890', 'early': '12.345678901234567890', 'zero': '0', 'unknown': None}
    assert len(s.query(kind='observation', start=datetime(2026, 1, 15, tzinfo=timezone.utc),
        end=datetime(2026, 2, 1, tzinfo=timezone.utc))['items']) == 3
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 4
        assert c.execute(text('SELECT count(*) FROM telemetry_identities WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 4
        assert c.execute(text('SELECT DISTINCT tableoid::regclass::text FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 'telemetry_samples_202601'
    with s.transaction() as uow:
        assert s.telemetry(uow).status(s.actor('alice'), UUID(result['id']))['status'] == 'current'


def test_quality_selection_precedes_pagination_aggregate_and_cursor_identity(telemetry_case):
    from lzo.errors import ApiError
    from lzo.synchronization.delivery import poll_deliveries
    s = telemetry_case
    s.accept([s.observation(str(i), quality=quality) for i, quality in enumerate(
        ('valid', 'suspect', 'invalid', 'unknown', 'suspect'))])
    poll_deliveries(s.registry)
    for quality, count in (('valid', 1), ('suspect', 2), ('invalid', 1), ('unknown', 1)):
        page = s.query(quality=quality)
        assert len(page['items']) == page['aggregate']['count'] == count
        assert {row['quality'] for row in page['items']} == {quality}
    first = s.query(quality='suspect', limit=1)
    assert first['next_cursor'] and first['aggregate']['count'] == 1
    second = s.query(quality='suspect', limit=1, cursor=first['next_cursor'])
    assert len(second['items']) == 1 and second['items'][0]['id'] != first['items'][0]['id']
    with pytest.raises(ApiError, match='TELEMETRY_CURSOR_STALE'):
        s.query(quality='invalid', cursor=first['next_cursor'])
    with pytest.raises(ApiError, match='INVALID_FILTER'):
        s.query(quality='invented')
    with pytest.raises(ApiError, match='INVALID_FILTER'):
        s.query(quality='suspect', latest=True)
    assert s.query(quality='valid', latest=True)['items']


def test_correction_withdrawal_and_late_original_delivery_preserve_history(telemetry_case):
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply
    from lzo.telemetry.repository import apply_batch
    from lzo.telemetry.schemas import Supersedes
    s = telemetry_case
    base = s.observation().model_copy(update={'sampled_at': datetime(2026, 1, 31, 23, 59, 59, tzinfo=timezone.utc)})
    first = s.accept([base])
    corrected = base.model_copy(update={'version': 2, 'supersedes': Supersedes(source_record_id='one', version=1),
        'sampled_at': datetime(2026, 2, 1, tzinfo=timezone.utc), 'normalized_value': Decimal('13')})
    second = s.accept([corrected])
    with s.transaction() as uow:
        wires = {str(r['entity_id']): r['envelope'] for r in uow.session.execute(text('SELECT entity_id,envelope FROM store_deliveries WHERE target=\'timeseries\'')).mappings()}
    _apply(s.registry, SyncEnvelope.model_validate(wires[second['id']]), apply_batch)
    _apply(s.registry, SyncEnvelope.model_validate(wires[first['id']]), apply_batch)
    # A target commit without core ack remains explicitly pending.
    assert s.query(kind='observation')['status'] == 'pending'
    from lzo.synchronization.delivery import poll_deliveries
    poll_deliveries(s.registry)
    assert [r['version'] for r in s.query(kind='observation')['items']] == [2]
    withdrawn = corrected.model_copy(update={'version': 3, 'operation': 'withdraw',
        'supersedes': Supersedes(source_record_id='one', version=2)})
    s.accept([withdrawn])
    poll_deliveries(s.registry)
    assert s.query(kind='observation')['items'] == []
    assert {r['version'] for r in s.query(kind='observation', history=True)['items']} == {1, 2, 3}
    with s.registry.engine('timeseries').connect() as c:
        assert set(c.execute(text('SELECT DISTINCT tableoid::regclass::text FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalars()) == {'telemetry_samples_202601', 'telemetry_samples_202602'}
        c.execute(text("SET LOCAL TIME ZONE 'Asia/Shanghai'"))
        routed = c.execute(text("SELECT payload->>'version',tableoid::regclass::text FROM telemetry_samples WHERE source_id=:id ORDER BY payload->>'version'"), {'id': s.source.id}).all()
        assert routed == [('1', 'telemetry_samples_202601'), ('2', 'telemetry_samples_202602'), ('3', 'telemetry_samples_202602')]


def test_target_commit_ack_loss_replays_once_and_failure_can_retry_exact_envelope(telemetry_case, monkeypatch):
    from lzo.synchronization import delivery
    from lzo.telemetry.repository import apply_batch
    s = telemetry_case
    batch = s.accept([s.observation()])
    original = delivery._acknowledge
    monkeypatch.setattr(delivery, '_acknowledge', lambda *a: (_ for _ in ()).throw(ConnectionError()))
    assert delivery.deliver_once(s.registry, 'timeseries', apply_batch).failed == 1
    monkeypatch.setattr(delivery, '_acknowledge', original)
    with s.transaction() as uow:
        uow.session.execute(text("UPDATE store_deliveries SET status='failed',available_at=clock_timestamp() WHERE entity_id=:id"), {'id': batch['id']})
        uow.commit()
    assert s.query(kind='observation')['status'] == 'failed'
    with s.transaction() as uow:
        s.telemetry(uow).retry(s.actor('alice'), UUID(batch['id']))
        uow.commit()
    assert delivery.deliver_once(s.registry, 'timeseries', apply_batch).delivered == 1
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 1
        assert c.execute(text('SELECT count(*) FROM store_inbox WHERE event_id=:id'), {'id': batch['event_id']}).scalar_one() == 1
    assert s.query(kind='observation')['status'] == 'current'


def test_restricted_partition_maintenance_and_atomic_rollback(telemetry_case):
    from sqlalchemy.exc import DBAPIError
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply
    from lzo.telemetry.repository import apply_batch
    s = telemetry_case
    with s.registry.engine('timeseries').connect() as c:
        row = c.execute(text("SELECT has_function_privilege(current_user,'telemetry_ensure_month(date)','EXECUTE') AS app_maintenance, has_schema_privilege(current_user,'public','CREATE') AS app_ddl")).mappings().one()
        assert row == {'app_maintenance': False, 'app_ddl': False}
        assert c.execute(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname='lzo_timeseries_maintainer'")).scalar_one() is False
    for statement in ("SELECT public.telemetry_ensure_month(DATE '2026-05-01')", 'CREATE TABLE public.telemetry_app_escape(id integer)'):
        with s.registry.engine('timeseries').begin() as c:
            with pytest.raises(DBAPIError):
                c.execute(text(statement))
    batch = s.accept([s.observation().model_copy(update={'sampled_at': datetime(2099, 1, 1, tzinfo=timezone.utc)})])
    with s.transaction() as uow:
        wire = uow.session.execute(text('SELECT envelope FROM store_deliveries WHERE entity_id=:id'), {'id': batch['id']}).scalar_one()
    with pytest.raises(DBAPIError):
        _apply(s.registry, SyncEnvelope.model_validate(wire), apply_batch)
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT count(*) FROM telemetry_identities WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 0
        assert c.execute(text('SELECT count(*) FROM store_inbox WHERE event_id=:id'), {'id': batch['event_id']}).scalar_one() == 0


def test_current_linked_permission_filters_before_pagination_and_latest_valid(telemetry_case):
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.synchronization.delivery import poll_deliveries
    import io
    s = telemetry_case
    regional = s.grant('alice', 'editor', scope='farm_b')
    with s.transaction() as uow:
        store = get_blob_store(s.config)
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_b'],
            store.put(io.BytesIO(b'Private synthetic segment'), 'video/mp4'), filename='private.mp4', metadata={})
        uow.commit()
    hidden = s.observation('hidden').model_copy(update={'artifact_version_id': artifact.version_id})
    older = s.observation('older').model_copy(update={'sampled_at': datetime(2026, 1, 2, tzinfo=timezone.utc)})
    invalid = s.observation('invalid').model_copy(update={'sampled_at': datetime(2026, 2, 1, tzinfo=timezone.utc), 'quality': 'invalid'})
    s.accept([hidden, older, invalid])
    poll_deliveries(s.registry)
    s.revoke(regional)
    page = s.query(kind='observation', limit=1)
    assert len(page['items']) == 1 and page['items'][0]['source_record_id'] == 'older'
    assert page['next_cursor']
    next_page = s.query(kind='observation', limit=1, cursor=page['next_cursor'])
    assert next_page['items'][0]['source_record_id'] == 'invalid'
    latest = s.query(kind='observation', latest=True)
    assert [r['source_record_id'] for r in latest['items']] == ['older']
    assert latest['aggregate'] == {'count': 1, 'numeric_count': 1, 'min': '12.345678901234567890',
        'max': '12.345678901234567890', 'sum': '12.345678901234567890'}


def test_direct_target_changed_time_cannot_bypass_global_identity(telemetry_case):
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply, DeliveryConflict
    from lzo.telemetry.repository import apply_batch
    s = telemetry_case
    batch = s.accept([s.observation()])
    with s.transaction() as uow:
        wire = uow.session.execute(text('SELECT envelope FROM store_deliveries WHERE entity_id=:id'), {'id': batch['id']}).scalar_one()
    original = SyncEnvelope.model_validate(wire)
    _apply(s.registry, original, apply_batch)
    changed = original.payload
    changed['records'][0]['sampled_at'] = '2026-02-15T12:00:00+00:00'
    forged = SyncEnvelope(event_id=uuid4(), entity_id=uuid4(), aggregate_revision=1, target='timeseries',
        operation='telemetry.batch', payload=changed, data_nature='synthetic')
    with pytest.raises(DeliveryConflict):
        _apply(s.registry, forged, apply_batch)
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 1


def test_static_input_permission_and_batch_write_guard_are_current(telemetry_case):
    import io
    import json
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.telemetry.schemas import BatchInput
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.errors import ApiError
    s = telemetry_case
    regional = s.grant('alice', 'editor', scope='farm_b')
    store = get_blob_store(s.config)
    with s.transaction() as uow:
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_b'],
            store.put(io.BytesIO(json.dumps([s.observation().model_dump(mode='json')]).encode()), 'application/json'), filename='static.json', metadata={})
        batch = s.telemetry(uow).accept(s.actor('alice'), BatchInput(source_id=s.source.id,
            adapter='json-v1', input_artifact_version_id=artifact.version_id, records=(s.observation(),)), 'file-batch')
        uow.commit()
    poll_deliveries(s.registry)
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='TELEMETRY_COMMAND_REQUIRED'):
            ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), UUID(batch['id']),
                store.put(io.BytesIO(b'Bypass'), 'text/plain'), filename='bypass.txt', metadata={})
    s.revoke(regional)
    assert s.query()['items'] == []


def test_latest_preserves_independent_sources_and_partition_account_is_restricted(telemetry_case):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.schemas import SourceCreate
    from lzo.telemetry.schemas import BatchInput
    from lzo.synchronization.delivery import poll_deliveries
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    s = telemetry_case
    s.accept([s.observation()])
    with s.transaction() as uow:
        source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='independent',
            source_instance=uuid4().hex, collection='observations', kind='file', scope=s.scope, data_nature='synthetic'))
        s.telemetry(uow).accept(s.actor('alice'), BatchInput(source_id=source.id,
            records=(s.observation().model_copy(update={'raw_value': '99', 'normalized_value': Decimal('99')}),)), 'independent')
        uow.commit()
    poll_deliveries(s.registry)
    assert len(s.query(latest=True)['items']) == 2
    maintenance = create_engine(os.environ['LZO_TIMESERIES_MAINTENANCE_DATABASE_URL'])
    try:
        with maintenance.begin() as c:
            assert c.execute(text('SELECT current_user')).scalar_one() == 'lzo_timeseries_maintainer'
            assert c.execute(text("SELECT public.telemetry_ensure_month(DATE '2026-03-01')")).scalar_one() == 'telemetry_samples_202603'
        for statement in ('CREATE TABLE public.maintenance_escape(id integer)', 'SELECT * FROM telemetry_samples',
                "SELECT public.telemetry_ensure_month(DATE '2200-01-01')"):
            with maintenance.begin() as c:
                with pytest.raises(DBAPIError):
                    c.execute(text(statement))
    finally:
        maintenance.dispose()


def test_http_authenticated_manifest_query_history_retry_and_revocation(telemetry_case):
    from fastapi.testclient import TestClient
    from lzo.auth.session import SessionService, SESSION_COOKIE, digest
    from lzo.auth.models import sessions
    from lzo.main import create_app
    from lzo.telemetry.schemas import BatchInput
    from lzo.synchronization.delivery import poll_deliveries
    s = telemetry_case
    app = create_app(s.config)
    app.state.storage_registry.close()
    app.state.engine = s.app
    app.state.storage_registry = s.registry
    with s.transaction() as uow:
        cookie = SessionService(uow, s.config).create(s.users['alice'], {'synthetic': True})
        csrf = uow.session.execute(select(sessions.c.csrf_token).where(sessions.c.id_hash == digest(cookie))).scalar_one()
        uow.commit()
    headers = {'Cookie': f'{SESSION_COOKIE}={cookie}', 'X-CSRF-Token': csrf, 'Origin': s.config.public_origin, 'Idempotency-Key': 'manifest'}
    with TestClient(app, base_url='https://testserver') as client:
        command = BatchInput(source_id=s.source.id, records=(s.observation(),)).model_dump(mode='json')
        assert client.post('/api/v1/telemetry/batches', json=command).status_code == 401
        created = client.post('/api/v1/telemetry/batches', json=command, headers=headers)
        assert created.status_code == 202, created.text
        assert client.post('/api/v1/telemetry/batches', json=command, headers=headers).json()['id'] == created.json()['id']
        identifier = created.json()['id']
        poll_deliveries(s.registry)
        result = client.get('/api/v1/telemetry/observations', headers=headers)
        assert result.status_code == 200, result.text
        assert result.json()['items'][0]['normalized_value'] == '12.345678901234567890'
        assert client.get('/api/v1/telemetry/batches/'+identifier, headers=headers).json()['status'] == 'current'
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET status='failed' WHERE entity_id=:id"), {'id': identifier})
            uow.commit()
        retry_headers = {**headers, 'Idempotency-Key': 'retry-once'}
        assert client.post('/api/v1/telemetry/batches/'+identifier+'/retry', headers=retry_headers).status_code == 202
        assert client.post('/api/v1/telemetry/batches/'+identifier+'/retry', headers=retry_headers).status_code == 202
        capabilities = client.get('/api/v1/telemetry/capabilities', headers=headers).json()
        assert all(value['control'] is False and value['live_connectivity'] is False for value in capabilities.values())
        assert client.get('/api/v1/telemetry/observations?cursor=malformed', headers=headers).status_code == 422
        s.revoke(s.writer)
        assert client.get('/api/v1/telemetry/batches/'+identifier, headers=headers).status_code in (403, 404)
        assert client.get('/api/v1/telemetry/observations', headers=headers).json()['items'] == []


def test_bounded_burst_uses_one_batch_revision_and_latest_is_one_stream(telemetry_case):
    import time
    import json
    from datetime import timedelta
    from lzo.synchronization.delivery import deliver_once
    from lzo.telemetry.repository import apply_batch
    s = telemetry_case
    base = s.observation()
    start = time.perf_counter()
    rows = [base.model_copy(update={'source_record_id': 'burst-'+str(n), 'sampled_at': base.sampled_at+timedelta(seconds=n),
        'raw_value': str(n), 'normalized_value': Decimal(n)}) for n in range(128)]
    batch = s.accept(rows)
    accepted = time.perf_counter()
    assert deliver_once(s.registry, 'timeseries', apply_batch).delivered == 1
    delivered = time.perf_counter()
    latest = s.query(latest=True)
    assert len(latest['items']) == 1 and latest['items'][0]['source_record_id'] == 'burst-127'
    assert latest['items'][0]['normalized_value'] == '127'
    with s.transaction() as uow:
        assert uow.session.execute(text('SELECT count(*) FROM revisions WHERE resource_id=:id'), {'id': batch['id']}).scalar_one() == 1
        assert not uow.session.execute(text("SELECT 1 FROM jobs WHERE kind='rdf.project' AND payload->>'resource_id'=:id"), {'id': batch['id']}).first()
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == 128
        plan = c.execute(text("EXPLAIN (FORMAT JSON) SELECT id FROM telemetry_samples WHERE sampled_at>=TIMESTAMPTZ '2026-01-01 UTC' AND sampled_at<TIMESTAMPTZ '2026-02-01 UTC'")).scalar_one()
        serialized = json.dumps(plan)
        assert 'telemetry_samples_202601' in serialized and 'telemetry_samples_202602' not in serialized
    print(json.dumps({'synthetic_records': 128, 'batches': 1, 'sample_step_seconds': 1,
        'accept_seconds': round(accepted-start, 4), 'delivery_seconds': round(delivered-accepted, 4),
        'latest_query_seconds': round(time.perf_counter()-delivered, 4)}))


def test_media_timeline_intersects_segment_and_filters_channel(telemetry_case):
    import io
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.synchronization.delivery import poll_deliveries
    s = telemetry_case
    with s.transaction() as uow:
        store = get_blob_store(s.config)
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
            store.put(io.BytesIO(b'Synthetic non-playable segment'), 'application/octet-stream'), filename='segment.bin', metadata={'synthetic': True})
        uow.commit()
    s.accept([s.observation().model_copy(update={'kind': 'media', 'channel_id': 'camera-1',
        'artifact_version_id': artifact.version_id, 'end_at': datetime(2026, 1, 15, 13, tzinfo=timezone.utc)})])
    poll_deliveries(s.registry)
    query = dict(kind='media', start=datetime(2026, 1, 15, 12, 30, tzinfo=timezone.utc), end=datetime(2026, 1, 15, 12, 45, tzinfo=timezone.utc))
    assert len(s.query(**query)['items']) == 1
    assert len(s.query(**query, channel_id='camera-1')['items']) == 1
    assert s.query(**query, channel_id='other')['items'] == []


def test_pending_withdrawal_cannot_return_superseded_latest(telemetry_case):
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.telemetry.schemas import Supersedes
    s = telemetry_case
    base = s.observation()
    s.accept([base])
    poll_deliveries(s.registry)
    s.accept([base.model_copy(update={'version': 2, 'operation': 'withdraw',
        'supersedes': Supersedes(source_record_id='one', version=1)})])
    result = s.query(latest=True)
    assert result['status'] == 'pending'
    assert result['items'] == []
    assert len(s.query(history=True)['items']) == 1


def test_forged_static_manifest_cannot_claim_unparsed_private_bytes(telemetry_case):
    import io
    import json
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.telemetry.schemas import BatchInput
    from lzo.errors import ApiError
    s = telemetry_case
    with s.transaction() as uow:
        store = get_blob_store(s.config)
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
            store.put(io.BytesIO(json.dumps([s.observation().model_dump(mode='json')]).encode()), 'application/json'),
            filename='source.json', metadata={})
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='TELEMETRY_PARSE_BINDING_MISMATCH'):
            s.telemetry(uow).accept(s.actor('alice'), BatchInput(source_id=s.source.id, adapter='json-v1',
                input_artifact_version_id=artifact.version_id,
                records=(s.observation().model_copy(update={'raw_value': 'forged'}),)), 'forged')
    for adapter, artifact_id, code in (('incremental-v1', artifact.version_id, 'TELEMETRY_PARSE_BINDING_MISMATCH'),
            ('json-v1', None, 'TELEMETRY_PARSE_BINDING_MISMATCH'),
            ('geojson-v1', artifact.version_id, 'TELEMETRY_PARSE_FAILED')):
        with s.transaction() as uow:
            with pytest.raises(ApiError, match=code):
                s.telemetry(uow).accept(s.actor('alice'), BatchInput(source_id=s.source.id, adapter=adapter,
                    input_artifact_version_id=artifact_id, records=(s.observation(),)), str(uuid4()))
