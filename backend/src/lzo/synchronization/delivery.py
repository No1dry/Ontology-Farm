"""Bounded durable delivery, with target commit preceding recoverable core acknowledgement.

Core row locks use SKIP LOCKED and remain held across the bounded target call.
A process crash releases its claim automatically; no stuck lease can lose work.
Handlers must perform all domain writes on the supplied connection.
"""
import json
from uuid import UUID
from sqlalchemy import text
from lzo.synchronization.contracts import SyncEnvelope, DomainReceipt, DeliveryBatchResult

class DeliveryConflict(ValueError): pass
class DeliveriesPending(RuntimeError): pass


def enqueue_delivery(uow, envelope: SyncEnvelope) -> UUID:
    values = dict(id=envelope.event_id, target=envelope.target, envelope=json.dumps(envelope.wire()),
                  digest=envelope.envelope_hash, payload=envelope.payload_hash, entity=envelope.entity_id, revision=envelope.aggregate_revision)
    uow.session.execute(text("""INSERT INTO store_deliveries(event_id,target,envelope,envelope_hash,payload_hash,entity_id,aggregate_revision)
      VALUES(:id,:target,CAST(:envelope AS jsonb),:digest,:payload,:entity,:revision) ON CONFLICT DO NOTHING"""), values)
    stored = uow.session.execute(text("SELECT envelope_hash FROM store_deliveries WHERE event_id=:id AND target=:target"), values).scalar_one()
    if stored != envelope.envelope_hash: raise DeliveryConflict("Event identity already has different content")
    return envelope.event_id


def delivery_status(uow, event_id: UUID) -> list[DomainReceipt]:
    rows = uow.session.execute(text("SELECT * FROM store_deliveries WHERE event_id=:id ORDER BY target"), {"id": event_id}).mappings()
    return [DomainReceipt(event_id=r["event_id"], target=r["target"], payload_hash=r["payload_hash"], aggregate_revision=r["aggregate_revision"], status=r["status"], attempts=r["attempts"], error=r["error"]) for r in rows]


def require_deliveries(uow, event_id: UUID, targets: tuple[str, ...]) -> None:
    receipts = {r.target:r for r in delivery_status(uow, event_id)}
    if any(t not in receipts or receipts[t].status not in ("applied", "superseded") for t in targets):
        raise DeliveriesPending("Required domain deliveries are pending or failed")


def _apply(registry, envelope, handler):
    with registry.engine(envelope.target).begin() as connection:
        connection.execute(text("SET LOCAL statement_timeout='30s'"))
        # Serialize aggregate head decisions, including entities without a head yet.
        connection.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:id,0))"), {"id": str(envelope.entity_id)})
        previous = connection.execute(text("SELECT envelope_hash,receipt FROM store_inbox WHERE event_id=:id"), {"id": envelope.event_id}).mappings().first()
        if previous:
            if previous["envelope_hash"] != envelope.envelope_hash: raise DeliveryConflict("Inbox event content conflict")
            return DomainReceipt.model_validate(previous["receipt"])
        head = connection.execute(text("SELECT aggregate_revision,aggregate_content_hash FROM store_heads WHERE entity_id=:id"), {"id": envelope.entity_id}).mappings().first()
        status = "applied"
        if head and head["aggregate_revision"] >= envelope.aggregate_revision:
            if head["aggregate_revision"] == envelope.aggregate_revision and head["aggregate_content_hash"] != envelope.aggregate_content_hash:
                raise DeliveryConflict("Aggregate revision content conflict")
            status = "superseded"
        expected = dict(event_id=envelope.event_id, target=envelope.target, payload_hash=envelope.payload_hash, aggregate_revision=envelope.aggregate_revision, status=status)
        receipt = DomainReceipt(**expected) if status == "superseded" else handler(connection, envelope)
        if not isinstance(receipt, DomainReceipt) or any(getattr(receipt,k) != v for k,v in expected.items()):
            raise DeliveryConflict("Handler receipt does not match committed envelope")
        connection.execute(text("""INSERT INTO store_inbox(event_id,envelope_hash,payload_hash,entity_id,aggregate_revision,receipt)
          VALUES(:id,:digest,:payload,:entity,:revision,CAST(:receipt AS jsonb))"""), dict(id=envelope.event_id,digest=envelope.envelope_hash,payload=envelope.payload_hash,entity=envelope.entity_id,revision=envelope.aggregate_revision,receipt=receipt.model_dump_json()))
        if status == "applied":
            connection.execute(text("""INSERT INTO store_heads(entity_id,aggregate_revision,event_id,payload_hash,aggregate_content_hash) VALUES(:entity,:revision,:id,:payload,:content)
             ON CONFLICT(entity_id) DO UPDATE SET aggregate_revision=EXCLUDED.aggregate_revision,event_id=EXCLUDED.event_id,payload_hash=EXCLUDED.payload_hash,aggregate_content_hash=EXCLUDED.aggregate_content_hash"""), dict(entity=envelope.entity_id,revision=envelope.aggregate_revision,id=envelope.event_id,payload=envelope.payload_hash,content=envelope.aggregate_content_hash))
        return receipt


def _acknowledge(connection, receipt):
    connection.execute(text("""UPDATE store_deliveries SET status=:status,receipt=CAST(:receipt AS jsonb),error=NULL,
      attempts=attempts+1 WHERE event_id=:id AND target=:target"""), dict(status=receipt.status,receipt=receipt.model_dump_json(),id=receipt.event_id,target=receipt.target))


def deliver_once(registry, target: str, handler, limit: int = 100) -> DeliveryBatchResult:
    if target not in ("gis", "timeseries", "catalog"): raise ValueError("Unknown delivery target")
    if registry.mode != "split": raise ValueError("Domain delivery requires explicit split storage")
    if not 1 <= limit <= 1000: raise ValueError("Invalid batch limit")
    result = DeliveryBatchResult()
    for _ in range(limit):
        with registry.engine("core").begin() as connection:
            row = connection.execute(text("""SELECT * FROM store_deliveries WHERE target=:target AND status IN ('pending','retry')
              AND available_at<=clock_timestamp() ORDER BY recorded_at,event_id FOR UPDATE SKIP LOCKED LIMIT 1"""), {"target":target}).mappings().first()
            if row is None: break
            try:
                receipt = _apply(registry, SyncEnvelope.model_validate(row["envelope"]), handler)
                _acknowledge(connection, receipt)
                result.delivered += 1
            except Exception as error:
                connection.execute(text("""UPDATE store_deliveries SET attempts=attempts+1,
                  status=CASE WHEN attempts+1>=3 THEN 'failed' ELSE 'retry' END,
                  available_at=clock_timestamp()+interval '5 seconds' * power(2,attempts),error=:error
                  WHERE event_id=:id AND target=:target"""), {"id":row["event_id"],"target":target,"error":type(error).__name__})
                result.failed += 1
    return result


# Trusted worker modules register handlers; no client-supplied module loading.
handlers = {}
def register_delivery_handler(target, handler):
    if target not in ("gis", "timeseries", "catalog"): raise ValueError("Unknown target")
    handlers[target] = handler

def poll_deliveries(registry):
    return [deliver_once(registry, target, handler) for target,handler in tuple(handlers.items())]
