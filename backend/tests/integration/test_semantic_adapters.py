"""Contracts catch lexical collapse, unsafe parsing, fake validation and remote fallback."""
import hashlib
import io
import json
from uuid import uuid4

import httpx
import pytest

XSD = "http://www.w3.org/2001/XMLSchema#"
SHAPES = '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:shape> a sh:NodeShape; sh:targetNode <urn:a>; sh:property [sh:path <urn:name>; sh:minCount 1].'


def test_finite_evaluation_uses_reached_pairs_and_keeps_default_behavior(bundle, semantica_adapter):
    from lzo.semantic.files import SemanticBudgetExceeded, SemanticRecursionIncomplete
    shapes = """@prefix sh:<http://www.w3.org/ns/shacl#>.
      <urn:Root> sh:targetNode <urn:A>; sh:property <urn:P>, <urn:Q>.
      <urn:P> sh:path <urn:p>; sh:qualifiedValueShape <urn:X>;
        sh:qualifiedMaxCount 1; sh:qualifiedValueShapesDisjoint true.
      <urn:Q> sh:path <urn:q>; sh:qualifiedValueShape <urn:Y>; sh:qualifiedMinCount 0.
      <urn:X> sh:in ("not-the-IRI-value").
      <urn:Y> sh:node <urn:Y>."""
    data = "<urn:A> <urn:p> <urn:B>."
    assert semantica_adapter.validate(data, shapes, bundle, finite_evaluation=True).conforms
    reached = shapes.replace('("not-the-IRI-value")', "(<urn:B>)")
    with pytest.raises(SemanticRecursionIncomplete):
        semantica_adapter.validate(data, reached, bundle, finite_evaluation=True)
    # Default S01 has the unchanged pinned engine's recursive warning behavior.
    assert semantica_adapter.validate(data, reached, bundle).conforms
    assert semantica_adapter.validate(data, reached.replace(
        "<urn:Y> sh:node <urn:Y>", "<urn:Y> sh:deactivated true; sh:node <urn:Y>"),
        bundle, finite_evaluation=True).conforms
    chain_shapes = """@prefix sh:<http://www.w3.org/ns/shacl#>.
      <urn:Chain> a sh:PropertyShape; sh:targetNode <urn:N0>, <urn:N1>;
        sh:path <urn:next>; sh:maxCount 1; sh:property <urn:Chain>."""
    chain = " ".join(f"<urn:N{i}> <urn:next> <urn:N{i+1}>." for i in range(7))
    # Two root targets are processed separately, not both marked active.
    assert semantica_adapter.validate(chain, chain_shapes, bundle, finite_evaluation=True).conforms
    tail = chain + "<urn:N7> <urn:next> <urn:N8>, <urn:N9>."
    report = semantica_adapter.validate(tail, chain_shapes, bundle, finite_evaluation=True)
    assert not report.conforms and any(i.focus_iri == "urn:N7" for i in report.violations)
    with pytest.raises(SemanticRecursionIncomplete):
        semantica_adapter.validate(chain + "<urn:N7> <urn:next> <urn:N0>.",
            chain_shapes, bundle, finite_evaluation=True)
    too_deep = " ".join(f"<urn:N{i}> <urn:next> <urn:N{i+1}>." for i in range(17))
    with pytest.raises(SemanticBudgetExceeded, match="budget"):
        semantica_adapter.validate(too_deep, chain_shapes, bundle, finite_evaluation=True)


