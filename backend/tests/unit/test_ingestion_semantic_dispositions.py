from types import SimpleNamespace
from importlib import import_module


def test_every_semantic_term_has_a_mapping_or_located_exclusion():
    mapping=import_module("lzo.ingestion.mapping")
    assert hasattr(mapping,"semantic_dispositions"), "Semantic terms need explicit dispositions"
    from lzo.ingestion.schemas import FieldMap
    from lzo.ingestion.sources import digest
    profile=SimpleNamespace(class_iri="urn:Pond",fields=(FieldMap(source_path="terms."+digest("urn:area"),predicate_iri="urn:area",term_kind="rdf",multiple=True),))
    row=SimpleNamespace(locator="rdf:one",raw_fields={"contract":"semantic-v1","predicates":{
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#type":[{"kind":"iri","value":"urn:Pond"}],
        "urn:area":[{"kind":"literal","value":"1"}],
        "urn:unknown":[{"kind":"literal","value":"unmapped"}],
        "http://www.w3.org/2002/07/owl#sameAs":[{"kind":"iri","value":"urn:elsewhere"}]}})
    issues=mapping.semantic_dispositions(row,profile)
    assert {(i.field_path,i.code) for i in issues}=={("urn:unknown","SEMANTIC_TERM_UNMAPPED"),
        ("http://www.w3.org/2002/07/owl#sameAs","SEMANTIC_ALIGNMENT_REVIEW")}
    assert all(i.locator=="rdf:one" for i in issues)
