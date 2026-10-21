"""Hand-derived finite-rule examples; no persistence doubles."""
from datetime import datetime, timezone
from importlib.util import find_spec
from uuid import UUID, uuid4

import pytest
from rdflib.namespace import OWL, RDF, RDFS, XSD

from lzo.contracts import ExecutionBundle, RevisionRef
from lzo.semantic.rdf import RdfTerm, Triple
from lzo.semantic.schemas import AssertionView, SourceSupport


def engine():
    assert find_spec("lzo.semantic.inference"), "S04 finite inference engine is missing"
    from lzo.semantic import inference
    return inference


def triple(s, p, o):
    return Triple(s, str(p), RdfTerm("iri", str(o)))


def bundle():
    return ExecutionBundle(UUID(int=1), UUID(int=2), None, UUID(int=3),
                           UUID(int=4), UUID(int=5), "0" * 64)


def assertion(s, p, o, *, state="accepted", nature="test"):
    resource = UUID(int=s)
    return AssertionView(id=uuid4(), resource_id=resource, predicate_iri=str(p),
        object=o if isinstance(o, RdfTerm) else RdfTerm("iri", str(o)),
        state=state, data_nature=nature, valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=None, supports=(SourceSupport(support_id=uuid4(), raw_value={"synthetic": True}),),
        decision_ref=None, revision=RevisionRef(resource, 1, uuid4()), bundle_id=bundle().id)


def subject(n):
    return f"urn:lzo:object:{UUID(int=n)}"


def run(assertions=(), schema=(), **budget):
    m = engine()
    return m.derive(m.InferenceInput(bundle=bundle(), input_watermark=7,
        assertions=tuple(assertions), schema_triples=tuple(schema)),
        m.InferenceBudget(max_triples=1000, max_proofs=10000, timeout_ms=5000).model_copy(update=budget))


@pytest.mark.parametrize("schema,facts,want", [
    ([triple("urn:A", RDFS.subClassOf, "urn:B"), triple("urn:B", RDFS.subClassOf, "urn:C")],
     [], triple("urn:A", RDFS.subClassOf, "urn:C")),
    ([triple("urn:A", RDFS.subClassOf, "urn:B"), triple("urn:B", RDFS.subClassOf, "urn:C")],
     [assertion(10, RDF.type, "urn:A")], triple(subject(10), RDF.type, "urn:C")),
    ([triple("urn:p", RDFS.subPropertyOf, "urn:q"), triple("urn:q", RDFS.subPropertyOf, "urn:r")],
     [assertion(10, "urn:p", "urn:b")], triple(subject(10), "urn:r", "urn:b")),
    ([triple("urn:p", OWL.inverseOf, "urn:q")],
     [assertion(10, "urn:q", "urn:b")], triple("urn:b", "urn:p", subject(10))),
    ([triple("urn:p", RDF.type, OWL.SymmetricProperty)],
     [assertion(10, "urn:p", "urn:b")], triple("urn:b", "urn:p", subject(10))),
    ([triple("urn:p", RDF.type, OWL.TransitiveProperty)],
     [assertion(10, "urn:p", subject(11)), assertion(11, "urn:p", "urn:c")],
     triple(subject(10), "urn:p", "urn:c")),
])
def test_six_explicit_rules(schema, facts, want):
    result = run(facts, schema)
    assert result.complete
    assert want in result.conclusions
    assert any(p.conclusion == want and p.schema_axiom_iris for p in result.proofs)


def test_undeclared_relations_and_domain_range_do_not_infer():
    facts = [assertion(10, "urn:p", subject(11)), assertion(11, "urn:p", "urn:c")]
    result = run(facts, [triple("urn:p", RDFS.domain, "urn:A"),
                         triple("urn:p", RDFS.range, "urn:B")])
    assert result.complete and result.conclusions == () and result.proofs == ()


def test_multiple_proofs_cycles_and_no_self_support():
    facts = [assertion(10, "urn:p", subject(11)), assertion(11, "urn:p", subject(12)),
             assertion(10, "urn:p", subject(13)), assertion(13, "urn:p", subject(12)),
             assertion(12, "urn:p", subject(10))]
    schema = [triple("urn:p", RDF.type, OWL.TransitiveProperty)]
    first = run(facts, schema)
    wanted = triple(subject(10), "urn:p", subject(12))
    assert first.complete and wanted in first.conclusions
    assert len([p for p in first.proofs if p.conclusion == wanted]) >= 2
    assert len(first.proofs) < 1000
    assert run(facts[:2], schema).complete
    empty = run([], schema)
    assert empty.complete and empty.conclusions == () and empty.proofs == ()
    repeat = run(tuple(reversed(facts)), schema)
    assert first == repeat


