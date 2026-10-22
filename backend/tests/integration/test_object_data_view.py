"""Synthetic field/pond/orchard scenarios on real core, GIS, TS, catalog, S3 and Fuseki."""
import csv
import io
import json
import os
from datetime import datetime, timezone, timedelta
from pathlib import Path
from uuid import UUID, uuid4
import pytest
from sqlalchemy import create_engine, select, text, update
from test_timeseries import telemetry_case
from test_catalog import catalog_case


def _route_client(s):
    """Actual session/CSRF authority, no request/authentication doubles."""
    from fastapi.testclient import TestClient
    from lzo.main import create_app
    from lzo.auth.session import SessionService, SESSION_COOKIE
    app = create_app(s.config)
    app.state.storage_registry.close()
    app.state.storage_registry = s.registry
    app.state.engine = s.app
    with s.transaction() as uow:
        cookie = SessionService(uow, s.config).create(s.users['alice'], {'synthetic': True})
        uow.commit()
    client = TestClient(app, base_url='https://testserver')
    client.cookies.set(SESSION_COOKIE, cookie)
    return client


def _install_acknowledged_history(s, count, streams):
    """Bulk fixture installation, not an admission throughput benchmark.

    Each <=1000-row fixture manifest has a real ledger ref, frozen delivery,
    core identity rows and actual normal target-handler/inbox acknowledgement.
    Per-row admission authorization is already covered by telemetry_case tests.
    """
    from sqlalchemy import insert
    from lzo.contracts import ResourceScope
    from lzo.governance.revisions import append_revision, lock_ledger
    from lzo.governance.risk import digest_change
    from lzo.ingestion.sources import add_scoped, bind
    from lzo.telemetry.service import record_content, record_id
    from lzo.telemetry.repository import batches, identities, apply_batch
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import enqueue_delivery, deliver_once
    base = s.observation()
    namespace = {'source_id': str(s.source.id), 'source_system': 'synthetic',
        'source_instance': s.source.configuration.source_instance, 'collection': 'field-pond-orchard'}
    received = datetime.now(timezone.utc)
    for offset in range(0, count, 1000):
        rows, keys = [], []
        for n in range(offset, min(count, offset+1000)):
            record = base.model_copy(update={'source_record_id': 'retained-'+str(n), 'metric_id': 'metric-'+str(n % streams),
                'sampled_at': base.sampled_at+timedelta(seconds=n)})
            content = record_content(record)
            identifier = record_id(s.source.id, record.source_record_id, 1)
            digest = digest_change(content)
            rows.append(content | {'id': str(identifier), 'content_hash': digest, 'received_at': received.isoformat(),
                'source_namespace': namespace, 'supersedes_id': None})
            keys.append(dict(source_id=s.source.id, source_record_id=record.source_record_id, version=1,
                id=identifier, object_id=record.object_id, content_hash=digest, received_at=received))
        identifier = uuid4()
        manifest_hash = digest_change(rows)
        with s.transaction() as uow:
            lock_ledger(uow)
            add_scoped(uow, s.actor('alice'), identifier, s.scope, 'telemetry.batch', 'source.manage')
            ref = append_revision(uow, s.actor('alice'), identifier, 0, {'source_id': str(s.source.id),
                'manifest_hash': manifest_hash, 'count': len(rows), 'accepted_count': len(rows), 'adapter': 'incremental-v1'}, 'telemetry.batch.accepted')
            bind(uow, ref, {s.source.id, base.object_id})
            uow.session.execute(insert(batches).values(id=identifier, source_id=s.source.id, manifest_hash=manifest_hash, event_id=ref.event_id))
            uow.session.execute(insert(identities), keys)
            enqueue_delivery(uow, SyncEnvelope(event_id=ref.event_id, entity_id=identifier, aggregate_revision=1,
                target='timeseries', operation='telemetry.batch', payload={'source_id': str(s.source.id), 'manifest_hash': manifest_hash,
                    'records': rows, 'input_artifact_version_id': None, 'mapping_version_id': None}, data_nature='synthetic'))
            uow.commit()
    assert deliver_once(s.registry, 'timeseries', apply_batch).delivered == (count+999)//1000
    with s.registry.engine('timeseries').connect() as connection:
        assert connection.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': s.source.id}).scalar_one() == count


