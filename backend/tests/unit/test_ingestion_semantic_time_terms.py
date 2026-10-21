import pytest


@pytest.mark.parametrize('value',[None,[],[{'kind':'iri','value':'urn:date'}],
    [{'kind':'literal','value':'a'},{'kind':'literal','value':'b'}],
    [{'kind':'bnode','value':'urn:lzo:source-blank:a'}]])
def test_temporal_accessor_rejects_missing_or_ambiguous_terms(value):
    from lzo.ingestion.semantic_files import temporal_literal
    raw={'terms':{'a'*64:value}}
    with pytest.raises(ValueError):temporal_literal(raw,'terms.'+'a'*64)


def test_temporal_accessor_selects_only_one_exact_registered_literal():
    from lzo.ingestion.semantic_files import temporal_literal
    value='2026-01-01T00:00:00+08:00'
    assert temporal_literal({'terms':{'b'*64:[{'kind':'literal','value':value}]}},'terms.'+'b'*64)==value
    with pytest.raises(ValueError):temporal_literal({'subject':{'value':value}},'subject.value')


@pytest.mark.parametrize('lexical,zone',[
    ('2026-01-01',None),('2026-01-01T08:00:00',None),
    ('2026-01-01T08:00:00','Unknown/Zone'),
    ('2026-11-01T01:30:00','America/New_York'),
    ('2026-03-08T02:30:00','America/New_York')])
def test_semantic_timestamp_rejects_invalid_or_ambiguous_business_time(lexical,zone):
    from lzo.ingestion.semantic_files import temporal_literal
    from lzo.ingestion.datetimes import source_datetime
    raw={'terms':{'c'*64:[{'kind':'literal','value':lexical}]}}
    with pytest.raises(ValueError):source_datetime(temporal_literal(raw,'terms.'+'c'*64),zone)
