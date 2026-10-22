"""Optional reference pins must match the omitted-empty wire contract."""
from uuid import UUID

from lzo.contracts import RevisionRef
from lzo.ontology.schemas import SchemaDocument


def test_reference_versions_have_no_materialized_wire_default():
    schema = SchemaDocument.model_json_schema()
    assert 'reference_versions' not in schema.get('required', [])
    # The configured TS generator materializes JSON Schema defaults as required
    # properties. This field is omitted on the wire, so it cannot advertise one.
    assert 'default' not in schema['properties']['reference_versions']
    missing = SchemaDocument.model_validate({})
    explicit_empty = SchemaDocument.model_validate({'reference_versions': []})
    assert missing.reference_versions == explicit_empty.reference_versions == ()
    assert 'reference_versions' not in missing.model_dump(mode='json')
    assert missing.model_dump(mode='json') == explicit_empty.model_dump(mode='json')


def test_nonempty_reference_pin_survives_wire_round_trip():
    pin = RevisionRef(UUID('11111111-1111-4111-8111-111111111111'), 3,
        UUID('22222222-2222-4222-8222-222222222222'))
    document = SchemaDocument(reference_versions=(pin,))
    wire = document.model_dump(mode='json')
    assert wire['reference_versions'] == [{'resource_id': '11111111-1111-4111-8111-111111111111',
        'revision': 3, 'event_id': '22222222-2222-4222-8222-222222222222'}]
    assert SchemaDocument.model_validate(wire).reference_versions == (pin,)