@pytest.mark.parametrize('count,streams,selection', [(10001, 1, {'observations_start': '2026-01-15T14:46:39Z'}),
    (101, 101, {'observations_metric_id': 'metric-100'})])
def test_object_route_keeps_parts_and_offers_bounded_latest_after_budget(telemetry_case, count, streams, selection):
    s = telemetry_case
    _install_acknowledged_history(s, count, streams)
    identifier = s.ids_by_name['object_a']
    with _route_client(s) as client:
        response = client.get('/api/v1/objects/'+str(identifier)+'/data')
        assert response.status_code == 200, response.text
        result = response.json()
        assert result['identity']['id'] == str(identifier)
        assert result['gis']['status'] == 'pending'
        assert result['catalog']['status'] == 'current'
        assert result['media']['status'] == 'current' and result['tracks']['status'] == 'current'
        assert result['observations']['status'] == 'incomplete'
        assert result['observations']['incomplete_reason'] == 'query_budget'
        assert result['observations']['query_url'].startswith('/api/v1/telemetry/observations?')
        recovered = client.get(result['observations']['query_url'])
        assert recovered.status_code == 200, recovered.text
        assert 1 <= len(recovered.json()['items']) <= 50
        bounded = client.get('/api/v1/objects/'+str(identifier)+'/data', params=selection)
        assert bounded.status_code == 200, bounded.text
        page = bounded.json()['observations']
        assert page['status'] == 'current' and len(page['items']) == 1
        assert page['items'][0]['source_record_id'] == 'retained-'+str(count-1)
        assert client.get(page['query_url']).status_code == 200


