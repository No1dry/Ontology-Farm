"""Real governed export assertion shared by the bounded mapped-term cases."""
from uuid import UUID
from sqlalchemy import select


def assert_exported_triples(s,runner,identifiers,fmt,expected):
    from test_semantic_export import grant_export
    from lzo.worker.models import jobs
    from lzo.semantic.files import parse_text
    from lzo.semantic.rdf import Triple,term_from_node
    grant_export(s,identifiers,name='alice')
    s.login()
    exported=s.client.post('/api/v1/query/exports',headers=s.headers(),json={
        'resource_ids':list(map(str,identifiers)),'format':fmt,'layout':'single-graph','graph_role':'accepted'})
    assert exported.status_code==202,exported.text
    identifier=UUID(exported.json()['id'])
    for _ in range(250):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==identifier)).scalar_one()
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded'
    detail=s.client.get('/api/v1/jobs/'+str(identifier)).json()
    content=s.client.get(detail['result_ref']['api_path']+'/content')
    assert content.status_code==200
    dataset=parse_text(content.text,fmt,allow_platform_skolem=True)
    assert {Triple(str(subject),str(predicate),term_from_node(value)) for subject,predicate,value,_ in dataset.quads()}==expected
