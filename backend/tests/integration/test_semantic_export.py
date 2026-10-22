"""Private job exports round-trip exact terms and retain named graph layers."""
import io
import json
import zipfile
from uuid import UUID, uuid4
import pytest
from rdflib import Dataset, URIRef
from test_authorized_queries import query_case
from test_inference_retraction import inference_case


def grant_export(s, identifiers, name='nobody'):
    from sqlalchemy import insert
    from lzo.auth.models import roles
    role = uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(roles).values(id=role,name='export-'+role.hex,actions=['export'],revision=1))
        uow.commit()
    key='export-'+role.hex
    s.roles[key]=role
    for identifier in identifiers:
        s.ids_by_name[str(identifier)]=identifier
        s.grant(name,key,resource=str(identifier))


def test_export_revocation_fails_job_and_hides_existing_artifact(query_case):
    s = query_case
    grant_export(s,[s.ids['a'],s.ids['c']])
    from sqlalchemy import select,func
    from lzo.auth.models import grants
    from lzo.governance.models import resource_heads
    with s.transaction() as uow:
        initial_head=uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==s.ids['a'])).scalar_one()
        initial_grants=uow.session.execute(select(func.count()).select_from(grants)).scalar_one()
    request = {"resource_ids": [str(s.ids['a']), str(s.ids['c'])], "format": "turtle",
        "layout": "dataset", "include_provenance": True, "include_inferred": True}
    response = s.client.post('/api/v1/query/exports', headers=s.headers(), json=request)
    assert response.status_code == 202, response.text
    s.drain()
    job = s.client.get('/api/v1/jobs/'+response.json()['id'])
    assert job.status_code == 200 and job.json()['job']['status'] == 'succeeded', job.text
    path = job.json()['result_ref']['api_path']
    artifact = s.client.get(path)
    assert artifact.status_code == 200, artifact.text
    owner=UUID(artifact.json()['resource_id']);version=UUID(artifact.json()['version_id']);job_id=UUID(response.json()['id'])
    from lzo.governance.history import HistoryService,HistoryQuery
    from lzo.auth.permissions import permission_context
    with s.transaction() as uow,permission_context(uow):
        assert uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==s.ids['a'])).scalar_one()==initial_head
        assert uow.session.execute(select(func.count()).select_from(grants)).scalar_one()==initial_grants
        for identifier in (owner,version,job_id):
            records=HistoryService(uow).list(s.actor('nobody'),HistoryQuery(resource_id=identifier),None).items
            assert records
            assert str(s.ids['restricted-b']) not in str([HistoryService(uow).get(s.actor('nobody'),identifier,r.revision).payload for r in records])
    body = s.client.get(path+'/content')
    assert body.status_code == 200 and body.content.startswith(b'PK')
    assert str(s.ids['restricted-b']).encode() not in body.content
    with zipfile.ZipFile(io.BytesIO(body.content)) as archive:
        assert all(str(s.ids['restricted-b']).encode() not in archive.read(name) for name in archive.namelist())
    # Job visibility must follow its actual append-only result/dependency rows,
    # not the selected producer list cached in the export binding.
    from sqlalchemy import insert
    from lzo.worker.models import job_dependencies
    with s.transaction() as uow:
        assert version in set(uow.session.execute(select(job_dependencies.c.resource_id).where(job_dependencies.c.job_id==job_id)).scalars())
        uow.session.execute(insert(job_dependencies).values(job_id=job_id,resource_id=s.ids['restricted-b']))
        uow.commit()
    assert s.client.get('/api/v1/jobs/'+str(job_id)).status_code==404
    assert s.client.get(path).status_code==200
    # Public upload and replacement cannot write a generated owner even with a forged metadata marker.
    for target in (f'/api/v1/artifacts?resource_id={owner}&filename=forged.ttl',
                   f'/api/v1/artifacts?resource_id={owner}&filename=forged.ttl&previous_version_id={version}'):
        result=s.client.post(target,headers={**s.headers(2),'Content-Type':'text/turtle','X-Artifact-Metadata':'{"generated":"semantic.export"}'},content=b'<urn:x> <urn:p> <urn:y> .')
        assert result.status_code==403,result.text
    from lzo.auth import permissions
    policy=permissions._generated_read_policy
    permissions._generated_read_policy=None
    try:
        assert s.client.get(path).status_code==404
    finally:
        permissions._generated_read_policy=policy
    pending = s.client.post('/api/v1/query/exports', headers=s.headers(), json=request)
    assert pending.status_code == 202, pending.text
    s.revoke(s.a_grant)
    s.drain()
    from sqlalchemy import select
    from lzo.worker.models import jobs
    with s.transaction() as uow:
        state = uow.session.execute(select(jobs).where(jobs.c.id == UUID(pending.json()['id']))).mappings().one()
        assert state['status'] == 'failed' and state['error_code'] == 'AUTHORIZATION_REVOKED'
    assert s.client.get(path).status_code == 404
    assert s.client.get(path+'/content').status_code == 404
    assert s.client.get('/api/v1/jobs/'+str(job_id)).status_code==404
    # Explicit generated grants must not override a denied source dependency.
    for identifier in (owner,version,job_id):
        s.ids_by_name[str(identifier)]=identifier
        s.grant('nobody','viewer',resource=str(identifier))
    assert s.client.get(path).status_code==404
    with s.transaction() as uow,permission_context(uow):
        for identifier in (owner,version,job_id):
            assert not HistoryService(uow,page_size=1).list(s.actor('nobody'),HistoryQuery(resource_id=identifier),None).items