def test_exact_literals_are_not_collapsed_and_candidates_are_not_roots():
    schema = [triple("urn:p", RDFS.subPropertyOf, "urn:q")]
    facts = [assertion(10, "urn:p", RdfTerm("literal", "01", str(XSD.integer))),
             assertion(10, "urn:p", RdfTerm("literal", "1", str(XSD.integer))),
             assertion(11, "urn:p", "urn:secret", state="candidate")]
    result = run(facts, schema)
    assert {t.object.value for t in result.conclusions} == {"01", "1"}
    assert all(facts[2].id not in p.premise_assertion_ids for p in result.proofs)


@pytest.mark.parametrize("budget", [{"max_triples": 0}, {"max_proofs": 0}, {"timeout_ms": 0}])
def test_budget_failure_never_returns_publishable_partial_closure(budget):
    result = run([assertion(10, "urn:p", "urn:b")],
                 [triple("urn:p", RDFS.subPropertyOf, "urn:q")], **budget)
    assert result.complete is False
    assert result.conclusions == () and result.proofs == ()


def test_type_declarations_are_real_tagged_premises_and_keep_data_nature():
    m = engine()
    ref = RevisionRef(UUID(int=10), 1, UUID(int=20))
    evidence = m.TypeDeclarationEvidence(declaration_revision=ref, class_iris=("urn:A",),
        dependency_refs=(), data_nature="imported_unverified", declaration_bundle_id=UUID(int=30),
        lifecycle_state="active", lifecycle_revision=ref)
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(),
        schema_triples=(triple("urn:A", RDFS.subClassOf, "urn:B"),), type_declarations=(evidence,))
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    proof = next(p for p in result.proofs if p.conclusion == triple(subject(10), RDF.type, "urn:B"))
    assert proof.premise_assertion_ids == ()
    assert proof.premise_type_refs == (m.TypePremiseRef(declaration_revision=ref, class_iri="urn:A"),)
    assert inputs.type_declarations[0].data_nature == "imported_unverified"
    inactive = inputs.model_copy(update={"type_declarations": (evidence.model_copy(update={"lifecycle_state": "inactive"}),)})
    assert m.derive(inactive, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000)).conclusions == ()


def test_direct_premise_groups_preserve_alternative_witness_lookup():
    schema = [triple("urn:p", RDFS.subPropertyOf, "urn:q"),
              triple("urn:q", RDFS.subPropertyOf, "urn:r")]
    a = assertion(10, "urn:p", "urn:b")
    b = assertion(10, "urn:p", "urn:b")
    result = run([a, b], schema)
    q = triple(subject(10), "urn:q", "urn:b")
    r = triple(subject(10), "urn:r", "urn:b")
    alternatives = [p for p in result.proofs if p.conclusion == q]
    assert {p.premise_assertion_ids for p in alternatives} == {(a.id,), (b.id,)}
    downstream = next(p for p in result.proofs if p.conclusion == r)
    assert q in downstream.premise_conclusions
    assert all(p in {proof.id for proof in result.proofs} for p in downstream.premise_proof_ids)


def test_affected_footprint_keeps_removed_bridge_and_new_incoming_boundary():
    assert find_spec("lzo.semantic.inference_jobs"), "S04 scope and scheduling service is missing"
    from lzo.semantic.inference_jobs import affected_members
    # A-B bridge was removed. New C-B edge must pull C in, unrelated D stays out.
    assert affected_members({"a", "b", "c", "d"}, [("b", "c")],
        [{"a", "b"}, {"c"}, {"d"}], {"a"}) == {"a", "b", "c"}