def test_selected_telemetry_reference_waits_for_exact_receipt_and_keeps_authority_after_retry(object_case):
    from lzo.auth.models import roles
    from lzo.contracts import ResourceScope, RevisionRef
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.schemas import SourceCreate
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.blobs import get_blob_store
    from lzo.artifacts.service import ArtifactService
    from lzo.telemetry.schemas import BatchInput, ObservationInput
    from lzo.telemetry.service import TelemetryService
    from lzo.telemetry.repository import apply_batch
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization import delivery
    from lzo.gis.repository import apply_geometry
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    from lzo.errors import ApiError
    s = object_case
    created = s.create_object()
    assert created.status_code == 201, created.text
    identifier = UUID(created.json()['revision']['resource_id'])
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=['view', 'edit', 'source.manage', 'source.read']))
        uow.commit()
    regional = s.grant('alice', 'editor', scope='farm_b')
    registry = StorageRegistry.from_settings(s.config)
    registry._engines['core'].dispose()
    registry._engines['core'] = s.app
    try:
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='synthetic', source_instance=uuid4().hex,
                collection='selected-summary', kind='file', scope=ResourceScope(None, s.scopes['farm_a'], (), 'data'), data_nature='synthetic'))
            store = get_blob_store(s.config)
            artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_b'],
                store.put(io.BytesIO(b'Original authored synthetic event reference'), 'text/plain'), filename='event.txt', metadata={'synthetic': True})
            event = ObservationInput(kind='event', event_type='synthetic.pump.stopped', source_record_id='selected-event',
                object_id=identifier, device_id=identifier, point_id='synthetic-pump', raw_value='pump stopped', normalized_value=None,
                sampled_at='2026-01-15T12:00:00Z', data_nature='synthetic', artifact_version_id=artifact.version_id)
            service = TelemetryService(uow, registry)
            selected = service.accept(s.actor('alice'), BatchInput(source_id=source.id, records=(event,)), 'selected')
            unrelated = service.accept(s.actor('alice'), BatchInput(source_id=source.id,
                records=(event.model_copy(update={'source_record_id': 'unrelated-event'}),)), 'unrelated')
            uow.commit()
        # A retry audit becomes the head, while the selected provenance still
        # pins the original immutable acceptance revision/event and its links.
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            uow.session.execute(text("UPDATE store_deliveries SET status='failed' WHERE entity_id=:id"), {'id': selected['id']})
            TelemetryService(uow, registry).retry(s.actor('alice'), UUID(selected['id']), 'selected-repair')
            wire = uow.session.execute(text("SELECT envelope FROM store_deliveries WHERE event_id=:id AND target='timeseries'"),
                {'id': selected['event_id']}).scalar_one()
            assert uow.session.execute(text('SELECT revision FROM resource_heads WHERE resource_id=:id'), {'id': selected['id']}).scalar_one() == 2
            uow.commit()
        envelope = SyncEnvelope.model_validate(wire)
        record = envelope.payload['records'][0]
        pin = {'resource_id': selected['id'], 'revision': 1, 'event_id': selected['event_id']}
        summary = s.assertion(identifier, value='Selected synthetic event: pump stopped')
        summary['support'].update(record_key=record['source_record_id'], source_version='1', observed_at=record['sampled_at'],
            evidence_refs=[pin], raw_value={'source_id': str(source.id), 'record_id': record['id'], 'content_hash': record['content_hash'], 'raw_value': record['raw_value']})
        s.login('alice')
        response = s.patch(identifier, 1, additions=[summary])
        assert response.status_code == 200, response.text
        value = response.json()['revision']
        ref = RevisionRef(UUID(value['resource_id']), value['revision'], UUID(value['event_id']))
        delivery.deliver_once(registry, 'gis', apply_geometry)
        repository = FusekiRepository(s.config.fuseki_url)
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            with pytest.raises(delivery.DeliveriesPending):
                ProjectionService(uow, repository, actor=s.actor('alice')).project_revision(ref)
        assert all(triple.object.value != 'Selected synthetic event: pump stopped' for triple in repository.read_graph(graph_iri(identifier, 'accepted')))
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET status='failed' WHERE event_id=:id AND target='timeseries'"),
                {'id': selected['event_id']})
            uow.commit()
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            with pytest.raises(delivery.DeliveriesFailed):
                ProjectionService(uow, repository, actor=s.actor('alice')).project_revision(ref)
            TelemetryService(uow, registry).retry(s.actor('alice'), UUID(selected['id']), 'selected-repair-again')
            uow.commit()
        receipt = delivery._apply(registry, envelope, apply_batch)
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            with pytest.raises(delivery.DeliveriesPending):
                ProjectionService(uow, repository, actor=s.actor('alice')).project_revision(ref)
        with registry.engine('core').begin() as connection:
            delivery._acknowledge(connection, receipt)
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            assert ProjectionService(uow, repository, actor=s.actor('alice')).project_revision(ref).state == 'synced'
            assert uow.session.execute(text("SELECT status FROM store_deliveries WHERE event_id=:id AND target='timeseries'"),
                {'id': unrelated['event_id']}).scalar_one() == 'pending'
            snapshot = uow.session.execute(text('SELECT payload FROM revisions WHERE event_id=:id'), {'id': ref.event_id}).scalar_one()
            assert pin in snapshot['dependency_revisions']
            stored = next(row for row in snapshot['assertions'] if str(row['id']) == summary['assertion_id'])['supports'][0]['payload']
            assert stored['record_key'] == 'selected-event' and stored['raw_value']['content_hash'] == record['content_hash']
            assert uow.session.execute(text('SELECT 1 FROM semantic_object_dependencies WHERE resource_id=:id AND dependency_id=:private'),
                {'id': identifier, 'private': artifact.version_id}).first()
            uow.commit()
        assert any(triple.predicate_iri == 'urn:note' and triple.object.value == 'Selected synthetic event: pump stopped'
            for triple in repository.read_graph(graph_iri(identifier, 'accepted')))
        assert s.client.get('/api/v1/objects/'+str(identifier)).status_code == 200
        s.revoke(regional)
        s.login('alice')
        assert s.client.get('/api/v1/objects/'+str(identifier)).status_code == 404
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            with pytest.raises(ApiError):
                ProjectionService(uow, repository, actor=s.actor('alice')).project_revision(ref)
    finally:
        registry._engines.pop('core')
        registry.close()


