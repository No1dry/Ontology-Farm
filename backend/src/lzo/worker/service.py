"""Transactional queue API, current-right reads, and handler/result registries."""
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
import json
import re
from uuid import UUID, uuid4

from pydantic import BaseModel
from sqlalchemy import exists, func, insert, select, update

from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, require_permission, validate_actor
from lzo.config import Settings
from lzo.contracts import ActorContext, JobRef, JobState, JsonObject, Page, ResourceScope, UnitOfWork
from lzo.errors import ApiError, Conflict
from lzo.governance.history import revision_metadata, revision_visibility_dependencies
from lzo.governance.models import outbox_events
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.worker.models import job_attempts, job_dependencies, jobs


class JobResultRef(BaseModel):
    kind: str
    resource_id: UUID
    api_path: str


class JobView(BaseModel):
    job: JobRef
    revision: int
    progress: JsonObject
    result_ref: JobResultRef | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class Handler:
    call: Callable
    references: Callable[[JsonObject], tuple[UUID, ...]]
    action: str | Callable[[UnitOfWork, JsonObject], str]


def _resource_reference(payload):
    try:
        return (UUID(str(payload["resource_id"])),)
    except (KeyError, TypeError, ValueError):
        raise ApiError("JOB_SCOPE_REQUIRED", 422, "Job needs an explicit resource scope") from None


handlers: dict[str, Handler] = {}
result_kinds: dict[str, str] = {}
_batch: ContextVar = ContextVar("job_batch")
_execution: ContextVar = ContextVar("job_execution")


