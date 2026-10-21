"""The public evaluator executes only local authorized terms, under real budgets."""
from importlib.util import find_spec
from threading import Event, Timer

import pytest
from rdflib import Dataset, URIRef, Literal


def executor():
    assert find_spec("lzo.semantic.query"), "S05 bounded query executor is missing"
    from lzo.semantic.query import evaluate_dataset
    return evaluate_dataset


def data():
    dataset = Dataset(default_union=False)
    for i in range(12):
        dataset.default_graph.add((URIRef(f"urn:n{i}"), URIRef("urn:p"), URIRef(f"urn:n{i+1}")))
    dataset.graph(URIRef("urn:lzo:query:candidate")).add((URIRef("urn:hidden"), URIRef("urn:p"), Literal("secret")))
    return dataset


def test_default_graph_and_named_graph_are_distinct():
    run = executor()
    assert run(data(), 'ASK { <urn:hidden> ?p ?o }')["boolean"] is False
    assert run(data(), 'ASK { GRAPH ?g { <urn:hidden> ?p ?o } }')["boolean"] is True
    result = run(data(), 'SELECT ?s WHERE { ?s <urn:p> ?o } ORDER BY ?s', max_rows=2)
    assert len(result["rows"]) == 2 and result["truncated"]


@pytest.mark.parametrize("query", [
    'SELECT * WHERE { SERVICE SILENT <https://outside.invalid/> { ?s ?p ?o } }',
    'ASK { FILTER NOT EXISTS { SERVICE ?s { ?a ?b ?c } } }',
    'SELECT * FROM <file:///private> WHERE { ?s ?p ?o }',
    'ASK FROM NAMED <urn:g> { GRAPH ?g {?s ?p ?o} }',
    'INSERT DATA { <urn:a> <urn:p> <urn:b> }',
    'PREFIX x:<urn:extension:> SELECT (x:fn(1) AS ?v) WHERE {}',
    'ASK { FILTER EXISTS { FILTER(<urn:extension:fn>(1)) } }',
])
def test_forbidden_ast_operations(query):
    run = executor()
    from lzo.errors import ApiError
    with pytest.raises(ApiError) as error:
        run(data(), query)
    assert error.value.code == "SPARQL_OPERATION_FORBIDDEN"


def test_lexical_profile_casts_and_response_scoped_blank_nodes():
    run = executor()
    ds = data()
    xsd = "http://www.w3.org/2001/XMLSchema#"
    for value in ("12.50", "12.5"):
        ds.default_graph.add((URIRef("urn:decimal"), URIRef("urn:value"),
            Literal(value, datatype=URIRef(xsd+"decimal"), normalize=False)))
    result = run(ds, 'PREFIX xsd:<http://www.w3.org/2001/XMLSchema#> '
        'SELECT (COUNT(?x) AS ?n) (sameTerm("abc", "abc"^^xsd:string) AS ?same) '
        '(sameTerm("12.50"^^xsd:decimal,"12.5"^^xsd:decimal) AS ?lex) '
        '("12.50"^^xsd:decimal = "12.5"^^xsd:decimal AS ?equal) '
        '(xsd:integer("01") AS ?cast) WHERE { <urn:decimal> <urn:value> ?x }')
    row = result["rows"][0]
    assert [row[k]["value"] for k in ("n", "same", "lex", "equal", "cast")] == ["2", "true", "false", "true", "1"]
    q = 'SELECT (BNODE("a") AS ?a) (BNODE("a") AS ?b) WHERE {}'
    first, second = run(ds, q)["rows"][0], run(ds, q)["rows"][0]
    assert first["a"] == first["b"] and first["a"]["kind"] == "bnode"
    assert first["a"] != second["a"]
    graph = run(ds, 'CONSTRUCT { _:x <urn:p> ?s; <urn:q> ?s } WHERE { ?s <urn:p> ?o } LIMIT 1')["graph"]
    assert len(graph) == 2 and graph[0]["subject_bnode"] == graph[1]["subject_bnode"]


def test_describe_wildcard_scope_projection_and_modifiers():
    run = executor()
    ds = data()
    q = 'DESCRIBE * WHERE { { SELECT ?s WHERE { ?s <urn:p> ?private } ORDER BY ?s LIMIT 1 } }'
    result = run(ds, q)
    assert len(result["graph"]) == 1
    assert result["graph"][0]["subject_iri"] == "urn:n0"
    result = run(ds, 'DESCRIBE ?s WHERE { ?s <urn:p> ?o } ORDER BY DESC(?s) OFFSET 1 LIMIT 1')
    assert [t["subject_iri"] for t in result["graph"]] == ["urn:n8"]


def test_describe_explicit_projection_does_not_describe_other_subquery_columns():
    result = executor()(data(), 'DESCRIBE ?s WHERE { {SELECT ?s ?private WHERE '
        '{?s <urn:p> ?private} ORDER BY ?s LIMIT 1} }')
    assert [t['subject_iri'] for t in result['graph']] == ['urn:n0']


def test_construct_accumulation_is_bounded_and_marked():
    result = executor()(data(), 'CONSTRUCT {?s <urn:p> ?o} WHERE {?s <urn:p> ?o}', max_rows=2)
    assert len(result["graph"]) == 2 and result["truncated"]


