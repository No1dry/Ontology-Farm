"""S05 real PG/Fuseki authorization precedes query evaluation and proof selection."""
from uuid import UUID, uuid4
import pytest
from test_inference_retraction import inference_case


@pytest.fixture
def query_case(inference_case):
    s = inference_case
    for name in ("a", "restricted-b", "c"):
        s.create_node(name)
    s.ab, s.bc = s.edge("a", "restricted-b"), s.edge("restricted-b", "c")
    s.recompute()
    for name in ("a", "c"):
        s.ids_by_name[name] = s.ids[name]
    s.a_grant = s.grant("nobody", "viewer", resource="a")
    s.c_grant = s.grant("nobody", "viewer", resource="c")
    s.login("nobody")
    def query(text, **options):
        return s.client.post("/api/v1/query/sparql", headers=s.headers(),
            json={"query": text, **options})
    s.query = query
    return s


def test_hidden_nodes_absent_before_aggregation_paths_graphs_and_describe(query_case):
    s = query_case
    count = s.query('SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a <urn:Node> }')
    assert count.status_code == 200, count.text
    assert count.json()["rows"][0]["n"]["value"] == "2"
    a, b, c = (f"urn:lzo:object:{s.ids[name]}" for name in ("a", "restricted-b", "c"))
    queries = [f'ASK {{ <{a}> <urn:connects>+ <{c}> }}',
        f'ASK {{ <{b}> ?p ?o }}',
        f'ASK {{ GRAPH ?g {{ <{b}> ?p ?o }} }}']
    for query in queries:
        result = s.query(query)
        assert result.status_code == 200, result.text
        assert result.json()["boolean"] is False
    for query in ['SELECT ?s ?o WHERE { ?s a <urn:Node> OPTIONAL {?s <urn:connects> ?o} }',
            'SELECT ?s WHERE { {SELECT ?s WHERE {?s a <urn:Node>}} MINUS {?s <urn:connects> ?o} }',
            'CONSTRUCT {?s ?p ?o} WHERE {?s ?p ?o}', 'DESCRIBE * WHERE {?s ?p ?o}',
            'SELECT * WHERE { GRAPH ?g {?s ?p ?o} }']:
        response = s.query(query)
        assert response.status_code == 200, response.text
        assert b not in response.text and "restricted-b" not in response.text
        assert "internal" not in response.text
    from lzo.semantic.inference import derived_assertion_id
    from lzo.semantic.rdf import Triple, RdfTerm
    assertion = derived_assertion_id(Triple(a, "urn:connects", RdfTerm("iri", c)))
    assert s.client.get(f"/api/v1/graph/proofs/{assertion}").status_code == 404
    neighborhood = s.client.get("/api/v1/graph/neighborhood", params={"resource_id": str(s.ids['a']), "hops": 3})
    assert neighborhood.status_code == 200, neighborhood.text
    assert [n["id"] for n in neighborhood.json()["nodes"]] == [str(s.ids["a"])]
    assert not neighborhood.json()["edges"]
    assert s.query('ASK {}', mode="review").status_code == 403
    assert s.query('SELECT * WHERE { SERVICE <https://outside.invalid> {?s ?p ?o} }').json()["code"] == "SPARQL_OPERATION_FORBIDDEN"
    # The same old browser session sees current revocation; no stale dataset cache.
    s.revoke(s.a_grant)
    result = s.query('SELECT (COUNT(?s) AS ?n) WHERE {?s a <urn:Node>}')
    assert result.status_code == 200, result.text
    assert result.json()["rows"][0]["n"]["value"] == "1"


