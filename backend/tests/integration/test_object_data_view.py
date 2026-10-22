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
