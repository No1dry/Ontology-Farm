"""Exact RDF lexical values and bounded repeatable normalization."""
import pytest
from lzo.ingestion.schemas import FieldMap


def test_typed_repeatable_mapping_preserves_language_lexical_and_iri():
    from lzo.ingestion import mapping
    assert hasattr(mapping, "field_terms"), "Typed mapping normalization is missing"
    field=FieldMap(source_path="values",predicate_iri="urn:value",term_kind="rdf",multiple=True)
    values=[{"kind":"literal","value":"01.00","datatype_iri":"http://www.w3.org/2001/XMLSchema#decimal"},
        {"kind":"literal","value":"pond","language":"en"},
        {"kind":"literal","value":"塘","language":"zh"},
        {"kind":"iri","value":"urn:registered:vocabulary"}]
    first=mapping.field_terms(field, {"values":values})
    assert {t.value for t in first}=={"01.00","pond","塘","urn:registered:vocabulary"}
    assert next(t for t in first if t.value=="01.00").datatype_iri=="http://www.w3.org/2001/XMLSchema#decimal"
    assert next(t for t in first if t.value=="塘").language=="zh"
    assert mapping.field_terms(field,{"values":list(reversed(values))})==first
    assert mapping.field_terms(field,{"values":[*values,values[0]]})==first
    with pytest.raises(ValueError):mapping.field_terms(field,{"values":values*300})
    with pytest.raises(ValueError):mapping.field_terms(field,{"values":[{"kind":"literal","value":"x","language":"en","datatype_iri":"urn:type"}]})


def test_legacy_scalar_profile_serialization_has_no_extension_keys():
    field=FieldMap(source_path="value",predicate_iri="urn:value")
    assert field.model_dump(mode="json")=={"source_path":"value","predicate_iri":"urn:value","datatype_iri":None,
        "source_unit":None,"target_unit":None,"expression":None}


def test_source_scoped_blank_reference_keeps_original_kind():
    from lzo.ingestion.mapping import field_terms
    field=FieldMap(source_path='values',predicate_iri='urn:next',term_kind='reference',multiple=True)
    value='urn:lzo:source-blank:source:artifact:digest:local'
    term=field_terms(field,{'values':[{'kind':'bnode','value':value}]})[0]
    assert term.kind=='bnode' and term.value==value
    with pytest.raises(ValueError):field_terms(field,{'values':[{'kind':'bnode','value':'unscoped'}]})
    with pytest.raises(ValueError):field_terms(field,{'values':[{'kind':'literal','value':value}]})