def test_current_visible_alternative_proof_and_withdrawal(query_case):
    s = query_case
    s.login()
    s.create_node("d")
    ad, dc = s.edge("a", "d"), s.edge("d", "c")
    s.recompute()
    s.ids_by_name["d"] = s.ids["d"]
    s.grant("nobody", "viewer", resource="d")
    s.login("nobody")
    a, c = (f"urn:lzo:object:{s.ids[name]}" for name in ("a", "c"))
    result = s.query(f'ASK {{ <{a}> <urn:connects> <{c}> }}')
    assert result.status_code == 200, result.text
    assert result.json()["boolean"] is True
    from lzo.semantic.inference import derived_assertion_id
    from lzo.semantic.rdf import Triple, RdfTerm
    assertion = derived_assertion_id(Triple(a, "urn:connects", RdfTerm("iri", c)))
    proof = s.client.get(f"/api/v1/graph/proofs/{assertion}")
    assert proof.status_code == 200, proof.text
    assert str(s.ids["restricted-b"]) not in proof.text
    assert s.ab["assertion_id"] not in proof.text and s.bc["assertion_id"] not in proof.text
    assert ad["assertion_id"] in proof.text and dc["assertion_id"] in proof.text
    s.login()
    s.withdraw(dc)  # no projection/recompute: stored old RDF must not grant truth
    s.login("nobody")
    result = s.query(f'ASK {{ <{a}> <urn:connects> <{c}> }}')
    assert result.status_code in (200, 409), result.text
    if result.status_code == 200:
        assert result.json()["boolean"] is False
    assert s.client.get(f"/api/v1/graph/proofs/{assertion}").status_code == 404


def test_current_time_expiry_without_projection_or_new_event(inference_case):
    from datetime import timedelta
    from sqlalchemy import select, func
    import time
    s = inference_case
    for name in ("a", "b", "c"):
        s.create_node(name)
    # Establish the non-expiring part before starting the finite validity
    # measurement. Native split-store projection can exceed the old 45s window.
    s.edge("b", "c")
    s.recompute()
    with s.transaction() as uow:
        end = uow.session.execute(select(func.clock_timestamp())).scalar_one() + timedelta(seconds=180)
    s.edge("a", "b", valid_to=end.isoformat())
    s.recompute()
    assert s.client.get(f'/api/v1/objects/{s.ids["a"]}').json()['projection_state'] == 'synced'
    query = f'ASK {{ <urn:lzo:object:{s.ids["a"]}> <urn:connects> <urn:lzo:object:{s.ids["c"]}> }}'
    result = s.client.post('/api/v1/query/sparql', headers=s.headers(), json={"query": query})
    assert result.status_code == 200, result.text
    assert result.json()["boolean"] is True
    from lzo.governance.models import platform_watermarks
    with s.transaction() as uow:
        before = tuple(uow.session.execute(select(platform_watermarks.c.event_position,
            platform_watermarks.c.projection_watermark)).one())
        delay = (end-uow.session.execute(select(func.clock_timestamp())).scalar_one()).total_seconds()
    if delay > 0:
        time.sleep(delay+.1)
    expired = s.client.post('/api/v1/query/sparql', headers=s.headers(), json={"query": query})
    assert expired.status_code == 200, expired.text
    assert expired.json()["boolean"] is False
    with s.transaction() as uow:
        assert tuple(uow.session.execute(select(platform_watermarks.c.event_position,
            platform_watermarks.c.projection_watermark)).one()) == before


def test_revocation_commits_while_owned_child_runs_and_result_is_rebuilt(query_case, monkeypatch):
    from threading import Event, Thread
    from lzo.semantic import query
    s = query_case
    original = query.run_child
    entered, revoked = Event(), Event()
    failures = []
    def revoke():
        try:
            assert entered.wait(10)
            s.revoke(s.a_grant)
        except BaseException as error:
            failures.append(error)
        finally:
            revoked.set()
    worker = Thread(target=revoke)
    worker.start()
    def supervised(payload, **kwargs):
        from lzo.semantic import query_process
        launch=query_process.subprocess.Popen
        children=[]
        def observed(*args,**options):
            child=launch(*args,**options)
            children.append(child)
            return child
        cancelled = kwargs.get('cancelled')
        def check():
            if children and not entered.is_set():
                assert children[0].poll() is None
                entered.set()  # first supervisor poll: real owned child exists
                assert revoked.wait(3), 'query held authorization lock during child execution'
            return cancelled() if cancelled else False
        with monkeypatch.context() as patch:
            patch.setattr(query_process.subprocess,'Popen',observed)
            return original(payload, **{**kwargs, 'cancelled':check})
    monkeypatch.setattr(query, 'run_child', supervised)
    try:
        response = s.query('SELECT (COUNT(?s) AS ?n) WHERE {?s a <urn:Node>}')
        assert response.status_code == 200, response.text
        assert response.json()['rows'][0]['n']['value'] == '1'
    finally:
        entered.set()
        worker.join(10)
    assert not worker.is_alive() and not failures
