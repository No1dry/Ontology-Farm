"""S02 profile and dynamic forms: fail closed without discarding source axioms."""
import pytest


def test_remote_context_is_rejected_at_any_depth():
    from lzo.semantic.semantic_profile import SemanticProfile
    report = SemanticProfile().inspect_jsonld({"@graph": [{"@context": "https://outside.invalid/context"}]})
    assert not report.allowed
    assert "REMOTE_CONTEXT_FORBIDDEN" in report.codes


def test_unsupported_axiom_retains_graph_and_subject_location():
    from lzo.semantic.semantic_profile import SemanticProfile
    report = SemanticProfile().inspect_text(
        '{"@id":"urn:graph","@graph":[{"@id":"urn:A",'
        '"http://www.w3.org/2002/07/owl#equivalentClass":{"@id":"urn:B"}}]}', "json-ld", "schema")
    assert not report.allowed
    assert report.locations[0]["graph"] == "urn:graph"
    assert report.locations[0]["subject"] == "urn:A"
    assert "UNSUPPORTED_OWL_AXIOM" in report.codes


def test_named_graphs_are_all_indexed():
    from lzo.semantic.semantic_profile import SemanticProfile
    report = SemanticProfile().inspect_text(
        '[{"@id":"urn:g1","@graph":[{"@id":"urn:A","@type":"http://www.w3.org/2002/07/owl#Class"}]},'
        '{"@id":"urn:g2","@graph":[{"@id":"urn:B","@type":"http://www.w3.org/2002/07/owl#Class"}]}]',
        "json-ld", "schema")
    assert report.allowed
    assert report.graphs == {"urn:g1": 1, "urn:g2": 1}


def test_inheritance_combines_shapes_and_preserves_exact_enum_terms():
    from lzo.ontology.schemas import ClassDef, PropertyDef, SchemaDocument
    from lzo.semantic.rdf import RdfTerm
    from lzo.semantic.semantic_profile import form_schema
    document = SchemaDocument(
        classes=(ClassDef(iri="urn:Sensor", label="Sensor"),
                 ClassDef(iri="urn:Ammonia", label="Ammonia", parents=("urn:Sensor",))),
        properties=(PropertyDef(iri="urn:value", label="Value", datatype_iri="http://www.w3.org/2001/XMLSchema#integer",
                                owner_policy_iri="urn:policy:manual", enum_values=(RdfTerm("literal", "01", "http://www.w3.org/2001/XMLSchema#integer"),)),),
        shapes='@prefix sh: <http://www.w3.org/ns/shacl#> . <urn:S> a sh:NodeShape; sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:value>; sh:minCount 1; sh:maxCount 1].')
    form = form_schema(document, "urn:Ammonia")
    assert len(form.fields) == 1
    field = form.fields[0]
    assert (field.iri, field.min_count, field.max_count, field.required) == ("urn:value", 1, 1, True)
    assert field.enum_values[0].value == "01"
    assert form.inherited_from == {"urn:value": ("urn:Sensor",)}


def test_inheritance_cycle_and_conflicting_counts_are_rejected():
    from lzo.ontology.schemas import ClassDef, SchemaDocument
    from lzo.semantic.semantic_profile import validate_document
    from lzo.errors import ApiError
    cyclic = SchemaDocument(classes=(ClassDef(iri="urn:A", label="A", parents=("urn:B",)),
                                    ClassDef(iri="urn:B", label="B", parents=("urn:A",))))
    with pytest.raises(ApiError, match="INHERITANCE_CYCLE"):
        validate_document(cyclic)
    counts = SchemaDocument(classes=(ClassDef(iri="urn:A", label="A"),),
        shapes='@prefix sh: <http://www.w3.org/ns/shacl#> . <urn:S> sh:targetClass <urn:A>; sh:property [sh:path <urn:p>; sh:minCount 2; sh:maxCount 1].')
    with pytest.raises(ApiError, match="INHERITANCE_CONFLICT"):
        validate_document(counts)


