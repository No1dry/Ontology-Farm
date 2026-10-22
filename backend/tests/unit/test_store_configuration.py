import pytest
from pydantic import ValidationError
from lzo.config import Settings

BASE = dict(database_url="postgresql+psycopg://app@localhost/lzo", fuseki_url="http://localhost", oidc_issuer="test", oidc_discovery_url="http://localhost", file_root=".runtime/files")

def test_split_requires_every_explicit_domain_url():
    with pytest.raises(ValidationError, match="split"):
        Settings(**BASE, storage_mode="split", gis_database_url=None, timeseries_database_url=None, catalog_database_url=None)

def test_legacy_registry_is_explicit_and_unknown_domains_rejected():
    from lzo.storage.registry import StorageRegistry
    registry = StorageRegistry.from_settings(Settings(**BASE, storage_mode="legacy"))
    try:
        assert registry.engine("gis") is registry.engine("core")
        with pytest.raises(ValueError):
            registry.engine("unknown")
    finally:
        registry.close()

def test_split_registry_has_independent_connections():
    from lzo.storage.registry import StorageRegistry
    values = {f"{domain}_database_url": f"postgresql+psycopg://{domain}@localhost/lzo_{domain}" for domain in ("gis", "timeseries", "catalog")}
    registry = StorageRegistry.from_settings(Settings(**BASE, storage_mode="split", **values))
    try:
        assert len({id(registry.engine(d)) for d in ("core", "gis", "timeseries", "catalog")}) == 4
    finally:
        registry.close()


def test_envelope_copies_nested_payload_and_rejects_naive_time():
    from datetime import datetime
    from uuid import uuid4
    from lzo.synchronization.contracts import SyncEnvelope
    payload = {"nested": [1]}
    item = SyncEnvelope(aggregate_revision=1, entity_id=uuid4(), target="gis", operation="update", payload=payload, data_nature="synthetic")
    payload["nested"].append(2)
    returned = item.payload
    returned["nested"].append(3)
    assert item.payload == {"nested": [1]}
    with pytest.raises(ValidationError):
        SyncEnvelope(aggregate_revision=1, entity_id=uuid4(), target="gis", operation="update", payload={}, data_nature="verified", recorded_at=datetime(2026,1,1))


@pytest.mark.parametrize("nature", ["test", "imported_unverified", "verified"])
def test_envelope_preserves_existing_evidence_data_nature(nature):
    from uuid import uuid4
    from lzo.synchronization.contracts import SyncEnvelope
    item = SyncEnvelope(aggregate_revision=1, entity_id=uuid4(), target="catalog", operation="update", payload={}, data_nature=nature)
    assert item.wire()["data_nature"] == nature