def test_finite_engine_hooks_restore_after_success_error_and_incompatibility(monkeypatch):
    import importlib
    import pyshacl
    from pyshacl.shape import Shape
    from pyshacl.constraints.constraint_component import ConstraintComponent
    from lzo.semantic.files import SemanticDependencyUnavailable, SemanticRecursionIncomplete
    from lzo.semantic.semantica_adapter import execute_validation
    original = Shape.validate, ConstraintComponent.recursion_triggers
    payload = {"data": "<urn:a> a <urn:A>.", "shapes": """@prefix sh:<http://www.w3.org/ns/shacl#>.
        <urn:Root> sh:targetNode <urn:a>; sh:node <urn:Loop>.
        <urn:Loop> sh:node <urn:Loop>.""", "scope": "finite-restoration", "finite_evaluation": True}
    with pytest.raises(SemanticRecursionIncomplete):
        execute_validation(payload)
    assert (Shape.validate, ConstraintComponent.recursion_triggers) == original
    finite = payload | {"shapes": payload["shapes"].replace(
        "<urn:Loop> sh:node <urn:Loop>", "<urn:Loop> sh:deactivated true; sh:node <urn:Loop>")}
    assert execute_validation(finite)["conforms"]
    assert (Shape.validate, ConstraintComponent.recursion_triggers) == original
    with monkeypatch.context() as m:
        m.setattr(pyshacl, "__version__", "incompatible-test-version")
        with pytest.raises(SemanticDependencyUnavailable):
            execute_validation(finite)
        assert (Shape.validate, ConstraintComponent.recursion_triggers) == original
    assert execute_validation(payload | {"finite_evaluation": False})["conforms"]
    assert (Shape.validate, ConstraintComponent.recursion_triggers) == original
    with monkeypatch.context() as m:
        def broken_engine(*args, **kwargs):
            raise RuntimeError("unexpected engine failure")
        m.setattr(importlib.import_module("semantica.ontology"), "run_shacl_validation", broken_engine)
        with pytest.raises(RuntimeError, match="unexpected engine failure"):
            execute_validation(finite)
        assert (Shape.validate, ConstraintComponent.recursion_triggers) == original
    with monkeypatch.context() as m:
        m.setattr(Shape, "validate", broken_engine)
        with pytest.raises(SemanticDependencyUnavailable):
            execute_validation(finite)
        assert Shape.validate is broken_engine and ConstraintComponent.recursion_triggers is original[1]


def test_trusted_root_selection_preserves_nested_constraints(bundle, semantica_adapter):
    from lzo.semantic.semantica_adapter import _prepare, root_key
    from rdflib.namespace import SH
    from rdflib import URIRef
    shapes = """@prefix sh:<http://www.w3.org/ns/shacl#>.
      [] a sh:NodeShape; sh:targetObjectsOf <urn:p>; sh:node <urn:Required>.
      <urn:Required> sh:property [sh:path <urn:status>; sh:minCount 1].
      <urn:Unrelated> a sh:NodeShape; sh:targetClass <urn:Pond>;
        sh:property [sh:path <urn:unrelated>; sh:minCount 1]."""
    prepared, _ = _prepare(shapes, str(bundle.schema_release_id), shapes=True)
    root = next(prepared.subjects(SH.targetObjectsOf, URIRef("urn:p")))
    selected = {"urn:b": (root_key(root),)}
    data = "<urn:a> <urn:p> <urn:b>. <urn:b> a <urn:Pond>."
    report = semantica_adapter.validate(data, shapes, bundle,
        focus_iris=("urn:b",), root_selection=selected)
    assert not report.conforms and report.root_selection == selected
    assert semantica_adapter.validate(data+' <urn:b> <urn:status> "ok".', shapes, bundle,
        focus_iris=("urn:b",), root_selection=selected).conforms
    with pytest.raises(ValueError, match="root selection"):
        semantica_adapter.validate(data, shapes, bundle,
            focus_iris=("urn:b",), root_selection={"urn:b": ("urn:missing",)})
    with pytest.raises(ValueError, match="root selection"):
        semantica_adapter.validate(data, shapes, bundle,
            focus_iris=("urn:a",), root_selection={"urn:a": (root_key(root),)})


def test_focused_validation_preserves_nested_constraints_and_original_identity(bundle, semantica_adapter):
    from dataclasses import replace
    shapes = """@prefix sh:<http://www.w3.org/ns/shacl#>.
      <urn:SensorShape> a sh:NodeShape; sh:targetClass <urn:Sensor>;
        sh:property [sh:path <urn:monitors>; sh:class <urn:Pond>].
      <urn:PondShape> a sh:NodeShape; sh:targetClass <urn:Pond>;
        sh:property [sh:path <urn:name>; sh:minCount 1]."""
    data = "<urn:s> a <urn:Sensor>; <urn:monitors> <urn:p>. <urn:p> a <urn:Pond>."
    pinned = replace(bundle, shapes_digest=hashlib.sha256(shapes.encode()).hexdigest())
    assert not semantica_adapter.validate(data, shapes, pinned).conforms
    focused = semantica_adapter.validate(data, shapes, pinned, focus_iris=("urn:s",))
    assert focused.conforms and focused.focus_iris == ("urn:s",)
    missing_type = "<urn:s> a <urn:Sensor>; <urn:monitors> <urn:p>."
    assert not semantica_adapter.validate(missing_type, shapes, pinned, focus_iris=("urn:s",)).conforms
    nested = shapes.replace("sh:class <urn:Pond>", "sh:class <urn:Pond>; sh:node <urn:PondShape>")
    pinned = replace(bundle, shapes_digest=hashlib.sha256(nested.encode()).hexdigest())
    full = semantica_adapter.validate(data, nested, pinned)
    focused = semantica_adapter.validate(data, nested, pinned, focus_iris=("urn:s",))
    assert not focused.conforms
    assert {i.shape_iri for i in focused.violations} <= {i.shape_iri for i in full.violations}
    assert semantica_adapter.validate(data + ' <urn:p> <urn:name> "Synthetic".',
        nested, pinned, focus_iris=("urn:s",)).conforms
    with pytest.raises(ValueError, match="digest"):
        semantica_adapter.validate(data, shapes, pinned, focus_iris=("urn:s",))