@pytest.fixture
def lexical_case(schema_case,request):
    from sqlalchemy import insert, update, select
    from lzo.auth import models
    from lzo.ontology.models import bundles
    from lzo.worker.runner import Runner
    from lzo.semantic.projection import register_nonsemantic
    s = schema_case
    for event in ('role.fixture_installed', 'grant.fixture_installed', 'object.fixture_created'):
        register_nonsemantic(event, 'test-export-fixture-v1')
    s.grant('alice', 'editor', scope='farm_a')
    with s.transaction() as uow:
        identifier = uuid4()
        uow.session.execute(insert(models.roles).values(id=identifier, name='review', actions=['view','candidate.read'], revision=1))
        s.review_grant_id = uuid4()
        uow.session.execute(insert(models.grants).values(id=s.review_grant_id, user_id=s.users['alice'], role_id=identifier,
            security_class='data', scope_id=s.scopes['farm_a'], revision=1))
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1))
        uow.commit()
    xsd = 'http://www.w3.org/2001/XMLSchema#'
    doc = {'classes':[{'iri':'urn:Measure','label':'Measure'}], 'properties':[
        {'iri':'urn:'+name, 'label':name, 'datatype_iri':xsd+datatype,
         'owner_policy_iri':'urn:lzo:authority:ontology_owned'}
        for name, datatype in [('decimal','decimal'),('integer','integer'),('boolean','boolean'),('text','string')]]}
    draft = s.create(doc)
    assert s.publish(draft,s.approve(draft)).status_code == 202
    assert Runner(s.app,s.config).run_once()
    with s.transaction() as uow:
        bundle = uow.session.execute(select(bundles.c.id)).scalar_one()
    s.primary_id, s.identifier = sorted((uuid4(),uuid4()),key=str)
    grant_export(s,[s.primary_id,s.identifier],name='alice')
    primary=s.client.post('/api/v1/objects',headers=s.headers(),json={'bundle_id':str(bundle),'create':{
        'resource_id':str(s.primary_id),'class_iris':['urn:Measure'],'display_name':'Primary ordinary root',
        'owner_scope_id':str(s.scopes['farm_a']),'state':'active','data_nature':'test'}})
    assert primary.status_code==201,primary.text
    s.literal_text='hello'+('x'*(1024*1024) if getattr(request,'param',False) else '')
    values = [('decimal','12.50','decimal','accepted'),('decimal','12.5','decimal','accepted'),
        ('decimal','12.50','decimal','candidate'),('integer','001','integer','accepted'),
        ('boolean','1','boolean','accepted'),('text',s.literal_text,'string','accepted')]
    assertions = [{'assertion_id':str(uuid4()),'resource_id':str(s.identifier),'predicate_iri':'urn:'+name,
        'object':{'kind':'literal','value':value,'datatype_iri':xsd+datatype},'state':state,'data_nature':'test',
        'valid_from':'2026-01-01T00:00:00Z','support':{'support_id':str(uuid4()),'raw_value':{'fixture':True}}}
        for name,value,datatype,state in values]
    response = s.client.post('/api/v1/objects',headers=s.headers(),json={'bundle_id':str(bundle),'create':{
        'resource_id':str(s.identifier),'class_iris':['urn:Measure'],'display_name':'Exact values',
        'owner_scope_id':str(s.scopes['farm_a']),'state':'active','data_nature':'test','assertions':assertions}})
    assert response.status_code == 201,response.text
    def drain():
        runner=Runner(s.app,s.config)
        for _ in range(30):
            if not runner.run_once():
                return
        pytest.fail('queue did not drain')
    s.drain=drain
    drain()
    return s


