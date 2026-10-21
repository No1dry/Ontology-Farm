"""Actual dataset quad budget and every disclosed export endpoint are protected."""
from uuid import UUID
import pytest
from test_inference_retraction import inference_case
from test_semantic_export import grant_export


@pytest.fixture
def endpoint_case(inference_case):
    s=inference_case
    for name in ('a','c'):
        s.create_node(name)
        s.ids_by_name[name]=s.ids[name]
    s.edge('a','c')
    s.drain()
    s.grant('nobody','viewer',resource='a')
    s.c_grant=s.grant('nobody','viewer',resource='c')
    grant_export(s,[s.ids['a']])
    s.login('nobody')
    return s


def test_export_disclosed_nonroot_endpoint_is_a_current_read_dependency(endpoint_case):
    s=endpoint_case
    result=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
        'resource_ids':[str(s.ids['a'])],'format':'turtle','layout':'single-graph','graph_role':'accepted'})
    assert result.status_code==202,result.text
    s.drain()
    job=s.client.get('/api/v1/jobs/'+result.json()['id']).json()
    assert job['job']['status']=='succeeded',job
    path=job['result_ref']['api_path']
    detail=s.client.get(path).json()
    assert str(s.ids['c']).encode() in s.client.get(path+'/content').content
    s.revoke(s.c_grant)
    status=s.client.get(path+'/content').status_code
    upload=s.client.post('/api/v1/artifacts',params={'resource_id':detail['resource_id'],'filename':'forged.ttl'},
        headers={**s.headers(2),'Content-Type':'text/turtle'},content=b'<urn:x> <urn:p> <urn:y> .')
    assert (status,upload.status_code)==(404,404)


def test_dataset_budget_counts_named_and_default_quads_before_retention(endpoint_case):
    from lzo.auth.permissions import permission_context
    from lzo.semantic.authorized_dataset import build_authorized_dataset
    s=endpoint_case
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        snapshot=build_authorized_dataset(s.actor('nobody'))
        union_count=len(snapshot.dataset)
        assert len(list(snapshot.dataset.quads()))>union_count
    original=s.client.app.state.settings
    s.client.app.state.settings=original.model_copy(update={'query_max_quads':union_count})
    try:
        result=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':'ASK {}'})
    finally:
        s.client.app.state.settings=original
    assert result.status_code==422,result.text
    assert result.json()['code']=='SPARQL_BUDGET_EXCEEDED'


def test_incomplete_deep_proof_budget_never_returns_false_as_complete(endpoint_case):
    s=endpoint_case
    s.login('alice')
    for name in ('d','e'):
        s.create_node(name)
        s.ids_by_name[name]=s.ids[name]
        s.grant('nobody','viewer',resource=name)
    s.edge('c','d');s.edge('d','e');s.recompute()
    s.login('nobody')
    original=s.client.app.state.settings
    s.client.app.state.settings=original.model_copy(update={'query_max_proof_nodes':1})
    try:
        result=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':
            f'ASK {{<urn:lzo:object:{s.ids["a"]}> <urn:connects> <urn:lzo:object:{s.ids["e"]}>}}'})
    finally:
        s.client.app.state.settings=original
    assert result.status_code==422,result.text
    assert result.json()['code']=='SPARQL_BUDGET_EXCEEDED'


def test_current_geometry_pointer_uses_real_revision_and_support_visibility(endpoint_case):
    from uuid import uuid4
    from sqlalchemy import select
    from lzo.governance.models import resource_heads
    s=endpoint_case
    s.login('alice')
    with s.transaction() as uow:
        head=uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id==s.ids['c'])).mappings().one()
    revision=s.client.get('/api/v1/objects/'+str(s.ids['a'])).json()['revision']
    response=s.client.put('/api/v1/geometry/'+str(s.ids['a']),headers=s.headers(revision),json={
        'bundle_id':str(s.bundle.id),'geometry':{'resource_id':str(s.ids['a']),
        'expected_revision':revision,'geojson':{'type':'Point','coordinates':[114.5,30.2]},
        'source_crs':'EPSG:4326','source_support':{'support_id':str(uuid4()),'raw_value':{'manual':True},
        'evidence_refs':[{'resource_id':str(s.ids['c']),'revision':head['revision'],'event_id':str(head['event_id'])}]}}})
    assert response.status_code==200,response.text
    pointer=f'urn:lzo:geometry:{s.ids["a"]}:{response.json()["revision"]}'
    s.drain();s.login('nobody')
    query=f'SELECT ?geometry WHERE {{<urn:lzo:object:{s.ids["a"]}> <urn:lzo:geometry> ?geometry}}'
    result=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':query})
    assert result.status_code==200,result.text
    assert [r['geometry']['value'] for r in result.json()['rows']]==[pointer]
    s.revoke(s.c_grant)
    result=s.client.post('/api/v1/query/sparql',headers=s.headers(),json={'query':query})
    assert result.status_code==200 and result.json()['rows']==[],result.text


def test_explicit_candidate_export_requires_candidate_read_even_if_empty(endpoint_case):
    s=endpoint_case
    result=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
        'resource_ids':[str(s.ids['a'])],'format':'turtle','layout':'single-graph','graph_role':'candidate'})
    assert result.status_code==403,result.text
