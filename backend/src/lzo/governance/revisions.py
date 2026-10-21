"""Append-only revisions and their atomic outbox positions."""
import hashlib
import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from lzo.contracts import ActorContext, JsonObject, RevisionRef, UnitOfWork
from lzo.errors import Conflict
from lzo.governance.models import audit_events, outbox_events, platform_watermarks, resource_heads, revisions


def lock_ledger(uow: UnitOfWork) -> None:
    """Serialize composed writes before they acquire resource or quota locks.

    append_revision keeps the ledger counter before resource-head ordering;
    repeated acquisition in the same transaction is reentrant.
    """
    uow.session.execute(select(platform_watermarks.c.singleton).where(
        platform_watermarks.c.singleton == 1).with_for_update()).scalar_one()


def append_revision(uow: UnitOfWork, actor: ActorContext, resource_id: UUID,
                    expected_revision: int, payload: JsonObject, event_type: str) -> RevisionRef:
    if expected_revision < 0:
        raise Conflict("REVISION_CONFLICT", "Revision must be nonnegative")
    session = uow.session
    with session.begin_nested():
        return _append_revision(session, actor, resource_id, expected_revision, payload, event_type)


def _append_revision(session, actor, resource_id, expected_revision, payload, event_type):
    # The singleton UPDATE serializes all writers and rolls back with the transaction.
    # A PostgreSQL sequence would leave gaps and allow a later commit to overtake an earlier one.
    position = session.execute(update(platform_watermarks).where(platform_watermarks.c.singleton == 1)
                               .values(event_position=platform_watermarks.c.event_position + 1)
                               .returning(platform_watermarks.c.event_position)).scalar_one()
    session.execute(insert(resource_heads).values(resource_id=resource_id, revision=0)
                    .on_conflict_do_nothing(index_elements=[resource_heads.c.resource_id]))
    head = session.execute(select(resource_heads).where(resource_heads.c.resource_id == resource_id)
                           .with_for_update()).mappings().one()
    if head["revision"] != expected_revision:
        raise Conflict("REVISION_CONFLICT", "Resource revision has changed",
                       {"expected_revision": expected_revision, "current_revision": head["revision"]})
    revision = expected_revision + 1
    event_id = uuid4()
    recorded_at = datetime.now(timezone.utc)
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                      ensure_ascii=False).encode("utf-8")).hexdigest()
    session.execute(insert(revisions).values(event_id=event_id, resource_id=resource_id,
        revision=revision, previous_revision=expected_revision, previous_digest=head["digest"],
        content_digest=digest, payload=payload, event_type=event_type, actor_id=actor.subject_id,
        request_id=actor.request_id, effective_at=recorded_at, recorded_at=recorded_at))
    session.execute(update(resource_heads).where(resource_heads.c.resource_id == resource_id)
        .values(revision=revision, digest=digest, event_id=event_id))
    session.execute(insert(audit_events).values(id=uuid4(), event_id=event_id,
        event_type=event_type, actor_id=actor.subject_id, request_id=actor.request_id,
        recorded_at=recorded_at))
    session.execute(insert(outbox_events).values(id=uuid4(), event_id=event_id, position=position,
        event_type=event_type, payload={"resource_id": str(resource_id), "revision": revision,
                                "event_id": str(event_id)}, recorded_at=recorded_at))
    return RevisionRef(resource_id, revision, event_id)


def advance_watermark(uow: UnitOfWork, stage: str, position: int) -> int:
    """Record one newly completed event; a consumer cannot claim a gap."""
    columns = {"input": platform_watermarks.c.input_watermark,
               "projection": platform_watermarks.c.projection_watermark,
               "inference": platform_watermarks.c.inference_watermark}
    if stage not in columns:
        raise ValueError("Unknown watermark stage")
    row = uow.session.execute(select(platform_watermarks).where(
        platform_watermarks.c.singleton == 1).with_for_update()).mappings().one()
    current = row[f"{stage}_watermark"]
    if position == current:
        return current
    if position != current + 1 or position > row["event_position"]:
        raise Conflict("WATERMARK_GAP", "Watermark must advance over the next committed event",
                       {"current_watermark": current, "requested_position": position})
    uow.session.execute(update(platform_watermarks).where(platform_watermarks.c.singleton == 1)
                        .values({columns[stage]: position}))
    return position