@pytest.mark.parametrize('lexical_case',[True],indirect=True)
def test_normal_large_literal_supervised_read_and_cancel_release_build_lock(lexical_case,monkeypatch):
    import time
    from threading import Event,Thread
    from lzo.semantic import query_read,query_process
    from lzo.semantic.nt import parse_nt
    s=lexical_case
    s.config=s.config.model_copy(update={'query_timeout_ms':60000})
    s.client.app.state.settings=s.config
    started=time.monotonic()
    created=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
        'resource_ids':[str(s.identifier)],'format':'turtle','layout':'single-graph','graph_role':'accepted'})
    assert created.status_code==202,created.text
    s.drain()
    job=s.client.get('/api/v1/jobs/'+created.json()['id']).json()
    assert job['job']['status']=='succeeded',job
    body=s.client.get(job['result_ref']['api_path']+'/content')
    assert body.status_code==200
    assert str(parse_nt(body.content).value(URIRef(f'urn:lzo:object:{s.identifier}'),URIRef('urn:text')))==s.literal_text
    print(f'normal >1MiB literal real export completed in {time.monotonic()-started:.3f}s')
    original=query_read.run_child
    entered,committed=Event(),Event()
    children,failures=[],[]
    def revoke():
        try:
            assert entered.wait(30)
            from sqlalchemy import update
            from lzo.auth.models import grants,authorization_state
            with s.transaction() as uow:
                uow.session.execute(update(grants).where(grants.c.id==s.review_grant_id).values(active=False))
                uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1))
                uow.commit()
            committed.set()
        except BaseException as error:
            failures.append(error)
    worker=Thread(target=revoke)
    def supervised(payload,**options):
        if len(payload['nt'])<1024*1024:
            return original(payload,**options)
        launch=query_process.subprocess.Popen
        def observed(*args,**kwargs):
            child=launch(*args,**kwargs)
            children.append(child)
            entered.set()
            return child
        with monkeypatch.context() as patch:
            patch.setattr(query_process.subprocess,'Popen',observed)
            return original(payload,**{**options,'cancelled':lambda:bool(children)})
    monkeypatch.setattr(query_read,'run_child',supervised)
    worker.start()
    try:
        result=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':'ASK {}','timeout_ms':60000})
        assert result.status_code==422 and result.json()['code']=='SPARQL_CANCELLED',result.text
        assert entered.is_set()
        assert committed.wait(5),'parser cancellation retained build authorization lock'
    finally:
        entered.set()
        worker.join(10)
    assert not worker.is_alive() and not failures
    assert children and all(child.poll() is not None for child in children)