@pytest.mark.parametrize("query", [
    'ASK { <urn:n0> <urn:p>+ <urn:missing> }',
    'SELECT (COUNT(*) AS ?n) WHERE { ?s <urn:p>* ?o }',
    'ASK { GRAPH ?g { <urn:missing> <urn:p> <urn:missing> } }',
])
def test_work_budget_never_returns_incomplete_boolean_or_aggregate(query):
    run = executor()
    from lzo.errors import ApiError
    with pytest.raises(ApiError) as error:
        run(data(), query, work_limit=1)
    assert error.value.code == "SPARQL_BUDGET_EXCEEDED"


def test_timeout_memory_and_active_cancellation_reap_owned_child(monkeypatch):
    run = executor()
    from lzo.errors import ApiError
    import subprocess,psutil
    from lzo.semantic import query_process
    original,owned=subprocess.Popen,[]
    def launch(*args,**kwargs):
        process=original(*args,**kwargs)
        owned.append(process)
        return process
    monkeypatch.setattr(query_process.subprocess,'Popen',launch)
    for options, expected in [({"timeout_ms": 1}, "SPARQL_TIMEOUT"),
                              ({"memory_mb": 1}, "SPARQL_MEMORY_EXCEEDED")]:
        with pytest.raises(ApiError) as error:
            run(data(), 'ASK {}', **options)
        assert error.value.code == expected
    cancelled = Event()
    timer = Timer(.05, cancelled.set)
    timer.start()
    try:
        with pytest.raises(ApiError) as error:
            run(data(), 'SELECT * WHERE {?a ?b ?c . ?d ?e ?f . ?g ?h ?i . ?j ?k ?l}',
                cancelled=cancelled.is_set)
        assert error.value.code == "SPARQL_CANCELLED"
    finally:
        timer.join()
    assert len(owned)==3
    assert all(process.poll() is not None and not psutil.pid_exists(process.pid) for process in owned)


def test_transfer_output_and_ast_caps_fail_before_unbounded_retention():
    run = executor()
    from lzo.errors import ApiError
    for query, options in [('ASK {}', {"input_bytes": 20}),
            ('SELECT (STRDT("' + 'x'*2000 + '", <urn:type>) AS ?x) WHERE {}', {"output_bytes": 512}),
            ('ASK { ?s (<urn:p>/<urn:p>/<urn:p>) ?o }', {"ast_limit": 2})]:
        with pytest.raises(ApiError) as error:
            run(data(), query, **options)
        assert error.value.code == "SPARQL_BUDGET_EXCEEDED"


def test_owned_prepared_query_reuses_shared_nested_literal_profile():
    from lzo.semantic.rdf import canonical_query
    from rdflib.plugins.sparql import prepareQuery
    text = 'ASK { FILTER EXISTS { FILTER(sameTerm("abc", "abc"^^<http://www.w3.org/2001/XMLSchema#string>)) } }'
    prepared = prepareQuery(text)
    normalized = canonical_query(prepared, owned=True)
    assert normalized is prepared
    assert Dataset().query(normalized).askAnswer is True
    assert Dataset().query(canonical_query(text)).askAnswer is True


def test_graph_iri_subject_retains_original_triple_field():
    result = executor()(data(), 'CONSTRUCT {?s <urn:p> ?o} WHERE {?s <urn:p> ?o} LIMIT 1')
    assert result['graph'][0]['subject_iri'].startswith('urn:n')


def test_query_child_denies_actual_python_socket_access():
    import subprocess, sys, os
    from pathlib import Path
    env = {**os.environ, 'PYTHONPATH': str(Path(__file__).resolve().parents[2] / 'src'),
        'LZO_QUERY_CHILD_MEMORY_BYTES': str(512*1024*1024)}
    result = subprocess.run([sys.executable, '-c',
        'import lzo.semantic.query_child; import socket; socket.create_connection(("127.0.0.1", 13030))'],
        env=env, capture_output=True, timeout=10)
    assert result.returncode != 0
    assert b'Query child external access denied' in result.stderr


def test_supervisor_pipe_cap_kills_and_reaps_actual_owned_process(monkeypatch):
    import subprocess, psutil
    from lzo.semantic import query_process
    from lzo.errors import ApiError
    original = subprocess.Popen
    owned = []
    def launch(command, **options):
        child = original([command[0], '-c', 'import sys,time; sys.stdout.write("x"*1000000); sys.stdout.flush(); time.sleep(30)'], **options)
        owned.append(child)
        return child
    monkeypatch.setattr(query_process.subprocess, 'Popen', launch)
    with pytest.raises(ApiError) as error:
        query_process.run_child({}, timeout_ms=5000,memory_mb=128,input_bytes=1024,output_bytes=1024)
    assert error.value.code == 'SPARQL_BUDGET_EXCEEDED'
    assert owned[0].poll() is not None and not psutil.pid_exists(owned[0].pid)


def test_hard_memory_limit_rejects_allocation_inside_actual_child(monkeypatch):
    import subprocess
    from lzo.semantic import query_process
    original = subprocess.Popen
    script = ('import sys,json; sys.stdin.read(); import lzo.semantic.query_child\n'
              'try:\n value=bytearray(128*1024*1024); capped=False\n'
              'except MemoryError:\n capped=True\n'
              'print(json.dumps({"capped":capped}))')
    def launch(command, **options):
        return original([command[0], '-c', script], **options)
    monkeypatch.setattr(query_process.subprocess, 'Popen', launch)
    assert query_process.run_child({},timeout_ms=10000,memory_mb=64,input_bytes=1024,output_bytes=1024)['capped'] is True
