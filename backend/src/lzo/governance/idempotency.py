"""Actor-scoped command result replay in the caller's transaction."""
from collections.abc import Callable
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from lzo.contracts import ActorContext, JsonObject, UnitOfWork
from lzo.errors import Conflict
from lzo.governance.models import idempotency_results


def execute_idempotent(uow: UnitOfWork, actor: ActorContext, key: str, body_digest: str,
                       command: Callable[[], JsonObject]) -> JsonObject:
    if not key or not body_digest:
        raise ValueError("Idempotency key and body digest are required")
    with uow.session.begin_nested():
        return _execute_idempotent(uow, actor, key, body_digest, command)


def _execute_idempotent(uow, actor, key, body_digest, command):
    inserted = uow.session.execute(insert(idempotency_results).values(
        actor_id=actor.subject_id, key=key, body_digest=body_digest, result=None,
        recorded_at=datetime.now(timezone.utc)).on_conflict_do_nothing().returning(idempotency_results.c.key)).scalar_one_or_none()
    if inserted is None:
        row = uow.session.execute(select(idempotency_results).where(
            idempotency_results.c.actor_id == actor.subject_id, idempotency_results.c.key == key)
            .with_for_update()).mappings().one()
        if row["body_digest"] != body_digest:
            raise Conflict("IDEMPOTENCY_CONFLICT", "Idempotency key was used with a different body")
        return row["result"]
    result = command()
    uow.session.execute(update(idempotency_results).where(
        idempotency_results.c.actor_id == actor.subject_id, idempotency_results.c.key == key)
        .values(result=result))
    return result
