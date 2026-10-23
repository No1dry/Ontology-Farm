"""Operational failures must never masquerade as an authorized empty page."""
from types import SimpleNamespace
from uuid import uuid4
import pytest
from lzo.errors import ApiError


@pytest.mark.parametrize('boundary', ['_artifact', '_survey_evidence'])
@pytest.mark.parametrize('code,status', [('BLOB_UNAVAILABLE', 503), ('BLOB_CORRUPT', 409), ('BLOB_MISSING', 404)])
def test_asset_read_propagates_operational_failure(monkeypatch, boundary, code, status):
    from lzo.gis import service
    from lzo.semantic import facts
    from lzo.storage.registry import StorageRegistry
    from lzo.gis.repository import GisRepository
    from lzo.synchronization import delivery
    monkeypatch.setattr(facts, 'get_object_row', lambda *args: None)
    monkeypatch.setattr(service, 'split', lambda *args: True)
    monkeypatch.setattr(service, '_require_history', lambda *args: None)
    monkeypatch.setattr(delivery, 'require_deliveries', lambda *args: None)
    monkeypatch.setattr(service, 'settings_for', lambda *args: SimpleNamespace(list_page_size=10, list_max_page_size=100))
    monkeypatch.setattr(StorageRegistry, 'from_settings', lambda *args: SimpleNamespace(close=lambda: None))
    asset = dict(asset_id=str(uuid4()), artifact_version_id=str(uuid4()), sha256='a'*64,
        survey_evidence_version_id=str(uuid4()), quality='verified')
    monkeypatch.setattr(GisRepository, 'assets', lambda *args, **kwargs: [asset])
    monkeypatch.setattr(service, '_artifact', lambda *args: None)
    monkeypatch.setattr(service, '_survey_evidence', lambda *args, **kwargs: None)
    def failure(*args, **kwargs): raise ApiError(code, status, 'Synthetic byte boundary failure')
    monkeypatch.setattr(service, boundary, failure)
    uow = SimpleNamespace(session=SimpleNamespace(execute=lambda *args: SimpleNamespace(scalar_one=lambda: uuid4())))
    actor = SimpleNamespace(subject_id=uuid4(), authorization_version=1)
    with pytest.raises(ApiError, match=code):
        service.list_spatial_assets(uow, actor, uuid4())
