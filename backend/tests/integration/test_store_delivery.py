"""Real independent PostgreSQL stores; absent native services fail explicitly."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import pytest
from sqlalchemy import text
from lzo.db import PgUnitOfWork

@pytest.fixture
def stores():
    from dotenv import load_dotenv
    from pathlib import Path
    if os.environ.get("LZO_SERVICE_MODE") != "compose":
        load_dotenv(Path(__file__).resolve().parents[3] / ".runtime/multistore/services.env", override=True)
    from lzo.config import Settings
    from lzo.storage.registry import StorageRegistry
    settings = Settings()
    from sqlalchemy import create_engine
    for domain in ("gis", "timeseries", "catalog"):
        owner = create_engine(getattr(settings, f"{domain}_migration_database_url"))
        with owner.begin() as c:
            c.execute(text("CREATE TABLE IF NOT EXISTS delivery_test_effects(event_id uuid PRIMARY KEY)"))
            c.execute(text(f"GRANT SELECT,INSERT ON delivery_test_effects TO lzo_{domain}_app"))
        owner.dispose()
    registry = StorageRegistry.from_settings(settings)
    assert registry.mode == "split"
    yield registry
    registry.close()

def envelope(**values):
    from lzo.synchronization.contracts import SyncEnvelope
    return SyncEnvelope(event_id=values.pop("event_id", uuid4()), entity_id=values.pop("entity_id", uuid4()), aggregate_revision=values.pop("aggregate_revision", 1), target=values.pop("target", "gis"), operation="test", payload=values.pop("payload", {"value": 1}), data_nature="synthetic", **values)

def enqueue(stores, item):
    from lzo.synchronization.delivery import enqueue_delivery
    with PgUnitOfWork(stores.engine("core")) as uow:
        result = enqueue_delivery(uow, item)
        uow.commit()
    return result

def handler(connection, item):
    from lzo.synchronization.contracts import DomainReceipt
    connection.execute(text("INSERT INTO delivery_test_effects(event_id) VALUES (:id)"), {"id": item.event_id})
    return DomainReceipt(event_id=item.event_id, target=item.target, payload_hash=item.payload_hash, aggregate_revision=item.aggregate_revision, status="applied")

def test_acknowledgement_loss_replays_receipt_without_duplicate_commit(stores, monkeypatch):
    from lzo.synchronization import delivery
    item = envelope()
    enqueue(stores, item)
    original = delivery._acknowledge
    def lost(*args):
        raise ConnectionError("synthetic lost acknowledgement")
    monkeypatch.setattr(delivery, "_acknowledge", lost)
    assert delivery.deliver_once(stores, "gis", handler).failed >= 1
    monkeypatch.setattr(delivery, "_acknowledge", original)
    with stores.engine("core").begin() as c:
        c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"), {"id": item.event_id})
    assert delivery.deliver_once(stores, "gis", handler).delivered >= 1
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT count(*) FROM delivery_test_effects WHERE event_id=:id"), {"id": item.event_id}).scalar_one() == 1
    with PgUnitOfWork(stores.engine("core")) as uow:
        delivery.require_deliveries(uow, item.event_id, ("gis",))
        receipt = delivery.delivery_status(uow, item.event_id)[0]
        assert receipt.payload_hash == item.payload_hash
        assert receipt.aggregate_revision == 1
        assert receipt.attempts == 2

def test_conflicting_event_and_stale_revision_cannot_replace_head(stores):
    from lzo.synchronization.delivery import deliver_once, DeliveryConflict
    item = envelope(aggregate_revision=3)
    enqueue(stores, item)
    assert enqueue(stores, item) == item.event_id
    with pytest.raises(DeliveryConflict):
        enqueue(stores, envelope(event_id=item.event_id, entity_id=item.entity_id, payload={"value": 2}))
    deliver_once(stores, "gis", handler)
    old = envelope(entity_id=item.entity_id, aggregate_revision=2)
    enqueue(stores, old)
    deliver_once(stores, "gis", handler)
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT aggregate_revision FROM store_heads WHERE entity_id=:id"), {"id": item.entity_id}).scalar_one() == 3
        assert c.execute(text("SELECT count(*) FROM delivery_test_effects WHERE event_id=:id"), {"id": old.event_id}).scalar_one() == 0

def test_concurrent_claims_and_observable_bounded_failures(stores):
    from lzo.synchronization.delivery import deliver_once, delivery_status, require_deliveries, DeliveriesPending
    items = [envelope() for _ in range(12)]
    for item in items: enqueue(stores, item)
    with ThreadPoolExecutor(max_workers=3) as pool:
        list(pool.map(lambda _: deliver_once(stores, "gis", handler, limit=5), range(3)))
    with PgUnitOfWork(stores.engine("core")) as uow:
        for item in items: require_deliveries(uow, item.event_id, ("gis",))
    item = envelope()
    enqueue(stores, item)
    def failing(connection, item):
        connection.execute(text("INSERT INTO delivery_test_effects(event_id) VALUES (:id)"), {"id":item.event_id})
        raise RuntimeError("sensitive failure")
    for _ in range(3):
        deliver_once(stores, "gis", failing)
        with stores.engine("core").begin() as c:
            c.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"), {"id": item.event_id})
    with PgUnitOfWork(stores.engine("core")) as uow:
        result = delivery_status(uow, item.event_id)[0]
        assert result.status == "failed" and result.attempts == 3 and result.error == "RuntimeError"
        with pytest.raises(DeliveriesPending): require_deliveries(uow, item.event_id, ("gis",))
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT count(*) FROM delivery_test_effects WHERE event_id=:id"), {"id":item.event_id}).scalar_one() == 0
        assert c.execute(text("SELECT count(*) FROM store_inbox WHERE event_id=:id"), {"id":item.event_id}).scalar_one() == 0



def test_app_roles_are_isolated_and_cannot_create_domain_tables(stores):
    from sqlalchemy import create_engine
    from sqlalchemy.exc import DBAPIError
    domains = ("core", "gis", "timeseries", "catalog")
    assert stores.readiness() == dict.fromkeys(domains, "ready")
    for domain in domains:
        engine = stores.engine(domain)
        with engine.connect() as c:
            assert c.execute(text("SELECT current_database()")).scalar_one() == ("lzo" if domain == "core" else "lzo_" + domain)
            assert c.execute(text("SELECT rolsuper OR rolcreatedb OR rolcreaterole FROM pg_roles WHERE rolname=current_user")).scalar_one() is False
            with pytest.raises(DBAPIError):
                c.execute(text("CREATE TABLE forbidden_creation(id integer)"))
        for other in domains:
            if other == domain: continue
            url = engine.url.set(database="lzo" if other == "core" else "lzo_" + other)
            denied = create_engine(url)
            try:
                with pytest.raises(DBAPIError):
                    with denied.connect(): pass
            finally: denied.dispose()
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT PostGIS_Version()")).scalar_one().startswith("3.")

def test_owned_native_restart_preserves_actual_domain_commit(stores):
    import subprocess
    import sys
    from pathlib import Path
    from lzo.synchronization.delivery import deliver_once, require_deliveries
    if os.environ.get("LZO_SERVICE_MODE") == "compose":
        pytest.skip("Native ownership/restart proof is not applicable to Compose; run its own restart acceptance separately")
    item = envelope()
    enqueue(stores, item)
    deliver_once(stores, "gis", handler)
    stores.close()
    root = Path(__file__).resolve().parents[3]
    subprocess.run([sys.executable, str(root / "scripts/multistore_native.py"), "restart"], check=True)
    with stores.engine("gis").connect() as c:
        assert c.execute(text("SELECT count(*) FROM delivery_test_effects WHERE event_id=:id"), {"id":item.event_id}).scalar_one() == 1
        assert c.execute(text("SELECT payload_hash FROM store_inbox WHERE event_id=:id"), {"id":item.event_id}).scalar_one() == item.payload_hash
    with PgUnitOfWork(stores.engine("core")) as uow:
        require_deliveries(uow, item.event_id, ("gis",))


@pytest.mark.parametrize("target", ["timeseries", "catalog"])
def test_each_independent_domain_commits_and_pending_is_explicit(stores, target):
    from lzo.synchronization.delivery import deliver_once, require_deliveries, DeliveriesPending
    item = envelope(target=target)
    enqueue(stores, item)
    with PgUnitOfWork(stores.engine("core")) as uow:
        with pytest.raises(DeliveriesPending): require_deliveries(uow, item.event_id, (target,))
    assert deliver_once(stores, target, handler).delivered == 1
    with stores.engine(target).connect() as c:
        assert c.execute(text("SELECT count(*) FROM delivery_test_effects WHERE event_id=:id"), {"id":item.event_id}).scalar_one() == 1
    with PgUnitOfWork(stores.engine("core")) as uow:
        require_deliveries(uow, item.event_id, (target,))
        with pytest.raises(DeliveriesPending): require_deliveries(uow, item.event_id, (target,"gis"))
