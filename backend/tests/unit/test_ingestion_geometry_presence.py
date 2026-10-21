"""Geometry omission never silently becomes source support withdrawal."""
from types import SimpleNamespace
import pytest


@pytest.mark.parametrize('raw',[{}, {'location':{}}, {'location':None}, {'location':[]}, {'location':'unknown'}])
def test_configured_geometry_requires_present_path(raw):
    from lzo.ingestion.mapping import geometry_value
    with pytest.raises(ValueError,match='geometry path'):
        geometry_value(SimpleNamespace(operation='upsert',raw_fields=raw,geometry=None),
            SimpleNamespace(geometry_path='location.geometry'))


def test_explicit_geometry_null_and_positive_value_are_distinct_from_missing():
    from lzo.ingestion.mapping import geometry_value
    geometry={'type':'Point','coordinates':[120,30]}
    for value in (None,geometry):
        assert geometry_value(SimpleNamespace(operation='upsert',raw_fields={'location':{'geometry':value}},geometry=None),
            SimpleNamespace(geometry_path='location.geometry'))==value


@pytest.mark.parametrize('raw',[{}, {'geometry':{'type':'Point','coordinates':[120,30]}}])
def test_authenticated_delete_never_readds_tombstone_geometry(raw):
    from lzo.ingestion.mapping import geometry_value
    assert geometry_value(SimpleNamespace(operation='delete',raw_fields=raw,geometry=raw.get('geometry')),
        SimpleNamespace(geometry_path='geometry')) is None


def test_unconfigured_geometry_keeps_normalized_file_fallback():
    from lzo.ingestion.mapping import geometry_value,path_value
    geometry={'type':'Point','coordinates':[120,30]}
    for value in (geometry,None):
        assert geometry_value(SimpleNamespace(operation='upsert',raw_fields={},geometry=value),
            SimpleNamespace(geometry_path=None))==value
    assert path_value({},'ordinary.missing') is None