def test_focused_targets_property_roots_subclasses_and_implicit_profile(bundle, semantica_adapter):
    shapes = """@prefix sh:<http://www.w3.org/ns/shacl#>.
      @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>.
      <urn:PropertyRoot> a sh:PropertyShape; sh:targetSubjectsOf <urn:p>; sh:path <urn:p>; sh:minCount 1.
      <urn:SensorShape> a sh:NodeShape; sh:targetClass <urn:Sensor>;
        sh:property [sh:path <urn:p>; sh:minCount 1].
      <urn:ObjectRoot> a sh:NodeShape; sh:targetObjectsOf <urn:p>; sh:class <urn:Pond>.
      <urn:OtherRoot> a sh:NodeShape; sh:targetNode <urn:other>;
        sh:property [sh:path <urn:missing>; sh:minCount 1].
      <urn:Pond> a rdfs:Class, sh:NodeShape; sh:property [sh:path <urn:name>; sh:minCount 1]."""
    data = """<urn:s> a <urn:Special>; <urn:p> <urn:pobj>.
      <urn:Special> <http://www.w3.org/2000/01/rdf-schema#subClassOf> <urn:Sensor>.
      <urn:pobj> a <urn:Pond>."""
    assert semantica_adapter.validate(data, shapes, bundle, focus_iris=("urn:s",)).conforms
    assert not semantica_adapter.validate(data, shapes, bundle, focus_iris=("urn:pobj",)).conforms
    assert not semantica_adapter.validate(data, shapes, bundle, focus_iris=("urn:other",)).conforms
    indirect = shapes + """<urn:ClassMarker> rdfs:subClassOf rdfs:Class.
       <urn:Pond> a <urn:ClassMarker>."""
    with pytest.raises(ValueError, match="UNSUPPORTED_IMPLICIT_CLASS_TARGET"):
        semantica_adapter.validate(data, indirect, bundle, focus_iris=("urn:s",))


