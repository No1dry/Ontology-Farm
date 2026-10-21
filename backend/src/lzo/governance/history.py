"""Current-right history queries and structural revision differences."""
import base64
import json
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import Column, ForeignKey, Table, exists, select, tuple_
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, validate_actor
from lzo.contracts import ActorContext, JsonObject, Page, RevisionRef
from lzo.errors import ApiError, Forbidden
from lzo.governance.models import metadata, revisions

revision_metadata = Table("revision_metadata", metadata,
    Column("event_id", PGUUID(as_uuid=True), ForeignKey("revisions.event_id"), primary_key=True),
    Column("task_id", PGUUID(as_uuid=True)), Column("bundle_id", PGUUID(as_uuid=True)),
    Column("evidence_refs", JSONB, nullable=False), Column("proof_refs", JSONB, nullable=False),
    extend_existing=True)
revision_visibility_dependencies = Table("revision_visibility_dependencies", metadata,
    Column("event_id", PGUUID(as_uuid=True), ForeignKey("revisions.event_id"), primary_key=True),
    Column("resource_id", PGUUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True))


@dataclass(frozen=True)
class HistoryQuery:
    resource_id: UUID | None = None
    resource_kind: str | None = None
    actor_id: UUID | None = None
    task_id: UUID | None = None
    recorded_from: datetime | None = None
    recorded_to: datetime | None = None


@dataclass(frozen=True)
class RevisionDetail:
    ref: RevisionRef
    payload: JsonObject
    actor_id: UUID
    task_id: UUID | None
    recorded_at: datetime
    effective_at: datetime | None
    bundle_id: UUID | None
    evidence_refs: list[UUID]
    proof_refs: list[UUID]


@dataclass(frozen=True)
class RevisionDiff:
    resource_id: UUID
    from_revision: int
    to_revision: int
    added: JsonObject
    removed: JsonObject
    changed: JsonObject
    actors: list[UUID]
    event_ids: list[UUID]


def _ref(row):
    return RevisionRef(row["resource_id"], row["revision"], row["event_id"])


class HistoryService:
    def __init__(self, uow, page_size=50, max_page_size=200):
        self.uow = uow
        self.page_size = min(max(page_size, 1), max_page_size)

    def _statement(self, actor):
        validate_actor(self.uow, actor)
        hidden_reference = exists(select(revision_visibility_dependencies.c.event_id).where(
            revision_visibility_dependencies.c.event_id == revisions.c.event_id,
            revision_visibility_dependencies.c.resource_id.not_in(authorized_statement(actor, "view"))))
        return select(revisions, revision_metadata.c.task_id, revision_metadata.c.bundle_id,
                      revision_metadata.c.evidence_refs, revision_metadata.c.proof_refs).select_from(
            revisions.outerjoin(revision_metadata)).where(
                revisions.c.resource_id.in_(authorized_statement(actor, "view")),
                ~hidden_reference)

    def list(self, actor: ActorContext, query: HistoryQuery, cursor: str | None) -> Page[RevisionRef]:
        statement = self._statement(actor)
        for column, value in ((revisions.c.resource_id, query.resource_id), (revisions.c.actor_id, query.actor_id),
                              (revision_metadata.c.task_id, query.task_id)):
            if value is not None:
                statement = statement.where(column == value)
        if query.resource_kind:
            statement = statement.where(revisions.c.resource_id.in_(select(resource_scopes.c.resource_id).where(
                resource_scopes.c.resource_kind == query.resource_kind)))
        for value in (query.recorded_from, query.recorded_to):
            if value is not None and value.tzinfo is None:
                raise ApiError("INVALID_HISTORY_QUERY", 422, "History timestamps must have a timezone")
        if query.recorded_from:
            statement = statement.where(revisions.c.recorded_at >= query.recorded_from)
        if query.recorded_to:
            statement = statement.where(revisions.c.recorded_at <= query.recorded_to)
        if cursor:
            try:
                data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
                at, event = datetime.fromisoformat(data[0]), UUID(data[1])
                if at.tzinfo is None:
                    raise ValueError()
            except (ValueError, TypeError, KeyError, IndexError):
                raise ApiError("INVALID_CURSOR", 422, "Invalid history cursor") from None
            statement = statement.where(tuple_(revisions.c.recorded_at, revisions.c.event_id) > (at, event))
        rows = self.uow.session.execute(statement.order_by(revisions.c.recorded_at, revisions.c.event_id)
                                        .limit(self.page_size + 1)).mappings().all()
        more = len(rows) > self.page_size
        rows = rows[:self.page_size]
        next_cursor = base64.urlsafe_b64encode(json.dumps([rows[-1]["recorded_at"].isoformat(),
            str(rows[-1]["event_id"])]).encode()).decode() if more else None
        return Page([_ref(row) for row in rows], next_cursor)

    def get(self, actor: ActorContext, resource_id: UUID, revision: int) -> RevisionDetail:
        try:
            statement = self._statement(actor)
        except Forbidden:
            raise ApiError("NOT_FOUND", 404, "Resource not found") from None
        row = self.uow.session.execute(statement.where(revisions.c.resource_id == resource_id,
            revisions.c.revision == revision)).mappings().one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Resource not found")
        return RevisionDetail(_ref(row), row["payload"], row["actor_id"], row["task_id"],
            row["recorded_at"], row["effective_at"], row["bundle_id"],
            [UUID(value) for value in row["evidence_refs"] or []], [UUID(value) for value in row["proof_refs"] or []])

    def diff(self, actor: ActorContext, resource_id: UUID, from_revision: int, to_revision: int) -> RevisionDiff:
        before = self.get(actor, resource_id, from_revision)
        after = self.get(actor, resource_id, to_revision)
        a, b = before.payload, after.payload
        return RevisionDiff(resource_id, from_revision, to_revision,
            {key: b[key] for key in b.keys() - a.keys()},
            {key: a[key] for key in a.keys() - b.keys()},
            {key: {"from": a[key], "to": b[key]} for key in a.keys() & b.keys() if a[key] != b[key]},
            list(dict.fromkeys([before.actor_id, after.actor_id])), [before.ref.event_id, after.ref.event_id])