def test_object_catalog_filter_fills_relevant_document_page(catalog_case):
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.catalog.schemas import CreateDocument, DocumentMetadata
    s = catalog_case
    with s.transaction() as uow:
        store = get_blob_store(s.config)
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
            store.put(io.BytesIO(b'Original authored synthetic reference'), 'text/plain'), filename='reference.txt', metadata={})
        docs = []
        for name in ('unrelated-1', 'unrelated-2', 'related'):
            docs.append(s.service(uow).create_document(s.actor('alice'), CreateDocument(scope=s.scope,
                metadata=DocumentMetadata(title=name, document_type='reference', artifact_version_id=artifact.version_id,
                    source_uri='urn:test:synthetic-reference', source_version='1', license='Original authored fixture',
                    entity_ids=(s.ids_by_name['object_a'],) if name == 'related' else ())), str(uuid4())))
        page = s.service(uow).list(s.actor('alice'), 'document', limit=1, entity_id=s.ids_by_name['object_a'])
        assert len(page.items) == 1 and page.items[0].id == docs[2].id
        assert page.next_cursor is None
        uow.commit()


def test_object_catalog_preserves_failure_when_another_reference_is_pending(catalog_case):
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.catalog.schemas import CreateDocument, DocumentMetadata, DeviceBinding
    from lzo.storage.object_view import ObjectDataService
    s = catalog_case
    model = s.create()
    with s.transaction() as uow:
        service = s.service(uow)
        service.bind_device(s.actor('alice'), model.id,
            DeviceBinding(entity_id=s.ids_by_name['object_a'], expected_revision=0), 'bind')
        store = get_blob_store(s.config)
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
            store.put(io.BytesIO(b'Original authored synthetic pending reference'), 'text/plain'), filename='reference.txt', metadata={})
        service.create_document(s.actor('alice'), CreateDocument(scope=s.scope, metadata=DocumentMetadata(title='Pending reference',
            document_type='reference', artifact_version_id=artifact.version_id, source_uri='urn:test:synthetic-reference', source_version='1',
            license='Original authored fixture', entity_ids=(s.ids_by_name['object_a'],))), 'doc')
        uow.session.execute(text("UPDATE store_deliveries SET status='failed' WHERE event_id=:id"), {'id': model.revision.event_id})
        value = ObjectDataService(uow, s.registry).get(s.actor('alice'), s.ids_by_name['object_a'])
        assert value['catalog']['models'][0]['projection_status'] == 'failed'
        assert value['catalog']['documents'][0]['projection_status'] == 'pending'
        assert value['catalog']['status'] == 'failed'


def test_historical_binding_never_substitutes_current_model(telemetry_case):
    from lzo.catalog.service import CatalogService
    from lzo.catalog.schemas import CreateModel, ModelMetadata, DeviceBinding
    from lzo.telemetry.schemas import BatchInput
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.telemetry.maintenance import maintain_partitions
    from lzo.errors import ApiError
    s = telemetry_case
    migrate = __import__('lzo.storage.migrate', fromlist=['migrate_domain']).migrate_domain
    migrate('catalog', s.config.catalog_migration_database_url, core_url=s.config.database_url)
    models = []
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        service = CatalogService(uow, s.registry)
        for name in ('Original camera', 'Replacement camera'):
            models.append(service.create_model(s.actor('alice'), CreateModel(scope=s.scope,
                metadata=ModelMetadata(exact_model=name, synthetic=True)), str(uuid4())))
        binding = service.bind_device(s.actor('alice'), models[0].id,
            DeviceBinding(entity_id=s.ids_by_name['object_a'], expected_revision=0), str(uuid4()))
        uow.commit()
    with s.transaction() as uow:
        captured = uow.session.execute(text('SELECT clock_timestamp()')).scalar_one()
    first_month = captured.date().replace(day=1)
    last_month = (first_month.replace(day=28)+timedelta(days=4)).replace(day=1)
    maintain_partitions(os.environ['LZO_TIMESERIES_MAINTENANCE_DATABASE_URL'], first_month, last_month)
    record = s.observation('historic').model_copy(update={'sampled_at': captured,
        'device_binding_ref': binding.revision, 'model_ref': models[0].revision})
    s.accept([record])
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        CatalogService(uow, s.registry).bind_device(s.actor('alice'), models[1].id,
            DeviceBinding(entity_id=s.ids_by_name['object_a'], expected_revision=1), str(uuid4()))
        uow.commit()
    poll_deliveries(s.registry)
    assert s.query()['items'][0]['model_ref']['resource_id'] == str(models[0].id)
    with pytest.raises(ApiError, match='TELEMETRY_BINDING_TIME_MISMATCH'):
        s.accept([record.model_copy(update={'source_record_id': 'wrong-time', 'sampled_at': datetime.now(timezone.utc)+timedelta(seconds=1)})])


