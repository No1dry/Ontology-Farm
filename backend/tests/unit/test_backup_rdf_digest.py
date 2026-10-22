"""RDF recovery equality preserves quad terms and shared blank-node identity."""
import pytest


def digest(tmp_path, name, value):
    from lzo.storage.restore import _rdf_digest
    path = tmp_path / name
    path.write_text(value, encoding='utf-8')
    return _rdf_digest(path)


def test_blank_node_renaming_and_reordered_quads_are_equivalent(tmp_path):
    first = '_:a <urn:p> _:b <urn:g1> .\n_:b <urn:q> "v" <urn:g2> .\n<urn:s> <urn:r> _:a _:graph .\n'
    renamed = '<urn:s> <urn:r> _:x _:other .\n_:y <urn:q> "v" <urn:g2> .\n_:x <urn:p> _:y <urn:g1> .\n'
    assert digest(tmp_path, 'first.nq', first) == digest(tmp_path, 'renamed.nq', renamed)


@pytest.mark.parametrize('changed', [
    '_:a <urn:p> _:b <urn:g1> .\n_:c <urn:q> "v" <urn:g2> .\n',
    '_:a <urn:p> _:b <urn:g2> .\n_:b <urn:q> "v" <urn:g1> .\n',
])
def test_shared_blank_node_links_and_named_graphs_cannot_change(tmp_path, changed):
    first = '_:a <urn:p> _:b <urn:g1> .\n_:b <urn:q> "v" <urn:g2> .\n'
    assert digest(tmp_path, 'first.nq', first) != digest(tmp_path, 'changed.nq', changed)


def test_blank_node_graph_identifier_relationship_cannot_be_split(tmp_path):
    first = '<urn:s> <urn:p> "one" _:g .\n<urn:t> <urn:p> "two" _:g .\n'
    changed = '<urn:s> <urn:p> "one" _:g .\n<urn:t> <urn:p> "two" _:h .\n'
    assert digest(tmp_path, 'first.nq', first) != digest(tmp_path, 'changed.nq', changed)


def test_ground_default_graph_is_stable_across_files_and_distinct_from_named(tmp_path):
    default = '<urn:s> <urn:p> "v" .\n'
    named = '<urn:s> <urn:p> "v" <urn:g> .\n'
    assert digest(tmp_path, 'first.nq', default) == digest(tmp_path, 'second.nq', default)
    assert digest(tmp_path, 'first.nq', default) != digest(tmp_path, 'named.nq', named)
    reserved_name = '<urn:s> <urn:p> "v" <urn:x-rdflib:default> .\n'
    assert digest(tmp_path, 'first.nq', default) != digest(tmp_path, 'reserved-name.nq', reserved_name)


@pytest.mark.parametrize('changed', [
    '<urn:s> <urn:p> "one"@zh <urn:g> .\n',
    '<urn:s> <urn:p> "two"@en <urn:g> .\n',
    '<urn:s> <urn:p> "one"^^<urn:datatype> <urn:g> .\n',
])
def test_literal_lexical_language_and_datatype_changes_are_rejected(tmp_path, changed):
    first = '<urn:s> <urn:p> "one"@en <urn:g> .\n'
    assert digest(tmp_path, 'first.nq', first) != digest(tmp_path, 'changed.nq', changed)


def test_numeric_literal_lexical_form_is_not_normalized_away(tmp_path):
    first = '<urn:s> <urn:p> "01"^^<http://www.w3.org/2001/XMLSchema#integer> <urn:g> .\n'
    changed = '<urn:s> <urn:p> "1"^^<http://www.w3.org/2001/XMLSchema#integer> <urn:g> .\n'
    assert digest(tmp_path, 'first.nq', first) != digest(tmp_path, 'changed.nq', changed)
