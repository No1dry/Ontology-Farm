"""Structured provenance travels through acceptance, independent stores and reads."""
import io, json
from uuid import uuid4
from sqlalchemy import text
from test_timeseries import telemetry_case
from test_telemetry_archive import owned_s3_settings


def test_professional_metadata_actual_store_roundtrip(telemetry_case):
    from lzo.storage.blobs import get_blob_store
    from lzo.storage.migrate import migrate_domain
    from lzo.artifacts.service import ArtifactService
    from lzo.telemetry.replay import accept_file
    from lzo.telemetry.schemas import FileReplayInput
    from lzo.catalog.service import CatalogService
    from lzo.catalog.schemas import CreateModel, ModelMetadata, CreateDocument, DocumentMetadata
    from lzo.synchronization.delivery import poll_deliveries
    s=telemetry_case
    s.config=owned_s3_settings(s.config)
    migrate_domain('catalog',s.config.catalog_migration_database_url,core_url=s.config.database_url)
    record=s.observation('provenance').model_dump(mode='json') | {
        'measurement_method':{'name':'ultrasonic','version':'v2'},'calibration':{'version':'cal-1','method':'bench'},
        'time_quality':{'state':'synchronized','uncertainty_ms':'2'},
        'media_metadata':{'encoding':'h264','resolution':{'width':1920,'height':1080},'sequence':4,
            'gaps':[{'start':'2026-01-15T12:00:01Z','end':'2026-01-15T12:00:02Z'}],'verification':'unverified'}}
    with s.transaction() as uow:
        svc=s.telemetry(uow)
        store=get_blob_store(s.config)
        artifact=ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],
            store.put(io.BytesIO(json.dumps([record]).encode()),'application/json'),filename='provenance.json',metadata={'synthetic':True})
        accepted=accept_file(svc,s.actor('alice'),FileReplayInput(source_id=s.source.id,artifact_version_id=artifact.version_id,format='json'),'provenance')
        catalog=CatalogService(uow,s.registry)
        model=catalog.create_model(s.actor('alice'),CreateModel(scope=s.scope,metadata=ModelMetadata(exact_model='PX-1',firmware='fw-7',
            hardware_version='board-2',equipment_category='pump',synthetic=True)),str(uuid4()))
        document=catalog.create_document(s.actor('alice'),CreateDocument(scope=s.scope,metadata=DocumentMetadata(title='Synthetic manual',
            document_type='manual',artifact_version_id=artifact.version_id,source_uri='urn:synthetic:manual',source_version='1',license='synthetic',
            author='Author',organization='Organization',agricultural_scenario='pond',language='zh',manual_summary_preparer='Curator')),str(uuid4()))
        uow.commit()
    poll_deliveries(s.registry)
    actual=s.query()['items'][0]
    for key in ('measurement_method','calibration','time_quality','media_metadata'):
        assert actual[key]==record[key]
    with s.registry.engine('timeseries').connect() as c:
        assert c.execute(text('SELECT payload FROM telemetry_samples WHERE id=:id'),{'id':actual['id']}).scalar_one()==actual
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        svc=CatalogService(uow,s.registry)
        assert svc.get(s.actor('alice'),model.id).metadata.hardware_version=='board-2'
        assert svc.get(s.actor('alice'),model.id).metadata.firmware=='fw-7'
        assert svc.get(s.actor('alice'),document.id).metadata.manual_summary_preparer=='Curator'
        assert svc.get(s.actor('alice'),document.id).projection_status=='current'
    from professional_http import scenario_client
    with scenario_client(s) as client:
        replay=client.post('/api/v1/telemetry/replay',headers={'Idempotency-Key':str(uuid4())},json={
            'source_id':str(s.source.id),'artifact_version_id':str(artifact.version_id),'format':'json'})
        assert replay.status_code==202 and replay.json()['accepted_count']==0,replay.text
        public_model=client.post('/api/v1/catalog/models',headers={'Idempotency-Key':str(uuid4())},json=CreateModel(
            scope=s.scope,metadata=ModelMetadata(exact_model='HTTP-PX',firmware='fw-http',hardware_version='board-http',
                equipment_category='pump',synthetic=True)).model_dump(mode='json'))
        assert public_model.status_code==201,public_model.text
        public_document=client.post('/api/v1/catalog/documents',headers={'Idempotency-Key':str(uuid4())},json=CreateDocument(
            scope=s.scope,metadata=document.metadata).model_dump(mode='json'))
        assert public_document.status_code==201,public_document.text
        poll_deliveries(s.registry)
        page=client.get('/api/v1/telemetry/observations',params={'source_id':str(s.source.id),'quality':'valid'})
        assert page.status_code==200,page.text
        for key in ('measurement_method','calibration','time_quality','media_metadata'):
            assert page.json()['items'][0][key]==record[key]
        assert client.get('/api/v1/catalog/models/'+public_model.json()['id']).json()['metadata']['hardware_version']=='board-http'
        assert client.get('/api/v1/catalog/documents/'+public_document.json()['id']).json()['metadata']['manual_summary_preparer']=='Curator'