def test_remote_typed_literal_and_named_graph(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple
    graph = "urn:lzo:test:" + uuid4().hex
    triple = Triple("urn:lzo:test:a", "urn:lzo:test:area",
                    RdfTerm("literal", "12.50", XSD + "decimal", None))
    rdf_repo.replace_graph(graph, (triple,))
    assert rdf_repo.read_graph(graph) == (triple,)
    assert rdf_repo.read_graph(graph + ":candidate") == ()


def test_exact_set_digest_and_logical_query(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple, logical_graph
    graph = "urn:lzo:test:" + uuid4().hex
    values = tuple(Triple("urn:s", "urn:p", RdfTerm("literal", value, XSD + "decimal"))
                   for value in ("12.50", "12.5"))
    digest = rdf_repo.replace_graph(graph, values + values)
    assert rdf_repo.replace_graph(graph, tuple(reversed(values))) == digest
    restored = rdf_repo.read_graph(graph)
    assert set(restored) == set(values)
    pair = rdf_repo.resolve_graph(graph)
    physical = rdf_repo.select(f"SELECT (COUNT(*) AS ?n) WHERE {{ GRAPH <{pair.physical_iri}> {{ ?s ?p ?o }} }}", 10000)
    assert physical["results"]["bindings"][0]["n"]["value"] == "1"
    dataset = logical_graph(restored)
    result = list(dataset.query("""SELECT (COUNT(*) AS ?n) WHERE { <urn:s> <urn:p> ?v }"""))
    assert int(result[0][0]) == 2
    assert sorted(str(row[0]) for row in dataset.query("SELECT (STR(?v) AS ?s) WHERE { <urn:s> <urn:p> ?v }")) == ["12.5", "12.50"]
    assert bool(dataset.query(f'ASK {{ FILTER("12.50"^^<{XSD}decimal> = "12.5"^^<{XSD}decimal>) }}'))
    assert not bool(dataset.query(f'ASK {{ FILTER(sameTerm("12.50"^^<{XSD}decimal>, "12.5"^^<{XSD}decimal>)) }}'))
    assert rdf_repo.replace_graph(graph, values[:1]) != digest


def test_literal_profile_and_equivalences():
    from lzo.semantic.rdf import RdfTerm, Triple
    assert RdfTerm("literal", "hello") == RdfTerm("literal", "hello", XSD + "string")
    assert RdfTerm("literal", "你好", language="ZH-cn") == RdfTerm("literal", "你好", language="zh-CN")
    for args in (("iri", "relative"), ("literal", "bad", XSD + "integer"),
                 ("literal", "maybe", XSD + "boolean"), ("literal", "1 2", XSD + "decimal"),
                 ("literal", "1e2", XSD + "decimal"), ("literal", "NaN", XSD + "decimal"),
                 ("literal", "2026-01-01 12:00:00", XSD + "dateTime"),
                 ("literal", "x", XSD + "string", "en"), ("literal", "\ud800")):
        with pytest.raises(ValueError):
            RdfTerm(*args)
    with pytest.raises(ValueError):
        Triple("urn:s> } INSERT DATA {", "urn:p", RdfTerm("literal", "x"))


def test_rdf11_string_equivalence_in_query_constants():
    from lzo.semantic.rdf import RdfTerm, Triple, logical_graph
    graph = logical_graph((Triple("urn:s", "urn:p", RdfTerm("literal", "hello")),))
    assert bool(graph.query(f'ASK {{ <urn:s> <urn:p> ?v . FILTER(sameTerm(?v, "hello")) }}'))
    assert bool(graph.query(f'ASK {{ FILTER(sameTerm("hello", "hello"^^<{XSD}string>)) }}'))


def test_remote_failure_has_no_local_fallback():
    from lzo.semantic.fuseki import FusekiRepository
    repo = FusekiRepository("http://127.0.0.1:1/lzo", timeout_seconds=0.2)
    assert repo.health() is False
    with pytest.raises(httpx.HTTPError):
        repo.read_graph("urn:missing")


def test_companion_corruption_fails_closed(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.semantic.fuseki import RepresentationError
    graph = "urn:lzo:test:" + uuid4().hex
    rdf_repo.replace_graph(graph, (Triple("urn:s", "urn:p", RdfTerm("literal", "001", XSD + "integer")),))
    # Genuine server-side damage, not a simulated read response.
    pair = rdf_repo.resolve_graph(graph)
    rdf_repo.update(f'DROP GRAPH <{pair.metadata_iri}>')
    with pytest.raises(RepresentationError):
        rdf_repo.read_graph(graph)


def test_persistence_without_reseed(rdf_repo, request):
    from lzo.semantic.rdf import RdfTerm, Triple
    graph = "urn:lzo:test:s01:persistence:v1"
    expected = (
        Triple("urn:persist", "urn:decimal", RdfTerm("literal", "12.50", XSD + "decimal")),
        Triple("urn:persist", "urn:decimal", RdfTerm("literal", "12.5", XSD + "decimal")),
        Triple("urn:persist", "urn:integer", RdfTerm("literal", "001", XSD + "integer")),
        Triple("urn:persist", "urn:language", RdfTerm("literal", "你好", language="zh-CN")),
    )
    if not request.config.getoption("--check-persisted-rdf"):
        rdf_repo.replace_graph(graph, expected)
        rdf_repo.replace_graph(graph + ":empty", ())
    assert set(rdf_repo.read_graph(graph)) == set(expected)
    assert rdf_repo.read_graph(graph + ":empty") == ()


def test_semantica_is_real_validator(semantica_adapter, bundle):
    report = semantica_adapter.validate("<urn:a> a <urn:Device>.", SHAPES, bundle)
    assert report.conforms is False
    assert report.violations[0].path_iri == "urn:name"
    assert report.violations[0].shape_iri is not None
    assert report.violations[0].constraint_iri == "http://www.w3.org/ns/shacl#MinCountConstraintComponent"
    assert semantica_adapter.validate('<urn:a> <urn:name> "设备"@zh.', SHAPES, bundle).conforms


def test_validation_preserves_warning_and_typed_value(semantica_adapter, bundle):
    shapes = SHAPES.replace("sh:minCount 1", 'sh:datatype <http://www.w3.org/2001/XMLSchema#integer>; sh:severity sh:Warning')
    report = semantica_adapter.validate('<urn:a> <urn:name> "设备"@zh.', shapes, bundle)
    assert report.conforms is False
    assert report.violations[0].severity == "Warning"
    assert report.violations[0].value.language == "zh"
    with pytest.raises(Exception):
        semantica_adapter.validate("not turtle", SHAPES, bundle)


def test_shacl_executable_and_remote_constructs_rejected(semantica_adapter, bundle):
    for fragment in ('sh:sparql [sh:select "SELECT ?this WHERE { SERVICE <https://invalid.test/> {} }"]',
                     'sh:js [sh:jsFunctionName "evil"]', 'sh:rule [a sh:TripleRule]'):
        with pytest.raises(ValueError, match="profile"):
            semantica_adapter.validate("<urn:a> a <urn:Device>.",
                '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:s> ' + fragment + ".", bundle)


def test_parser_dataset_scopes_and_lossless_export(tmp_path):
    from lzo.artifacts.store import ArtifactStore
    from lzo.semantic.files import parse_semantic_document, semantic_file_context, export_jsonld, skolemize
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    raw = json.dumps([{"@id": "urn:g1", "@graph": [{"@id": "_:a", "urn:p": {"@value": "001", "@type": XSD + "integer"}}]},
                      {"@id": "urn:g2", "@graph": [{"@id": "_:a", "urn:p": {"@value": "你好", "@language": "zh"}}]}]).encode()
    blob = store.put(io.BytesIO(raw), "application/ld+json")
    second = store.put(io.BytesIO(raw), "application/ld+json")
    with semantic_file_context(store):
        first = parse_semantic_document(blob, "json-ld")
        other = parse_semantic_document(second, "json-ld")
    assert len(list(first.dataset.quads())) == 2
    a = skolemize(first)
    b = skolemize(other)
    assert len(a.mapping) == 1
    assert set(a.mapping.values()).isdisjoint(b.mapping.values())
    assert skolemize(first).mapping == a.mapping
    assert {str(g) for _, _, _, g in first.dataset.quads()} == {"urn:g1", "urn:g2"}
    assert '"001"' in export_jsonld(first.dataset)


def test_parser_rejects_remote_and_lossy_profile(tmp_path):
    from lzo.artifacts.store import ArtifactStore
    from lzo.semantic.files import parse_semantic_document, semantic_file_context
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    documents = [
        ("json-ld", '{"@context":"https://invalid.test/context","@id":"urn:a"}'),
        ("json-ld", '{"@context":{"@import":"https://invalid.test/context"},"@id":"urn:a"}'),
        ("turtle", '<urn:a> <http://www.w3.org/2002/07/owl#imports> <https://invalid.test/a>.'),
        ("turtle", f'<urn:a> <urn:p> "a  b"^^<{XSD}token>.'),
        ("turtle", '<urn:lzo:internal:fake> <urn:p> "x".'),
    ]
    with semantic_file_context(store):
        for format, raw in documents:
            blob = store.put(io.BytesIO(raw.encode()), "text/plain")
            with pytest.raises(ValueError):
                parse_semantic_document(blob, format)


def test_damaged_immutable_pair_is_not_silently_repaired(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.semantic.fuseki import RepresentationError
    graph = "urn:lzo:test:" + uuid4().hex
    triples = (Triple("urn:s", "urn:p", RdfTerm("literal", "original")),)
    rdf_repo.replace_graph(graph, triples)
    pair = rdf_repo.resolve_graph(graph)
    rdf_repo.update(f'DROP GRAPH <{pair.physical_iri}>')
    with pytest.raises(RepresentationError):
        rdf_repo.replace_graph(graph, triples)


def test_supported_lexical_matrix_remote_and_export(rdf_repo, tmp_path):
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.semantic.files import parse_semantic_document, semantic_file_context, export_jsonld
    from lzo.artifacts.store import ArtifactStore
    cases = [("12.50", "decimal"), ("12.5", "decimal"), ("001", "integer"),
             ("1", "boolean"), ("2026-10-20T01:02:03+08:00", "dateTime"),
             ("2026-10-20", "date"), ("1.25E2", "double"),
             ('quote " slash \\ newline\n中文', "string")]
    triples = tuple(Triple("urn:s", "urn:" + str(i), RdfTerm("literal", value, XSD + datatype))
                    for i, (value, datatype) in enumerate(cases))
    name = "urn:lzo:test:" + uuid4().hex
    rdf_repo.replace_graph(name, triples)
    assert set(rdf_repo.read_graph(name)) == set(triples)
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    raw = "\n".join(" ".join(node.n3() for node in triple.nodes()) + " ." for triple in triples).encode()
    with semantic_file_context(store):
        first = parse_semantic_document(store.put(io.BytesIO(raw), "text/turtle"), "turtle")
        exported = export_jsonld(first.dataset).encode()
        second = parse_semantic_document(store.put(io.BytesIO(exported), "application/ld+json"), "json-ld")
    assert {(str(p), str(o), str(o.datatype)) for _, p, o, _ in second.dataset.quads()} == {
        (triple.predicate_iri, triple.object.value, triple.object.datatype_iri) for triple in triples}


def test_bnode_canonicalization_and_recorded_reversal(tmp_path):
    from lzo.artifacts.store import ArtifactStore
    from lzo.semantic.files import parse_semantic_document, semantic_file_context, skolemize, reverse_skolem, nquads, _canonical
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    raw = b'_:one <urn:p> _:two . _:two <urn:p> _:one .'
    with semantic_file_context(store):
        doc = parse_semantic_document(store.put(io.BytesIO(raw), "text/turtle"), "turtle")
    first, second = skolemize(doc), skolemize(doc)
    assert first.mapping == second.mapping
    assert len(first.mapping) == 2
    assert _canonical(nquads(reverse_skolem(first))) == _canonical(nquads(doc.dataset))


def test_remote_context_and_shacl_service_make_zero_http_requests(tmp_path, semantica_adapter, bundle):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread
    from lzo.artifacts.store import ArtifactStore
    from lzo.semantic.files import parse_semantic_document, semantic_file_context
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"@context":{}}')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/escape"
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    try:
        with semantic_file_context(store):
            for document in ({"@context": url, "@id": "urn:a"}, url,
                             {"@context": {"@import": url}, "@id": "urn:a"}):
                blob = store.put(io.BytesIO(json.dumps(document).encode()), "application/ld+json")
                with pytest.raises(ValueError):
                    parse_semantic_document(blob, "json-ld")
        with pytest.raises(ValueError, match="profile"):
            semantica_adapter.validate("<urn:a> a <urn:A>.",
                f'@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:s> sh:sparql [sh:select "SELECT ?this WHERE {{ SERVICE <{url}> {{}} }}"].', bundle)
        assert requests == []
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_semantica_lexical_pattern_info_and_stable_shape(semantica_adapter, bundle):
    shapes = SHAPES.replace("sh:minCount 1", 'sh:pattern "^12[.]50$"; sh:severity sh:Info')
    report = semantica_adapter.validate(f'<urn:a> <urn:name> "12.5"^^<{XSD}decimal>.', shapes, bundle)
    repeated = semantica_adapter.validate(f'<urn:a> <urn:name> "12.5"^^<{XSD}decimal>.', shapes, bundle)
    assert report == repeated
    assert report.conforms is False and report.violations[0].severity == "Info"
    assert report.violations[0].value.datatype_iri == XSD + "decimal"
    assert semantica_adapter.validate(f'<urn:a> <urn:name> "12.50"^^<{XSD}decimal>.', shapes, bundle).conforms


def test_semantica_dependency_missing_and_report_mismatch_fail_closed(monkeypatch):
    import builtins
    import importlib
    from lzo.semantic.semantica_adapter import execute_validation
    # The package root exposes a lazy proxy, not the imported ontology module.
    ontology = importlib.import_module("semantica.ontology")
    original_import = builtins.__import__
    def deny_semantica(name, *args, **kwargs):
        if name == "semantica.ontology":
            raise ImportError("deliberately missing dependency")
        return original_import(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", deny_semantica)
        with pytest.raises(ImportError):
            execute_validation({"data": "<urn:a> a <urn:A>.", "shapes": SHAPES, "scope": "fixture"})
    genuine = ontology.run_shacl_validation
    def mismatched(*args, **kwargs):
        result = genuine(*args, **kwargs)
        result.conforms = not result.conforms
        return result
    monkeypatch.setattr(ontology, "run_shacl_validation", mismatched)
    with pytest.raises(ValueError, match="mismatch"):
        execute_validation({"data": "<urn:a> a <urn:A>.", "shapes": SHAPES, "scope": "fixture"})


def test_semantic_process_time_and_memory_limits():
    from lzo.semantic.files import bounded_json
    payload = {"operation": "parse", "raw": '<urn:a> <urn:p> "x".', "format": "turtle"}
    with pytest.raises(ValueError, match="time budget"):
        bounded_json("lzo.semantic.files", payload, timeout_seconds=0.001)
    with pytest.raises(ValueError, match="memory budget"):
        bounded_json("lzo.semantic.files", payload, memory_mb=1)


def test_execution_incomplete_types_preserve_legacy_errors_and_programming_failures(monkeypatch):
    import os
    from contextlib import nullcontext
    from types import SimpleNamespace
    from lzo.semantic import files
    payload = {"data": "nonempty"}
    with monkeypatch.context() as patch:
        patch.setattr(files, "MAX_BYTES", 1)
        with pytest.raises(files.SemanticExecutionIncomplete) as caught:
            files.bounded_json("unused", payload)
        assert isinstance(caught.value, ValueError)
    def unavailable(*args, **kwargs):
        raise OSError("test process launch failure")
    with monkeypatch.context() as patch:
        patch.setattr(files.subprocess, "Popen", unavailable)
        with pytest.raises(files.SemanticExecutionIncomplete) as caught:
            files.bounded_json("unused", payload)
        assert isinstance(caught.value, RuntimeError)
    def child(error, returncode=1):
        return nullcontext(SimpleNamespace(pid=os.getpid(), returncode=returncode,
            poll=lambda: returncode, communicate=lambda encoded: (json.dumps(error), "")))
    with monkeypatch.context() as patch:
        patch.setattr(files.subprocess, "Popen", lambda *a, **k: child(
            {"type": "ImportError", "error": "missing dependency"}))
        with pytest.raises(files.SemanticExecutionIncomplete) as caught:
            files.bounded_json("unused", payload)
        assert isinstance(caught.value, ImportError)
        patch.setattr(files.subprocess, "Popen", lambda *a, **k: child(
            {"type": "ValueError", "error": "unexpected programming failure"}))
        with pytest.raises(ValueError) as caught:
            files.bounded_json("unused", payload)
        assert not isinstance(caught.value, files.SemanticExecutionIncomplete)


def test_validation_consumes_trusted_reconstructed_skolem_terms(semantica_adapter, bundle):
    shapes = SHAPES.replace("<urn:a>", "<urn:lzo:skolem:known-document:node>")
    report = semantica_adapter.validate(
        "<urn:lzo:skolem:known-document:node> a <urn:Device>.", shapes, bundle)
    assert not report.conforms
    assert report.violations[0].focus_iri == "urn:lzo:skolem:known-document:node"


def test_pair_verification_failure_does_not_publish(rdf_repo, settings):
    from lzo.semantic.fuseki import FusekiRepository, RepresentationError
    from lzo.semantic.rdf import Triple, RdfTerm
    class RejectPair(FusekiRepository):
        def read_pair(self, pair):
            super().read_pair(pair)
            raise RepresentationError("controlled verification rejection")
    graph = "urn:lzo:test:" + uuid4().hex
    with pytest.raises(RepresentationError):
        RejectPair(settings.fuseki_url).replace_graph(graph, (Triple("urn:s", "urn:p", RdfTerm("literal", "x")),))
    assert rdf_repo.read_graph(graph) == ()


def test_missing_published_pointer_is_not_an_absent_graph(rdf_repo):
    from lzo.semantic.fuseki import CATALOG, V, RepresentationError
    from lzo.semantic.rdf import Triple, RdfTerm
    graph = "urn:lzo:test:" + uuid4().hex
    rdf_repo.replace_graph(graph, (Triple("urn:s", "urn:p", RdfTerm("literal", "x")),))
    rdf_repo.update(f'DELETE WHERE {{ GRAPH <{CATALOG}> {{ <{graph}> <{V.current}> ?pointer }} }}')
    with pytest.raises(RepresentationError):
        rdf_repo.read_graph(graph)


@pytest.mark.parametrize("graph_id,context", [
    ("urn:x-rdflib:default", {}),
    ("reserved:default", {"reserved": "urn:x-rdflib:"}),
])
def test_reserved_dataset_default_graph_is_rejected_before_merge(tmp_path, graph_id, context):
    from lzo.artifacts.store import ArtifactStore
    from lzo.semantic.files import parse_semantic_document, semantic_file_context
    document = {"@context": context, "@graph": [
        {"@id": "urn:s", "urn:p": [{"@value": "default"}]},
        {"@id": graph_id, "@graph": [{"@id": "urn:s", "urn:p": [{"@value": "named"}]}]},
    ]}
    store = ArtifactStore(tmp_path / "files", max_upload_bytes=100000)
    with semantic_file_context(store):
        blob = store.put(io.BytesIO(json.dumps(document).encode()), "application/ld+json")
        with pytest.raises(ValueError, match="Reserved dataset graph"):
            parse_semantic_document(blob, "json-ld")


@pytest.mark.parametrize("kind,conforms", [("BlankNode", True), ("IRI", False)])
@pytest.mark.parametrize("location", ["focus", "value"])
def test_blank_node_kind_is_evaluated_before_report_mapping(semantica_adapter, bundle, kind, conforms, location):
    data = "_:b a <urn:Device>." if location == "focus" else "<urn:a> a <urn:Device>; <urn:child> _:b."
    constraint = f"sh:nodeKind sh:{kind}" if location == "focus" else f"sh:property [sh:path <urn:child>; sh:nodeKind sh:{kind}]"
    shapes = f"@prefix sh: <http://www.w3.org/ns/shacl#>. [] a sh:NodeShape; sh:targetClass <urn:Device>; {constraint}."
    result = semantica_adapter.validate(data, shapes, bundle)
    assert result.conforms is conforms
    assert result == semantica_adapter.validate(data, shapes, bundle)
    if not conforms:
        issue = result.violations[0]
        assert issue.shape_iri.startswith("urn:lzo:skolem:")
        assert issue.value.kind == "iri" and issue.value.value.startswith("urn:lzo:skolem:")
        if location == "focus":
            assert issue.focus_iri == issue.value.value


@pytest.mark.parametrize("value", ["!!!", "Zg", "Zg===", "=Zg=", "Zg==AA==", "Zh==", "Zm9=", "Zg==\u00a0"])
def test_invalid_base64_lexical_forms_are_rejected(value):
    from lzo.semantic.rdf import RdfTerm
    with pytest.raises(ValueError):
        RdfTerm("literal", value, XSD + "base64Binary")


@pytest.mark.parametrize("value", ["", " \t\r\n", "Zg==", "Zm8=", "Zm9v", " Z g =\t=\r\n"])
def test_base64_legal_whitespace_and_padding_preserve_spelling(value):
    from lzo.semantic.rdf import RdfTerm
    term = RdfTerm("literal", value, XSD + "base64Binary")
    assert term.value == value and str(term.node()) == value


def test_base64_whitespace_remains_exact_after_remote_persistence(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple
    graph = "urn:lzo:test:" + uuid4().hex
    triples = tuple(Triple("urn:s", "urn:p", RdfTerm("literal", value, XSD + "base64Binary"))
                    for value in ("Zg==", " Z g =\t=\r\n"))
    rdf_repo.replace_graph(graph, triples)
    assert set(rdf_repo.read_graph(graph)) == set(triples)


def test_blank_rdf_list_members_and_shape_targets_keep_node_kind(semantica_adapter, bundle):
    data = '<urn:a> <urn:items> ( "x" "y" ).'
    shapes = '''@prefix sh: <http://www.w3.org/ns/shacl#>.
        @prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#>.
        [] a sh:NodeShape; sh:targetSubjectsOf rdf:first; sh:nodeKind sh:BlankNode.
        [] a sh:NodeShape; sh:targetNode _:target; sh:nodeKind sh:BlankNode.'''
    assert semantica_adapter.validate(data, shapes, bundle).conforms