def test_result_is_bound_to_the_explicit_complete_scope():
    m = engine()
    assert hasattr(m, "InferenceScope"), "Result must bind its complete input scope"
    scope = m.InferenceScope(mode="global", members=(), trigger_revisions=(),
        evaluation_time=datetime(2026, 1, 1, tzinfo=timezone.utc), next_boundary=None,
        authorization_version=1, reference_ids=(), base_generation_id=None,
        inventory_stamp="a" * 64, covered_input_event_ids=(), input_watermark=7, bundle_id=bundle().id)
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(),
        schema_triples=(), scope=scope)
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    assert result.scope_digest == scope.digest()


def test_selected_expansion_uses_visible_alternative_without_hidden_canonical_ids():
    from lzo.semantic import proofs
    assert hasattr(proofs, "select_witnesses"), "Bounded authorized witness expansion is missing"
    m = engine()
    hidden, visible = assertion(10, "urn:p", "urn:b"), assertion(10, "urn:p", "urn:b")
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(hidden, visible),
        schema_triples=(triple("urn:p", RDFS.subPropertyOf, "urn:q"),
                        triple("urn:q", RDFS.subPropertyOf, "urn:r")))
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    wanted = triple(subject(10), "urn:r", "urn:b")
    view = proofs.select_witnesses(inputs, result, wanted, visible_assertion_ids={visible.id},
        visible_type_refs=(), max_nodes=100)
    assert view.alternatives and not view.truncated
    serialized = view.model_dump_json()
    assert str(visible.id) in serialized and str(hidden.id) not in serialized
    assert view.data_natures == ("test",)
    limited = proofs.select_witnesses(inputs, result, wanted, visible_assertion_ids={visible.id},
        visible_type_refs=(), max_nodes=0)
    assert limited.truncated and limited.alternatives == ()


def test_registered_schema_axiom_identity_is_used_without_inventing_aliases():
    m = engine()
    assert hasattr(m, "SchemaAxiomEvidence"), "Exact registered S02 axiom evidence is missing"
    ax = triple("urn:p", RDFS.subPropertyOf, "urn:q")
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7,
        assertions=(assertion(10, "urn:p", "urn:b"),), schema_triples=(ax,),
        schema_axioms=(m.SchemaAxiomEvidence(iri="urn:lzo:axiom:registered-fixture", triple=ax),))
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    assert {iri for p in result.proofs for iri in p.schema_axiom_iris} == {"urn:lzo:axiom:registered-fixture"}


def test_selected_witness_requires_every_data_premise():
    from lzo.semantic.proofs import select_witnesses
    m = engine()
    left = assertion(10, "urn:p", subject(11))
    right = assertion(11, "urn:p", subject(12), nature="synthetic")
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(left, right),
        schema_triples=(triple("urn:p", RDF.type, OWL.TransitiveProperty),))
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    wanted = triple(subject(10), "urn:p", subject(12))
    partial = select_witnesses(inputs, result, wanted,
        visible_assertion_ids={left.id}, visible_type_refs=())
    assert partial.alternatives == () and partial.nodes == ()
    assert partial.data_natures == () and not partial.truncated
    complete = select_witnesses(inputs, result, wanted,
        visible_assertion_ids={left.id, right.id}, visible_type_refs=())
    assert len(complete.alternatives) == 1 and not complete.truncated
    assert {w.assertion_id for w in complete.alternatives[0].witnesses
            if w.kind == "assertion"} == {left.id, right.id}
    assert complete.data_natures == ("synthetic", "test")


def test_selected_witness_rejects_rootless_cycle_and_accepts_rooted_alternative():
    from lzo.semantic.proofs import select_witnesses
    m = engine()
    root = assertion(10, "urn:p", subject(11))
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(root,),
        schema_triples=(triple("urn:p", OWL.inverseOf, "urn:q"),))
    result = m.derive(inputs, m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000))
    wanted = triple(subject(11), "urn:q", subject(10))
    assert len(result.proofs) == 2  # p -> q -> p; the stored graph has a real root.
    rootless = select_witnesses(inputs, result, wanted,
        visible_assertion_ids=set(), visible_type_refs=(), max_nodes=100)
    assert rootless.alternatives == () and rootless.nodes == () and not rootless.truncated
    bounded = select_witnesses(inputs, result, wanted,
        visible_assertion_ids=set(), visible_type_refs=(), max_nodes=1)
    assert bounded.truncated and bounded.alternatives == () and bounded.nodes == ()
    rooted = select_witnesses(inputs, result, wanted,
        visible_assertion_ids={root.id}, visible_type_refs=(), max_nodes=100)
    assert rooted.alternatives and not rooted.truncated
    assert {w.assertion_id for node in rooted.nodes for w in node.witnesses
            if w.kind == "assertion"} == {root.id}
    assert {node.id for node in rooted.nodes} <= {p.id for p in result.proofs}
    assert rooted.data_natures == ("test",)


