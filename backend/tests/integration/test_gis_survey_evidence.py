"""Synthetic authority fixtures exercise controlled provenance, never field accuracy."""
import io,json
from uuid import UUID,uuid4
import pytest
from sqlalchemy import insert,update,select


def test_survey_requires_controlled_evidence_and_current_authority(object_case,gis_tick):
    from lzo.auth import models
    from lzo.contracts import ResourceScope,RevisionRef
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.schemas import SourceCreate,EvidenceCreate
    from lzo.ingestion.models import sources
    from lzo.ingestion.evidence import EvidenceService
    from lzo.storage.blobs import get_blob_store
    from lzo.governance.revisions import append_revision
    from lzo.governance.models import resource_heads
    s=object_case
    with s.transaction() as uow:
        for name,actions in [('survey_source',['view','source.manage','source.read']),('survey_evidence',['view','evidence.manage','evidence.read'])]:
            s.roles[name]=uuid4()
            uow.session.execute(insert(models.roles).values(id=s.roles[name],name=name,actions=actions,revision=1))
        uow.commit()
    source_grant=s.grant('alice','survey_source',scope='farm_a')
    evidence_grant=s.grant('alice','survey_evidence',scope='farm_a')
    target_edit=s.grant('alice','editor',scope='farm_b')
    s.grant('alice','viewer',scope='farm_b')
    identifier=uuid4()
    created=s.client.post('/api/v1/objects',headers=s.headers(),json={'bundle_id':str(s.bundle_id),'create':{
        'resource_id':str(identifier),'class_iris':['urn:Pond'],'display_name':'Synthetic verified-nature fixture',
        'owner_scope_id':str(s.scopes['farm_a']),'state':'active','data_nature':'verified','assertions':[]}})
    assert created.status_code==201,created.text
    with s.transaction() as uow:
        source=SourceService().create(uow,s.actor('alice'),SourceCreate(source_system='test',source_instance='synthetic',collection='survey',kind='file',
            scope=ResourceScope(None,s.scopes['farm_a'],(),'data'),data_nature='synthetic'))
        # Explicit test-only seed: production verified-source creation is unavailable.
        configuration=source.configuration.model_copy(update={'data_nature':'verified'}).model_dump(mode='json')
        uow.session.execute(update(sources).where(sources.c.id==source.id).values(configuration=configuration,revision=2))
        pin=append_revision(uow,s.actor('alice'),source.id,1,configuration,'source.fixture_verified')
        target=uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id==s.ids_by_name['object_b'])).mappings().one()
        command=EvidenceCreate(source_id=source.id,scope=ResourceScope(None,s.scopes['farm_a'],(),'data'),
            target_refs=(RevisionRef(target['resource_id'],target['revision'],target['event_id']),
                RevisionRef(identifier,1,UUID(created.json()['revision']['event_id']))),
            title='Synthetic survey authority fixture',locator='synthetic/no-field-measurement',data_nature='verified',replaces_id=None)
        evidence=EvidenceService(uow,get_blob_store(s.config),s.config).create(uow,s.actor('alice'),io.BytesIO(b'synthetic controlled evidence'),'application/pdf',command)
        uow.commit()
    forged={'evidence_policy':'evidence-v1','high_risk':True,'source_revision':{'resource_id':str(uuid4()),'revision':1,'event_id':str(uuid4())},
        'evidence':command.model_copy(update={'source_id':None,'scope':ResourceScope(s.ids_by_name['object_a'],s.scopes['farm_a'],(),'data')}).model_dump(mode='json')}
    forged['evidence']['source_id']=forged['source_revision']['resource_id']
    upload=s.client.post(f"/api/v1/artifacts?resource_id={s.ids_by_name['object_a']}&filename=forged.bin",
        headers={**s.headers(1),'Content-Type':'application/octet-stream','X-Artifact-Metadata':json.dumps(forged)},content=b'synthetic package')
    assert upload.status_code==201,upload.text
    artifact=upload.json()
    asset={'resource_id':str(identifier),'expected_revision':1,'asset_id':str(uuid4()),'artifact_version_id':artifact['version_id'],
        'sha256':artifact['sha256'],'footprint':{'type':'Point','coordinates':[114,30]},'horizontal_datum':'EPSG:4326',
        'captured_at':'2026-10-22T00:00:00Z','accuracy_metres':1,'source':'synthetic fixture only','quality':'verified',
        'survey_evidence_version_id':artifact['version_id']}
    def register(value):return s.client.post('/api/v1/spatial-assets',headers=s.headers(value['expected_revision']),json={'asset':value,'bundle_id':str(s.bundle_id)})
    rejected=register(asset)
    assert rejected.status_code==404,rejected.text
    asset['survey_evidence_version_id']=str(evidence.id)
    accepted=register(asset)
    assert accepted.status_code==201,accepted.text
    with s.transaction() as uow:
        assert uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==evidence.id)).scalar_one()==evidence.revision.revision
    gis_tick(s)
    listing=s.client.get(f'/api/v1/objects/{identifier}/spatial-assets')
    assert listing.status_code==200 and listing.json()['assets'][0]['quality']=='verified',listing.text
    # Model an old pre-fix specialized-index entry; read filtering must reject
    # its ordinary-file survey independently of the now-fixed write command.
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply
    from lzo.gis.repository import apply_geometry
    registry=StorageRegistry.from_settings(s.config)
    try:
        legacy={**asset,'asset_id':str(uuid4()),'survey_evidence_version_id':artifact['version_id'],
            'normalized':{'type':'Point','coordinates':[114,30]}}
        _apply(registry,SyncEnvelope(entity_id=identifier,aggregate_revision=100,target='gis',operation='geometry.index',
            payload={'geometry':None,'active':True,'asset':legacy},data_nature='synthetic'),apply_geometry)
    finally:registry.close()
    filtered=s.client.get(f'/api/v1/objects/{identifier}/spatial-assets')
    assert filtered.status_code==200 and [a['asset_id'] for a in filtered.json()['assets']]==[asset['asset_id']],filtered.text
    # Target edit is distinct from continued view: registration cannot use read authority.
    s.revoke(target_edit)
    another={**asset,'asset_id':str(uuid4()),'expected_revision':accepted.json()['revision']}
    denied=register(another)
    assert denied.status_code in (403,404),denied.text
    s.grant('alice','editor',scope='farm_b')
    s.revoke(source_grant)
    source_denied=register(another)
    assert source_denied.status_code in (403,404),source_denied.text
    # Current evidence-read authority is required even for already-indexed survey metadata.
    s.revoke(evidence_grant)
    current=s.client.get(f'/api/v1/objects/{identifier}/spatial-assets')
    assert current.status_code==404 or current.status_code==200 and current.json()['assets']==[],current.text