def test_exact_turtle_jsonld_layer_round_trips_and_candidate_download_right(lexical_case,monkeypatch):
    from lzo.semantic.files import parse_text, canonical_strings
    s=lexical_case
    query = s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':
        'SELECT (COUNT(?v) AS ?n) WHERE {?s <urn:decimal> ?v}'})
    assert query.status_code == 200,query.text
    assert query.json()['rows'][0]['n']['value']=='2'
    comparison=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':
        'SELECT (?a = ?b AS ?equal) (sameTerm(?a,?b) AS ?same) WHERE {'
        '?s <urn:decimal> ?a,?b FILTER(STR(?a)="12.50" && STR(?b)="12.5")}'})
    assert comparison.status_code==200,comparison.text
    assert comparison.json()['rows'][0]['equal']['value']=='true'
    assert comparison.json()['rows'][0]['same']['value']=='false'
    paths=[]
    for format,layout,role in [('turtle','single-graph','accepted'),('turtle','dataset',None),
                              ('json-ld','dataset',None),('json-ld','single-graph','accepted')]:
        response=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
            'resource_ids':[str(s.primary_id),str(s.identifier)],'format':format,'layout':layout,'graph_role':role,
            'include_provenance':True,'include_inferred':False})
        assert response.status_code==202,response.text
        s.drain()
        job=s.client.get('/api/v1/jobs/'+response.json()['id']).json()
        assert job['job']['status']=='succeeded',job
        from sqlalchemy import select
        from lzo.worker.models import jobs
        with s.transaction() as uow:
            assert uow.session.execute(select(jobs.c.resource_id).where(jobs.c.id==UUID(response.json()['id']))).scalar_one()==s.primary_id
        path=job['result_ref']['api_path'];paths.append(path)
        detail=s.client.get(path).json()
        assert detail['metadata']['scope']=='authorized_subset'
        content=s.client.get(path+'/content').content
        if format=='turtle' and layout=='dataset':
            ds=Dataset()
            with zipfile.ZipFile(io.BytesIO(content)) as archive:
                manifest=json.loads(archive.read('manifest.json'))
                for entry in manifest['graphs']:
                    assert entry['blank_node_scope']=='dataset'
                    parsed=parse_text(archive.read(entry['file']).decode(),'turtle',allow_platform_skolem=True)
                    for triple in parsed.default_graph:
                        ds.graph(URIRef(entry['iri'])).add(triple)
        else:
            ds=parse_text(content.decode(),format,allow_platform_skolem=True)
        ds=canonical_strings(ds)
        decimal=[(str(o),str(g)) for _,p,o,g in ds.quads() if str(p)=='urn:decimal']
        assert {'12.50','12.5'} <= {v for v,g in decimal}
        assert {str(o) for _,p,o,g in ds.quads() if str(p)=='urn:integer'}=={'001'}
        assert {str(o) for _,p,o,g in ds.quads() if str(p)=='urn:boolean'}=={'1'}
        if layout=='dataset':
            assert len([1 for v,g in decimal if v=='12.50'])==2
            assert {g for v,g in decimal}=={'urn:lzo:query:accepted','urn:lzo:query:candidate'}
        else:
            assert len(decimal)==2
    # Candidate read is independent from view: revoking it must hide review exports.
    # Legitimate short reads preserve the actual exported bytes and file handle.
    # Pause the real route between chunks; no authorization boundary is mocked.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from starlette.requests import Request
    from lzo.artifacts.router import content as download
    from lzo.artifacts.store import ArtifactStore
    from lzo.errors import ApiError
    from sqlalchemy import update
    from lzo.auth.models import grants,authorization_state
    def revoke_candidate():
        with s.transaction() as uow:
            uow.session.execute(update(grants).where(grants.c.id==s.review_grant_id).values(active=False))
            uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1))
            uow.commit()
    request=Request({'type':'http','app':s.client.app,'headers':[(b'cookie',
        '; '.join(f'{k}={v}' for k,v in s.client.cookies.items()).encode())],
        'state':{'request_id':'export-stream-test'}})
    from lzo.storage.blobs import get_blob_store
    provider_class = type(get_blob_store(s.client.app.state.settings))
    original_open=provider_class.open
    opened=[]
    def short_open(store,blob):
        stream=original_open(store,blob)
        opened.append(stream)
        class ShortRead:
            def read(self,size):
                return stream.read(min(size,32))
            def close(self):
                stream.close()
        return ShortRead()
    async def paused_stream():
        first=await anext(response.body_iterator)
        assert len(first)==32
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(revoke_candidate).result(timeout=5)
        with pytest.raises(ApiError) as failure:
            await anext(response.body_iterator)
        assert failure.value.status==404
    with monkeypatch.context() as patch:
        patch.setattr(provider_class,'open',short_open)
        response=download(UUID(paths[1].rsplit('/',1)[-1]),request)
        asyncio.run(paused_stream())
    assert opened and all(stream.closed for stream in opened)
    assert s.client.get(paths[0]+'/content').status_code==200
    assert s.client.get(paths[1]+'/content').status_code==404
    assert s.client.get(paths[2]+'/content').status_code==404


def test_export_publication_is_fenced_and_replay_keeps_one_actual_output(lexical_case):
    from datetime import datetime,timedelta,timezone
    from sqlalchemy import select,update,func
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs,job_dependencies
    from lzo.semantic.exports import _run
    from lzo.semantic.models import export_outputs
    from lzo.artifacts.models import artifact_versions
    from lzo.errors import ApiError
    s=lexical_case
    result=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
        'resource_ids':[str(s.primary_id)],'format':'turtle','layout':'single-graph','graph_role':'accepted'})
    assert result.status_code==202,result.text
    identifier=UUID(result.json()['id'])
    runner=Runner(s.app,s.config)
    expired=runner.claim()
    assert expired.job_id==identifier
    owner=UUID(expired.payload['export_id'])
    with s.transaction() as uow:
        uow.session.execute(update(jobs).where(jobs.c.id==identifier).values(lease_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        uow.commit()
    with pytest.raises(ApiError) as failure:
        _run(expired)
    assert failure.value.code=='LEASE_EXPIRED'
    with s.transaction() as uow:
        assert not uow.session.execute(select(export_outputs).where(export_outputs.c.export_id==owner)).first()
    current=runner.claim()
    assert current.job_id==identifier and current.lease_token!=expired.lease_token
    _run(current)
    _run(current)  # Resume after committed publication, before runner.finish.
    runner.finish(current)
    with s.transaction() as uow:
        version=uow.session.execute(select(export_outputs.c.version_id).where(export_outputs.c.export_id==owner)).scalar_one()
        assert uow.session.execute(select(func.count()).select_from(artifact_versions).where(artifact_versions.c.resource_id==owner)).scalar_one()==1
        assert version in set(uow.session.execute(select(job_dependencies.c.resource_id).where(job_dependencies.c.job_id==identifier)).scalars())
    assert s.client.get('/api/v1/jobs/'+str(identifier)).json()['job']['status']=='succeeded'