@pytest.mark.parametrize('provider', ['local', 's3'])
def test_field_pond_orchard_static_incremental_media_track_and_partial_object_view(object_case, provider, tmp_path):
    from dotenv import dotenv_values
    from lzo.auth.models import roles
    from lzo.catalog.service import CatalogService
    from lzo.catalog.schemas import CreateModel, ModelMetadata, DeviceBinding, CreateDocument, DocumentMetadata
    from lzo.contracts import ResourceScope
    from lzo.ingestion.schemas import SourceCreate, EvidenceCreate
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.evidence import EvidenceService
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.migrate import migrate_domain
    from lzo.telemetry.maintenance import maintain_partitions
    from lzo.telemetry.service import TelemetryService
    from lzo.telemetry.schemas import BatchInput, ObservationInput, Supersedes, FileReplayInput
    from lzo.telemetry.replay import accept_file
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.storage.object_view import ObjectDataService
    s = object_case
    identifiers, geometry_refs = [], []
    for label in ('field', 'pond', 'orchard'):
        created = s.create_object()
        assert created.status_code == 201, created.text
        identifier = UUID(created.json()['revision']['resource_id'])
        identifiers.append(identifier)
        body = {'bundle_id': str(s.bundle_id), 'geometry': {'resource_id': str(identifier), 'expected_revision': 1,
            'source_crs': 'EPSG:4326', 'geojson': {'type': 'Point', 'coordinates': [114.5, 30.2]},
            'source_support': s.assertion(identifier)['support'], 'decision_ref': None}}
        response = s.client.put('/api/v1/geometry/'+str(identifier), headers=s.headers(1), json=body)
        assert response.status_code == 200, response.text
        from lzo.contracts import RevisionRef
        geometry_refs.append(RevisionRef(**{**response.json(), 'resource_id': UUID(response.json()['resource_id']),
            'event_id': UUID(response.json()['event_id'])}))
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=[
            'view', 'edit', 'source.manage', 'source.read', 'catalog.manage', 'evidence.manage', 'evidence.read']))
        uow.commit()
    if provider == 's3':
        env = dotenv_values('.runtime/object-store/services.env')
        assert env, 'Actual owned S3 is required'
        s.config = s.config.model_copy(update={'blob_provider': 's3', 's3_endpoint': env['LZO_S3_ENDPOINT'],
            's3_bucket': env['LZO_S3_BUCKET'], 's3_access_key_file': Path(env['LZO_S3_ACCESS_KEY_FILE']),
            's3_secret_key_file': Path(env['LZO_S3_SECRET_KEY_FILE']), 'blob_staging_path': tmp_path/'staging'})
    s.client.app.state.settings = s.config
    registry = StorageRegistry.from_settings(s.config)
    registry._engines['core'].dispose()
    registry._engines['core'] = s.app
    for domain in ('timeseries', 'catalog'):
        migrate_domain(domain, getattr(s.config, domain+'_migration_database_url'), core_url=s.config.database_url)
    maintain_partitions(os.environ['LZO_TIMESERIES_MAINTENANCE_DATABASE_URL'], '2026-01-01', '2027-01-01')
    scope = ResourceScope(None, s.scopes['farm_a'], (), 'data')
    store = get_blob_store(s.config)
    try:
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='synthetic', source_instance=uuid4().hex,
                collection='field-pond-orchard', kind='file', scope=scope, data_nature='synthetic'))
            # Original authored bytes are synthetic evidence/segment placeholders,
            # not camera footage or a genuine flight dataset.
            evidence = EvidenceService(uow, store, s.config).create(uow, s.actor('alice'), io.BytesIO(b'Synthetic conflicting thermometer bench note'),
                'text/plain', EvidenceCreate(scope=scope, source_id=source.id, target_refs=(), title='Synthetic conflicting evidence',
                    locator='fixture:bench', data_nature='synthetic', replaces_id=None))
            media = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
                store.put(io.BytesIO(b'SYNTHETIC non-playable camera/drone media placeholder'), 'application/octet-stream'), filename='synthetic-segment.bin', metadata={'synthetic': True})
            models = [CatalogService(uow, registry).create_model(s.actor('alice'), CreateModel(scope=scope,
                metadata=ModelMetadata(exact_model='SYNTHETIC '+device, synthetic=True)), str(uuid4())) for device in ('camera', 'pump', 'drone')]
            reference_bytes = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
                store.put(io.BytesIO(b'Original authored synthetic camera/pump/drone fixture notes; hardware fields unknown'), 'text/plain'),
                filename='synthetic-reference.txt', metadata={'synthetic': True})
            document = CatalogService(uow, registry).create_document(s.actor('alice'), CreateDocument(scope=scope,
                metadata=DocumentMetadata(title='Synthetic telemetry reference', document_type='reference',
                    artifact_version_id=reference_bytes.version_id, source_uri='urn:lzo:synthetic:telemetry-reference', source_version='1',
                    license='Original authored synthetic fixture', model_ids=tuple(model.id for model in models), entity_ids=tuple(identifiers))), str(uuid4()))
            bindings = [CatalogService(uow, registry).bind_device(s.actor('alice'), model.id,
                DeviceBinding(entity_id=identifier, expected_revision=0), str(uuid4())) for model, identifier in zip(models, identifiers)]
            captured = uow.session.execute(text('SELECT clock_timestamp()')).scalar_one()
            uow.commit()
        records = []
        for label, identifier, model, binding, geometry_ref in zip(('field', 'pond', 'orchard'), identifiers, models, bindings, geometry_refs):
            records.append(ObservationInput(source_record_id=label+'-sample', object_id=identifier, device_id=identifier,
                point_id=label+'-point', metric_id='temperature', raw_value='23.50', raw_unit='Cel', normalized_value='23.50',
                normalized_unit='Cel', sampled_at=captured, reported_at=captured+timedelta(seconds=2), data_nature='synthetic',
                evidence_version_ids=(evidence.id,), model_ref=model.revision, device_binding_ref=binding.revision, location_ref=geometry_ref))
        def service(uow):
            uow.ontology_settings = s.config
            return TelemetryService(uow, registry)
        # Each actual registered static format enters the same acceptance path.
        for format, record in zip(('csv', 'json', 'geojson'), records):
            row = record.model_dump(mode='json')
            if format == 'csv':
                output = io.StringIO()
                writer = csv.DictWriter(output, fieldnames=list(row))
                writer.writeheader()
                writer.writerow({k: json.dumps(v) if v is None or isinstance(v, (dict, list)) else str(v) for k, v in row.items()})
                payload = output.getvalue().encode()
            else:
                payload = json.dumps([row] if format == 'json' else {'type': 'FeatureCollection',
                    'features': [{'type': 'Feature', 'properties': row, 'geometry': None}]}).encode()
            with s.transaction() as uow:
                artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
                    store.put(io.BytesIO(payload), 'text/plain'), filename='synthetic.'+format, metadata={'synthetic': True})
                result = accept_file(service(uow), s.actor('alice'), FileReplayInput(source_id=source.id,
                    artifact_version_id=artifact.version_id, format=format), str(uuid4()))
                assert result['accepted_count'] == 1
                uow.commit()
        poll_deliveries(registry)
        increments = [records[0].model_copy(update={'source_record_id': 'camera-channel', 'kind': 'media',
            'artifact_version_id': media.version_id, 'channel_id': 'synthetic-1', 'end_at': captured+timedelta(seconds=30)}),
            records[2].model_copy(update={'source_record_id': 'drone-track', 'kind': 'track', 'mission_id': 'synthetic-mission',
                'position': __import__('lzo.gis.schemas', fromlist=['PositionInput']).PositionInput(observed_at=captured,
                    horizontal_datum='EPSG:4326', longitude=114.5, latitude=30.2)}),
            records[1].model_copy(update={'source_record_id': 'pump-alarm', 'kind': 'event', 'event_type': 'synthetic.pump.alarm'})]
        with s.transaction() as uow:
            result = service(uow).accept(s.actor('alice'), BatchInput(source_id=source.id, records=tuple(increments)), str(uuid4()))
            uow.commit()
        poll_deliveries(registry)
        with s.transaction() as uow:
            svc = service(uow)
            view = ObjectDataService(uow, registry).get(s.actor('alice'), identifiers[0])
            assert view['gis']['status'] == 'current' and view['gis']['geometry'] is not None
            assert view['catalog']['models'][0]['id'] == str(models[0].id)
            assert view['catalog']['documents'][0]['id'] == str(document.id)
            assert view['observations']['items'][0]['evidence_version_ids'] == [str(evidence.id)]
            assert view['media']['items'][0]['artifact_version_id'] == str(media.version_id)
            assert svc.query(s.actor('alice'), kind='track')['items'][0]['position']['height_metres'] is None
            assert svc.query(s.actor('alice'), kind='event')['items'][0]['event_type'] == 'synthetic.pump.alarm'
            assert svc.retention_preview(s.actor('alice'), identifiers[0], captured+timedelta(days=1))['automatic_deletion'] is False
            from lzo.errors import ApiError
            with pytest.raises(ApiError, match='TELEMETRY_IDENTITY_CONFLICT'):
                svc.accept(s.actor('alice'), BatchInput(source_id=source.id, records=(records[1].model_copy(update={'raw_value': '99'}),)), str(uuid4()))
        corrected = records[1].model_copy(update={'version': 2, 'supersedes': Supersedes(source_record_id='pond-sample', version=1),
            'normalized_value': __import__('decimal').Decimal('24.25')})
        withdrawn = corrected.model_copy(update={'version': 3, 'operation': 'withdraw', 'supersedes': Supersedes(source_record_id='pond-sample', version=2)})
        for record in (corrected, withdrawn):
            with s.transaction() as uow:
                service(uow).accept(s.actor('alice'), BatchInput(source_id=source.id, records=(record,)), str(uuid4()))
                uow.commit()
        poll_deliveries(registry)
        with s.transaction() as uow:
            assert service(uow).query(s.actor('alice'), object_id=identifiers[1])['items'] == []
            assert len(service(uow).query(s.actor('alice'), object_id=identifiers[1], history=True)['items']) == 3
        # Real connection failure to an unused port preserves other store results.
        healthy = registry._engines['timeseries']
        registry._engines['timeseries'] = create_engine(s.config.timeseries_database_url.replace(':25432/', ':1/'), connect_args={'connect_timeout': 1})
        try:
            with s.transaction() as uow:
                service(uow)
                partial = ObjectDataService(uow, registry).get(s.actor('alice'), identifiers[0])
                assert partial['observations']['status'] == 'unavailable'
                assert partial['gis']['status'] == 'current'
                assert partial['catalog']['models'][0]['id'] == str(models[0].id)
        finally:
            registry._engines['timeseries'].dispose()
            registry._engines['timeseries'] = healthy
        with registry.engine('timeseries').connect() as c:
            assert c.execute(text('SELECT count(*) FROM telemetry_samples WHERE source_id=:id'), {'id': source.id}).scalar_one() == 8
        # Raw batches are explicitly nonsemantic at trusted worker startup.
        from lzo.semantic.projection import ProjectionService
        from lzo.contracts import RevisionRef
        with s.transaction() as uow:
            assert not uow.session.execute(text("SELECT 1 FROM jobs WHERE kind='rdf.project' AND payload->>'resource_id'=:id"), {'id': result['id']}).first()
    finally:
        registry._engines.pop('core')
        registry.close()