@pytest.mark.parametrize("limit,complete", [(4, False), (5, True)])
def test_private_run_budget_deduplicates_schema_but_preserves_exact_lexical_terms(limit, complete):
    m = engine()
    assert hasattr(m, "_TripleBudget"), "Run-wide exact triple accounting is missing"
    accounting = m._TripleBudget(max_triples=limit)
    budget = m.InferenceBudget(max_triples=limit, max_proofs=100, timeout_ms=1000)
    schema = (triple("urn:p", RDFS.subPropertyOf, "urn:q"),)
    for lexical in ("01", "1"):
        inputs = m.InferenceInput(bundle=bundle(), input_watermark=7,
            assertions=(assertion(10, "urn:p", RdfTerm("literal", lexical, str(XSD.integer))),),
            schema_triples=schema)
        result = m.derive(inputs, budget, _triple_budget=accounting)
        if lexical == "01":
            assert result.complete and len(accounting.visited) == 3
    assert result.complete is complete and len(accounting.visited) == limit
    if not complete:
        assert result.conclusions == () and result.proofs == ()


def test_private_run_budget_counts_shared_derived_schema_once():
    m = engine()
    accounting = m._TripleBudget(max_triples=3)
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(),
        schema_triples=(triple("urn:A", RDFS.subClassOf, "urn:B"),
                        triple("urn:B", RDFS.subClassOf, "urn:C")))
    budget = m.InferenceBudget(max_triples=3, max_proofs=10, timeout_ms=1000)
    for _ in range(2):
        result = m.derive(inputs, budget, _triple_budget=accounting)
        assert result.complete and result.conclusions == (triple("urn:A", RDFS.subClassOf, "urn:C"),)
    assert accounting.visited == {*inputs.schema_triples, triple("urn:A", RDFS.subClassOf, "urn:C")}


def test_persisted_scope_cannot_use_missing_or_mismatched_axiom_evidence():
    m = engine()
    scope = m.InferenceScope(mode="global", members=(), trigger_revisions=(),
        evaluation_time=datetime(2026, 1, 1, tzinfo=timezone.utc), next_boundary=None,
        authorization_version=1, reference_ids=(), base_generation_id=None,
        inventory_stamp="a" * 64, covered_input_event_ids=(), input_watermark=7, bundle_id=bundle().id)
    ax = triple("urn:p", RDFS.subPropertyOf, "urn:q")
    inputs = m.InferenceInput(bundle=bundle(), input_watermark=7, assertions=(),
        schema_triples=(ax,), scope=scope)
    budget = m.InferenceBudget(max_triples=100, max_proofs=100, timeout_ms=1000)
    with pytest.raises(ValueError, match="Schema axiom evidence"):
        m.derive(inputs, budget)
    with pytest.raises(ValueError, match="Schema axiom evidence"):
        m.derive(inputs.model_copy(update={"schema_axioms": (
            m.SchemaAxiomEvidence(iri="urn:lzo:axiom:wrong",
                triple=triple("urn:x", RDFS.subPropertyOf, "urn:q")),)}), budget)
    with pytest.raises(ValueError, match="Schema axiom evidence"):
        m.derive(inputs.model_copy(update={"schema_axioms": (
            m.SchemaAxiomEvidence(iri="urn:lzo:axiom:unregistered", triple=ax),)}), budget)


@pytest.mark.parametrize("name,value", [
    ("inference_max_triples", -1), ("inference_max_proofs", -1),
    ("inference_timeout_ms", 0), ("inference_timeout_ms", "unbounded")])
def test_operational_settings_reject_invalid_inference_budgets(name, value, tmp_path):
    from pydantic import ValidationError
    from lzo.config import Settings
    base = dict(database_url="postgresql://example.invalid/test",
        fuseki_url="http://example.invalid/dataset", oidc_issuer="https://example.invalid",
        oidc_discovery_url="https://example.invalid/config", file_root=tmp_path)
    with pytest.raises(ValidationError):
        Settings(**base, **{name: value})