def register_handler(kind: str, handler: Callable, *,
                     references: Callable[[JsonObject], tuple[UUID, ...]] = _resource_reference,
                     action: str | Callable[[UnitOfWork, JsonObject], str] = "edit") -> None:
    """Trusted startup registration, never controlled by a job payload.

    references must enumerate *all* protected payload references; first is the
    operation's primary resource. Bundle identity is added automatically.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", kind) or not action:
        raise ValueError("Invalid handler registration")
    handlers[kind] = Handler(handler, references, action)


def resolve_action(handler: Handler, uow: UnitOfWork, payload: JsonObject) -> str:
    action = handler.action(uow, payload) if callable(handler.action) else handler.action
    if not isinstance(action, str) or not re.fullmatch(r"[a-zA-Z0-9_.-]{1,128}", action):
        raise ApiError("JOB_POLICY_CHANGED", 409, "Handler action policy is invalid")
    return action


def register_result_kind(kind: str, api_path_template: str) -> None:
    if not re.fullmatch(r"/api/v1/[a-zA-Z0-9/_-]+/\{resource_id\}(?:/[a-zA-Z0-9_-]+)?",
                        api_path_template):
        raise ValueError("Result kind requires a fixed local resource route")
    result_kinds[kind] = api_path_template


def validate_result(result: JobResultRef) -> None:
    template = result_kinds.get(result.kind)
    if template is None or result.api_path != template.format(resource_id=result.resource_id):
        raise ApiError("INVALID_RESULT_REF", 422, "Unregistered result resource path")


def check_json(value, settings):
    try:
        valid = isinstance(value, dict) and len(json.dumps(value, allow_nan=False).encode()) <= settings.job_max_json_bytes
    except (TypeError, ValueError):
        valid = False
    if not valid:
        raise ApiError("INVALID_JOB_DATA", 422, "Job data exceeds its JSON object budget")


def resource(uow, resource_id):
    row = uow.session.execute(select(resource_scopes).where(
        resource_scopes.c.resource_id == resource_id)).mappings().first()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return ResourceScope(resource_id, row["owner_scope_id"],
                         tuple(UUID(x) for x in row["related_scope_ids"]), row["security_class"])


def check_execution(uow, actor, row):
    handler = handlers.get(row["kind"])
    if handler is None:
        raise ApiError("JOB_HANDLER_UNREGISTERED", 409, "Handler is not registered")
    if resolve_action(handler, uow, row["payload"]) != row["action"]:
        raise ApiError("JOB_POLICY_CHANGED", 409, "Handler action policy changed")
    require_permission(actor, row["action"], resource(uow, row["resource_id"]), uow=uow)
    for dependency in uow.session.execute(select(job_dependencies.c.resource_id).where(
            job_dependencies.c.job_id == row["id"])).scalars():
        require_permission(actor, "view", resource(uow, dependency), uow=uow)


def append_job_event(uow, actor, row, event_type, *, dependency_recovery=None, **values):
    """Caller holds authorization -> ledger -> job locks."""
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    next_row = {**row, **values, "updated_at": now}
    payload = {"kind": row["kind"], "status": next_row["status"],
               "progress": next_row["cursor"], "error_code": next_row["error_code"],
               "initiator_id": str(row["initiator_id"]),
               "attempt": next_row["attempts"]}
    if next_row["result_ref"]:
        payload["result_ref"] = next_row["result_ref"]
    if dependency_recovery is not None:
        payload["dependency_recovery"] = dependency_recovery
    revision = append_revision(uow, actor, row["id"], row["revision"], payload, event_type)
    next_row["revision"] = revision.revision
    uow.session.execute(update(jobs).where(jobs.c.id == row["id"]).values(
        **values, updated_at=now, revision=revision.revision))
    uow.session.execute(insert(revision_metadata).values(event_id=revision.event_id,
        task_id=row["id"], bundle_id=row["bundle_id"], evidence_refs=[], proof_refs=[]))
    refs = uow.session.execute(select(job_dependencies.c.resource_id).where(
        job_dependencies.c.job_id == row["id"])).scalars().all()
    if refs:
        uow.session.execute(insert(revision_visibility_dependencies), [
            {"event_id": revision.event_id, "resource_id": ref} for ref in refs])
    return next_row


def record_attempt(uow, actor, row, phase, error_code=None):
    if row["lease_token"] is None:
        return
    uow.session.execute(insert(job_attempts).values(id=uuid4(), job_id=row["id"],
        attempt=row["attempts"], lease_token=row["lease_token"], worker_id=row["worker_id"],
        initiator_id=row["initiator_id"], authorization_version=actor.authorization_version,
        phase=phase, error_code=error_code, recorded_at=func.clock_timestamp()))


def enqueue(uow: UnitOfWork, actor: ActorContext, kind: str,
            payload: JsonObject, bundle_id: UUID | None) -> JobRef:
    return _enqueue(uow, actor, kind, payload, bundle_id)


def enqueue_at(uow: UnitOfWork, actor: ActorContext, kind: str,
               payload: JsonObject, bundle_id: UUID | None, available_at) -> JobRef:
    """Trusted delayed producer; ordinary enqueue and HTTP controls are unchanged."""
    from datetime import datetime
    if not isinstance(available_at, datetime) or available_at.tzinfo is None:
        raise ValueError("Delayed job time must include timezone")
    return _enqueue(uow, actor, kind, payload, bundle_id, available_at=available_at)


def enqueue_outbox(uow: UnitOfWork, actor: ActorContext, kind: str, outbox_id: UUID,
                   payload: JsonObject, bundle_id: UUID | None) -> JobRef:
    """Deliver a persisted outbox event once per handler kind, in caller UoW."""
    return _enqueue(uow, actor, kind, payload, bundle_id, outbox_id=outbox_id)


def _enqueue(uow, actor, kind, payload, bundle_id, outbox_id=None, available_at=None):
    settings = getattr(uow, "job_settings", None) or Settings()
    check_json(payload, settings)
    handler = handlers.get(kind)
    if handler is None:
        raise ApiError("JOB_HANDLER_UNREGISTERED", 422, "Job handler is not registered")
    references = tuple(dict.fromkeys(handler.references(payload)))
    if not references or not all(isinstance(ref, UUID) for ref in references):
        raise ApiError("JOB_SCOPE_REQUIRED", 422, "Job needs explicit resource references")
    if bundle_id is not None:
        references = tuple(dict.fromkeys((*references, bundle_id)))
    validate_actor(uow, actor)
    action = resolve_action(handler, uow, payload)
    require_permission(actor, action, resource(uow, references[0]), uow=uow)
    for ref in references:
        require_permission(actor, "view", resource(uow, ref), uow=uow)
    lock_ledger(uow)
    with uow.session.begin_nested():
        event = None
        if outbox_id:
            event = uow.session.execute(select(outbox_events).where(outbox_events.c.id == outbox_id)).mappings().first()
            if event is None or UUID(event["payload"]["resource_id"]) not in references:
                raise ApiError("INVALID_OUTBOX_JOB", 422, "Outbox event does not match the job scope")
            existing = uow.session.execute(select(jobs).where(
                jobs.c.kind == kind, jobs.c.outbox_id == outbox_id)).mappings().first()
            if existing:
                if (existing["payload"] != payload or existing["bundle_id"] != bundle_id
                        or existing["initiator_id"] != actor.subject_id):
                    raise Conflict("IDEMPOTENCY_CONFLICT", "Outbox delivery changed")
                return JobRef(existing["id"], existing["status"])
        depth = uow.session.execute(select(func.count()).select_from(jobs).where(
            jobs.c.status.in_(("queued", "running", "paused")))).scalar_one()
        if depth >= settings.job_queue_limit:
            raise ApiError("JOB_QUEUE_FULL", 503, "Job queue budget is exhausted")
        job_id = uuid4()
        scope = uow.session.execute(select(resource_scopes).where(
            resource_scopes.c.resource_id == references[0])).mappings().one()
        uow.session.execute(insert(resource_scopes).values(
            **{**dict(scope), "resource_id": job_id, "resource_kind": "job"}))
        now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
        row = dict(id=job_id, kind=kind, status="queued", revision=0,
            initiator_id=actor.subject_id, resource_id=references[0], action=action,
            bundle_id=bundle_id, payload=payload, reference_ids=[str(ref) for ref in references],
            cursor={}, result_ref=None, error_code=None,
            attempts=0, retry_count=0, max_attempts=settings.job_max_attempts,
            lease_token=None, worker_id=None, lease_expires_at=None, heartbeat_at=None,
            available_at=available_at or now, created_at=now, updated_at=now, outbox_id=outbox_id,
            outbox_position=event["position"] if event else None)
        uow.session.execute(insert(jobs).values(**row))
        uow.session.execute(insert(job_dependencies), [
            {"job_id": job_id, "resource_id": ref} for ref in references])
        append_job_event(uow, actor, row, "job.enqueued")
        return JobRef(job_id, "queued")


def checkpoint(job_id: UUID, lease_token: UUID, cursor: JsonObject) -> None:
    """Use the active batch, or open a standalone fenced progress transaction.

    Only the active-batch form is atomic with domain writes in ctx.uow.
    """
    ctx = _batch.get(None)
    if ctx is None:
        ctx = _execution.get(None)
        if ctx is not None and ctx.job_id == job_id and ctx.lease_token == lease_token:
            with ctx.batch():
                ctx.checkpoint(cursor)
            return
    if ctx is None or ctx.job_id != job_id or ctx.lease_token != lease_token:
        raise Conflict("JOB_BATCH_REQUIRED", "Checkpoint requires its active fenced batch")
    ctx.checkpoint(cursor)


def set_job_state(actor: ActorContext, job_id: UUID, state: JobState, expected_revision: int) -> JobRef:
    """Public context-bound facade; use JobService for explicit caller UoW."""
    from lzo.auth.permissions import _current_uow
    uow = _current_uow.get()
    return JobService(uow, getattr(uow, "job_settings", None) or Settings()).set_state(
        actor, job_id, state, expected_revision)


class JobService:
    def __init__(self, uow, settings):
        self.uow, self.settings = uow, settings

    def _visible(self, actor):
        validate_actor(self.uow, actor)
        hidden = exists(select(job_dependencies.c.job_id).where(
            job_dependencies.c.job_id == jobs.c.id,
            job_dependencies.c.resource_id.not_in(authorized_statement(actor, "view"))))
        return select(jobs).where(~hidden, (jobs.c.kind != "semantic.export") |
            jobs.c.id.in_(authorized_statement(actor, "view")))

    def row(self, actor, job_id, *, edit=False, lock=False):
        row = self.uow.session.execute(self._visible(actor).where(jobs.c.id == job_id)).mappings().first()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Job not found")
        if edit:
            check_execution(self.uow, actor, row)
        if lock:
            lock_ledger(self.uow)
            row = self.uow.session.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().one()
            # Another batch may have introduced a protected result dependency
            # between the initial read and this lock. The auth lock is already
            # held, so repeating visibility here preserves the global order.
            if self.uow.session.execute(self._visible(actor).where(jobs.c.id == job_id)).first() is None:
                raise ApiError("NOT_FOUND", 404, "Job not found")
            if edit:
                check_execution(self.uow, actor, row)
        return row

    def _view(self, row):
        result = JobResultRef.model_validate(row["result_ref"]) if row["result_ref"] else None
        if result:
            validate_result(result)
        return JobView(job=JobRef(row["id"], row["status"]), revision=row["revision"],
                       progress=row["cursor"], result_ref=result, error_code=row["error_code"])

    def get(self, actor, job_id):
        return self._view(self.row(actor, job_id))

    def list(self, actor, cursor=None):
        query = self._visible(actor)
        if cursor:
            try:
                after = UUID(cursor)
            except ValueError:
                raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
            query = query.where(jobs.c.id > after)
        size = min(self.settings.list_page_size, self.settings.list_max_page_size)
        rows = self.uow.session.execute(query.order_by(jobs.c.id).limit(size + 1)).mappings().all()
        return Page([self._view(row) for row in rows[:size]],
                    str(rows[size - 1]["id"]) if len(rows) > size else None)

    def set_state(self, actor, job_id, state, expected_revision):
        row = self.row(actor, job_id, edit=True, lock=True)
        if row["revision"] != expected_revision:
            raise Conflict("REVISION_CONFLICT", "Job revision changed")
        allowed = {"queued": {"paused", "cancelled"}, "running": {"paused", "cancelled"},
                   "paused": {"queued", "cancelled"}, "failed": {"queued", "cancelled"}}
        if state not in allowed.get(row["status"], set()):
            raise Conflict("JOB_STATE_CONFLICT", "Job transition is not allowed")
        retry_count = row["retry_count"]
        recovery = None
        if row["status"] == "running":
            now = self.uow.session.execute(select(func.clock_timestamp())).scalar_one()
            if row["lease_expires_at"] <= now:
                # An expired attempt has already lost its execution boundary.
                # Pausing it must not erase a crash from the failure allowance.
                record_attempt(self.uow, actor, row, "expired", "LEASE_EXPIRED")
            else:
                record_attempt(self.uow, actor, row, state)
                if state == "paused":
                    # claim() reserves one allowance. Release only this live,
                    # deliberately interrupted attempt, retaining earlier failures.
                    retry_count -= 1
        elif row["status"] == "failed" and state == "queued":
            from lzo.gis.service import retry_failed_projection_deliveries
            recovery = retry_failed_projection_deliveries(self.uow,actor,row)
            retry_count = 0
        append_job_event(self.uow, actor, row, "job." + state,
            dependency_recovery=recovery,
            status=state, lease_token=None, lease_expires_at=None, error_code=None,
            retry_count=retry_count,
            available_at=func.clock_timestamp())
        return JobRef(job_id, state)
