"""Large synthetic attachment sets use one governed object fixture, not one per asset."""
from uuid import UUID,uuid4
import pytest
from sqlalchemy import text


def test_asset_pages_filter_current_files_before_limit_and_reach_all_rows(object_case):
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization.delivery import _apply,deliver_once
    from lzo.gis.repository import apply_geometry
    s=object_case
    obj=s.create_object().json()['revision']['resource_id']
    grant=s.grant('alice','editor',scope='farm_b')
    files=[]
    for owner in ('object_b','object_a'):
        response=s.client.post(f'/api/v1/artifacts?resource_id={s.ids_by_name[owner]}&filename=fixture.bin',
            headers={**s.headers(1),'Content-Type':'application/octet-stream'},content=b'synthetic asset package')
        assert response.status_code==201,response.text
        files.append(response.json())
    s.revoke(grant)
    registry=StorageRegistry.from_settings(s.config)
    registry._engines['core'].dispose();registry._engines['core']=s.app
    base=uuid4().int>>32
    visible=[]
    try:
        for offset in range(181):
            identifier=UUID(int=base+offset)
            artifact=files[0 if offset<60 else 1]
            asset={'asset_id':str(identifier),'artifact_version_id':artifact['version_id'],'sha256':artifact['sha256'],
                'normalized':{'type':'Point','coordinates':[114,30]},'quality':'display_only'}
            envelope=SyncEnvelope(entity_id=UUID(obj),aggregate_revision=offset+2,target='gis',operation='geometry.index',
                payload={'geometry':None,'active':True,'asset':asset},data_nature='synthetic')
            _apply(registry,envelope,apply_geometry)
            if offset>=60:visible.append(str(identifier))
        deliver_once(registry,'gis',apply_geometry)
        seen=[];cursor=None
        while True:
            response=s.client.get(f'/api/v1/objects/{obj}/spatial-assets',params={'limit':37,**({'cursor':cursor} if cursor else {})})
            assert response.status_code==200,response.text
            page=response.json()
            assert page['status']=='current'
            seen.extend(a['asset_id'] for a in page['assets'])
            cursor=page['next_cursor']
            if cursor is None:break
            assert len(page['assets'])==37
        assert seen==visible and len(seen)==121 and len(set(seen))==121
        import base64,json
        for malformed in ('','broken',base64.urlsafe_b64encode(json.dumps({'version':1,'resource':3,'event':str(uuid4()),'actor':str(s.users['alice']),'authorization':1,'after':str(uuid4())}).encode()).decode()):
            assert s.client.get(f'/api/v1/objects/{obj}/spatial-assets',params={'cursor':malformed}).status_code==422
        assert s.client.get(f'/api/v1/objects/{obj}/spatial-assets?limit=0').status_code==422
        first=s.client.get(f'/api/v1/objects/{obj}/spatial-assets?limit=37').json()
        s.grant('alice','viewer',scope='farm_a')
        stale_authority=s.client.get(f'/api/v1/objects/{obj}/spatial-assets',params={'limit':37,'cursor':first['next_cursor']})
        assert stale_authority.status_code==409 and stale_authority.json()['code']=='GIS_CURSOR_STALE'
        first=s.client.get(f'/api/v1/objects/{obj}/spatial-assets?limit=37').json()
        assert s.patch(obj,1,[s.assertion(obj,state='candidate')]).status_code==200
        stale=s.client.get(f'/api/v1/objects/{obj}/spatial-assets',params={'limit':37,'cursor':first['next_cursor']})
        assert stale.status_code==409 and stale.json()['code']=='GIS_CURSOR_STALE'
    finally:
        registry._engines.pop('core');registry.close()