def test_imported_language_annotations_and_removed_definitions():
    from dataclasses import asdict
    from lzo.ontology.drafts import imported_definitions
    from lzo.ontology.releases import schema_graph
    from lzo.ontology.schemas import SchemaDocument
    from lzo.semantic.files import nquads
    from lzo.semantic.semantic_profile import parse
    from rdflib import URIRef, Literal
    from rdflib.namespace import RDFS
    dataset = parse('@prefix owl: <http://www.w3.org/2002/07/owl#>. '
                    '@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#>. '
                    '<urn:A> a owl:Class; rdfs:label "Sensor"@en, "传感器"@zh. <urn:B> a owl:Class.')
    classes, props, rels = imported_definitions(dataset)
    source = [{"kind": "schema", "nquads": nquads(dataset)}]
    document = SchemaDocument(classes=classes, properties=props, relations=rels)
    graph = schema_graph(document, source)
    assert (URIRef("urn:A"), RDFS.label, Literal("Sensor", lang="en")) in graph
    assert (URIRef("urn:A"), RDFS.label, Literal("传感器", lang="zh")) in graph
    graph = schema_graph(document.model_copy(update={"classes": tuple(c for c in classes if c.iri != "urn:B")}), source)
    assert not list(graph.triples((URIRef("urn:B"), None, None)))


@pytest.mark.parametrize("document", [
    {"classes": [{"iri": "relative", "label": "Invalid"}]},
    {"classes": [{"iri": "urn:A", "label": "A"}], "shapes": "invalid turtle"},
    {"properties": [
        {"iri": "urn:a", "label": "A", "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
         "owner_policy_iri": "urn:manual", "superproperties": ["urn:b"]},
        {"iri": "urn:b", "label": "B", "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
         "owner_policy_iri": "urn:manual", "superproperties": ["urn:a"]}]},
])
def test_invalid_document_and_property_cycle_return_domain_errors(document):
    from lzo.ontology.schemas import SchemaDocument
    from lzo.semantic.semantic_profile import validate_document
    from lzo.errors import ApiError
    with pytest.raises(ApiError) as caught:
        validate_document(SchemaDocument.model_validate(document))
    assert caught.value.status == 422


def test_dynamic_property_datatype_and_enum_are_real_shape_constraints():
    from uuid import uuid4
    import hashlib
    from lzo.ontology.schemas import SchemaDocument
    from lzo.semantic.semantic_profile import effective_shapes
    from lzo.semantic.semantica_adapter import SemanticaAdapter
    from lzo.contracts import ExecutionBundle
    document = SchemaDocument.model_validate({
        "classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "properties": [{"iri": "urn:value", "label": "Value",
            "datatype_iri": "http://www.w3.org/2001/XMLSchema#integer", "owner_policy_iri": "urn:manual",
            "enum_values": [{"kind": "literal", "value": "01", "datatype_iri": "http://www.w3.org/2001/XMLSchema#integer"}]}],
        "shapes": '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:shape> sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:value>].'})
    shapes = effective_shapes(document)
    assert effective_shapes(document) == shapes
    bundle = ExecutionBundle(uuid4(), uuid4(), None, uuid4(), uuid4(), uuid4(), hashlib.sha256(shapes.encode()).hexdigest())
    adapter = SemanticaAdapter()
    assert not adapter.validate('<urn:x> a <urn:Sensor>; <urn:value> "bad".', shapes, bundle).conforms
    assert adapter.validate('<urn:x> a <urn:Sensor>; <urn:value> "01"^^<http://www.w3.org/2001/XMLSchema#integer>.', shapes, bundle).conforms


@pytest.mark.parametrize("fields", [None, [], "invalid", {"urn:p": None}, {"urn:p": []},
                                   {"urn:p": "invalid"}])
def test_malformed_ui_field_mappings_are_typed_errors(fields):
    from lzo.ontology.schemas import SchemaDocument
    from lzo.semantic.semantic_profile import validate_document
    from lzo.errors import ApiError
    document = SchemaDocument.model_validate({
        "classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "shapes": '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:S> sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:p>].',
        "ui_metadata": {"fields": fields}})
    with pytest.raises(ApiError) as caught:
        validate_document(document)
    assert caught.value.status == 422
