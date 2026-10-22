"""Trusted immutable-revision producers, monotonic projection and dirty fences.

No public command accepts triples. Registered deployment code derives a full
generation from the exact PG revision and explicitly pinned dependencies.
"""
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

import httpx
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert

from lzo.auth.permissions import require_permission, validate_actor
from lzo.contracts import RevisionRef, UnitOfWork
from lzo.errors import ApiError
from lzo.governance.history import revision_visibility_dependencies
from lzo.governance.models import outbox_events, platform_watermarks, revisions
from lzo.governance.revisions import lock_ledger
from lzo.semantic.fuseki import FusekiRepository, RepresentationError, generation_digest, graph_iri
from lzo.semantic.models import (projection_events, projection_heads, projection_classifications,
                                 inference_fence, inference_inputs, inference_requests)
from lzo.semantic.models import inference_output_retirements
from lzo.semantic.rdf import Triple
from lzo.worker.service import enqueue_outbox, resource


@dataclass(frozen=True)
class ProjectionReceipt:
    resource_id: UUID
    revision: int
    state: Literal["synced", "superseded", "failed", "retired"]
    digest: str


@dataclass(frozen=True)
class ProjectionContent:
    graphs: dict[str, tuple[Triple, ...]]
    references: tuple[UUID, ...]
    dependency_revisions: tuple[RevisionRef, ...] = ()


@dataclass(frozen=True)
class Producer:
    resolve: Callable
    version: str
    dirty_kind: Literal["facts", "schema", "rules"] | None
    action: str
    before_publish: Callable | None = None
    after_publish: Callable | None = None
    result_resolver: Callable | None = None


producers: dict[str, Producer] = {}
# Explicit foundation event catalogue: no wildcards or prefix-based exemptions.
nonsemantic = {name: "foundation-v1" for name in (
    "user.registered", "user.disabled", "user.enabled", "group.created", "group.updated",
    "group.members_changed", "role.created", "role.updated", "grant.created", "grant.revoked",
    "identity.pending", "identity.provider_pending", "identity.provider_created", "identity.sending",
    "identity.delivery_unknown", "identity.failed", "identity.completed",
    "approval.submitted", "approval.decided", "artifact.uploaded", "artifact.linked",
    "artifact.reviewed", "evidence.attached", "catalog.delivery.retried", "source.created", "source.upload.registered",
    "source.record.registered", "evidence.created", "job.enqueued", "job.started", "job.checkpoint",
    "job.result", "job.queued", "job.paused", "job.failed", "job.succeeded", "job.cancelled")}
_context = ContextVar("semantic_projection")


def register_projection(event_type: str, resolver: Callable, *, version: str,
                        dirty_kind: Literal["facts", "schema", "rules"] | None, action="edit",
                        before_publish=None, after_publish=None, result_resolver=None):
    if event_type in nonsemantic or not version or dirty_kind not in ("facts", "schema", "rules", None) or not action:
        raise ValueError("Invalid projection producer registration")
    producers[event_type] = Producer(resolver, version, dirty_kind, action, before_publish, after_publish,
                                     result_resolver)


def register_nonsemantic(event_type: str, version: str):
    """Trusted startup classification, including test fixtures only in tests."""
    if event_type in producers or not version:
        raise ValueError("Invalid nonsemantic registration")
    nonsemantic[event_type] = version


def _revision(uow, ref):
    row = uow.session.execute(select(revisions).where(revisions.c.event_id == ref.event_id,
        revisions.c.resource_id == ref.resource_id, revisions.c.revision == ref.revision)).mappings().first()
    if row is None:
        raise ValueError("Revision identity does not match authoritative PG")
    return row


def _outbox(uow, ref):
    return uow.session.execute(select(outbox_events).where(
        outbox_events.c.event_id == ref.event_id)).mappings().one()


def _dependency_refs(content):
    return sorted(({"resource_id": str(ref.resource_id), "revision": ref.revision,
                    "event_id": str(ref.event_id)} for ref in content.dependency_revisions),
                  key=lambda ref: ref["resource_id"])


def mark_inference_dirty(uow: UnitOfWork, revision: RevisionRef,
                         kind: Literal["facts", "schema", "rules"]) -> None:
    if kind not in ("facts", "schema", "rules"):
        raise ValueError("Invalid inference input kind")
    lock_ledger(uow)
    _revision(uow, revision)
    position = _outbox(uow, revision)["position"]
    old = uow.session.execute(select(inference_inputs).where(
        inference_inputs.c.event_id == revision.event_id)).mappings().first()
    if old and old["kind"] != kind:
        raise ValueError("Inference input kind changed")
    uow.session.execute(insert(inference_inputs).values(event_id=revision.event_id,
        position=position, kind=kind).on_conflict_do_nothing())
    uow.session.execute(update(inference_fence).values(
        expected_position=func.greatest(inference_fence.c.expected_position, position)))


def inference_is_current(uow: UnitOfWork) -> bool:
    fence = uow.session.execute(select(inference_fence)).mappings().one()
    return fence["resolved_position"] == fence["expected_position"]


def _retired_output(uow, event_id, event_type):
    producer = producers.get(event_type)
    return (event_type == "inference.output.ready" and producer is not None
        and producer.dirty_kind is None and uow.session.execute(
            select(inference_output_retirements.c.event_id).where(
                inference_output_retirements.c.event_id == event_id)).first() is not None)


@contextmanager
def projection_context(uow, repository, actor=None):
    token = _context.set(ProjectionService(uow, repository, actor=actor))
    try:
        yield
    finally:
        _context.reset(token)


def project_revision(revision: RevisionRef) -> ProjectionReceipt:
    return _context.get().project_revision(revision)


class ProjectionService:
    def __init__(self, uow, repository: FusekiRepository, *, actor=None):
        self.uow, self.repository, self.actor = uow, repository, actor

    def _content(self, ref):
        row = _revision(self.uow, ref)
        producer = producers.get(row["event_type"])
        if producer is None:
            raise ValueError("Unregistered semantic producer")
        content = producer.resolve(self.uow, row)
        if not isinstance(content, ProjectionContent) or not content.references or content.references[0] != ref.resource_id:
            raise ValueError("Projection references must start with the primary resource")
        if len(set(content.references)) != len(content.references):
            raise ValueError("Duplicate projection dependency")
        pinned = {dependency.resource_id for dependency in content.dependency_revisions}
        if set(content.references[1:]) != pinned:
            raise ValueError("Every secondary dependency must pin an immutable revision")
        for dependency in content.dependency_revisions:
            _revision(self.uow, dependency)
        return row, producer, content, generation_digest(content.graphs)

    def schedule(self, actor, revision: RevisionRef):
        """Caller already appends the revision in this UoW; never commits."""
        validate_actor(self.uow, actor)
        row, producer, content, digest = self._content(revision)
        if row["actor_id"] != actor.subject_id:
            raise ValueError("Projection initiator must be the revision author")
        for reference in content.references:
            require_permission(actor, "view", resource(self.uow, reference), uow=self.uow)
        require_permission(actor, producer.action, resource(self.uow, revision.resource_id), uow=self.uow)
        lock_ledger(self.uow)
        outbox = _outbox(self.uow, revision)
        previous = self.uow.session.execute(select(projection_events).where(
            projection_events.c.event_id == revision.event_id)).mappings().first()
        refs = [str(ref) for ref in content.references]
        if previous and (previous["digest"] != digest or previous["producer_version"] != producer.version
                         or previous["reference_ids"] != refs
                         or previous["dependency_refs"] != _dependency_refs(content)):
            raise ValueError("Immutable projection producer output changed")
        self.uow.session.execute(insert(projection_events).values(
            event_id=revision.event_id, resource_id=revision.resource_id, revision=revision.revision,
            position=outbox["position"], producer_version=producer.version, reference_ids=refs,
            dependency_refs=_dependency_refs(content),
            initiator_id=actor.subject_id, state="pending", digest=digest).on_conflict_do_nothing())
        self.uow.session.execute(insert(projection_classifications).values(position=outbox["position"],
            event_id=revision.event_id, kind="semantic", version=producer.version).on_conflict_do_nothing())
        for reference in content.references:
            self.uow.session.execute(insert(revision_visibility_dependencies).values(
                event_id=revision.event_id, resource_id=reference).on_conflict_do_nothing())
        if producer.dirty_kind is not None:
            mark_inference_dirty(self.uow, revision, producer.dirty_kind)
        payload = {"resource_id": str(revision.resource_id), "revision": revision.revision,
                   "event_id": str(revision.event_id), "reference_ids": refs}
        return enqueue_outbox(self.uow, actor, "rdf.project", outbox["id"], payload, None)

    def project_revision(self, revision: RevisionRef) -> ProjectionReceipt:
        # Direct service is a trusted internal interface. Workers pass their
        # fresh initiator; public callers cannot provide a graph/payload.
        if self.actor is not None:
            validate_actor(self.uow, self.actor)
        lock_ledger(self.uow)
        pending = self.uow.session.execute(select(projection_events).where(
            projection_events.c.event_id == revision.event_id).with_for_update()).mappings().first()
        if pending is None:
            raise ValueError("Projection was not registered by its originating transaction")
        row, producer, content, digest = self._content(revision)
        if (pending["digest"] != digest or pending["producer_version"] != producer.version
                or pending["reference_ids"] != [str(ref) for ref in content.references]
                or pending["dependency_refs"] != _dependency_refs(content)):
            raise ValueError("Projection producer contract changed")
        if self.actor is not None:
            if self.actor.subject_id != pending["initiator_id"]:
                raise ValueError("Projection execution identity changed")
            for reference in content.references:
                require_permission(self.actor, "view", resource(self.uow, reference), uow=self.uow)
            require_permission(self.actor, producer.action, resource(self.uow, revision.resource_id), uow=self.uow)
        if _retired_output(self.uow, revision.event_id, row["event_type"]):
            self.advance_contiguous()
            return ProjectionReceipt(revision.resource_id, revision.revision, "retired", digest)
        from lzo.gis.service import split
        if split(self.uow):
            from lzo.synchronization.delivery import require_deliveries, DeliveriesPending, DeliveriesFailed
            from lzo.semantic.facts import OBJECT_EVENTS
            dependency_pending = False
            for dependency in (revision,*content.dependency_revisions):
                dependent=_revision(self.uow,dependency)
                target = None
                if dependent["event_type"] in OBJECT_EVENTS:
                    target = 'gis'
                elif dependent['event_type'] == 'telemetry.batch.accepted':
                    if self.actor is not None:
                        from lzo.telemetry.service import TelemetryService
                        TelemetryService(self.uow).reference_dependencies(self.actor, dependency)
                    target = 'timeseries'
                elif dependent['event_type'] == 'catalog.version.accepted':
                    target = 'catalog'
                if target:
                    try:
                        require_deliveries(self.uow, dependency.event_id, (target,))
                    except DeliveriesFailed:
                        raise
                    except DeliveriesPending:
                        dependency_pending = True
            if dependency_pending:
                raise DeliveriesPending('Required domain deliveries are pending')
        if producer.before_publish is not None:
            try:
                producer.before_publish(self.uow, self.actor, row)
            except ApiError as failure:
                self.uow.session.execute(update(projection_events).where(
                    projection_events.c.event_id == revision.event_id).values(
                        state="failed", error_code=failure.code))
                receipt = ProjectionReceipt(revision.resource_id, revision.revision, "failed", digest)
                if producer.after_publish is not None:
                    producer.after_publish(self.uow, self.actor, row, receipt)
                return receipt
        if _retired_output(self.uow, revision.event_id, row["event_type"]):
            self.advance_contiguous()
            return ProjectionReceipt(revision.resource_id, revision.revision, "retired", digest)
        try:
            state, remote_digest = self.repository.publish_revision(revision.resource_id, revision.revision,
                pending["position"], content.graphs)
            if remote_digest != digest:
                raise RepresentationError("Projection digest mismatch")
        except (httpx.HTTPError, RepresentationError):
            self.uow.session.execute(update(projection_events).where(
                projection_events.c.event_id == revision.event_id).values(state="failed", error_code="RDF_PROJECTION_FAILED"))
            receipt = ProjectionReceipt(revision.resource_id, revision.revision, "failed", digest)
            if producer.after_publish is not None:
                producer.after_publish(self.uow, self.actor, row, receipt)
            return receipt
        self.uow.session.execute(update(projection_events).where(
            projection_events.c.event_id == revision.event_id).values(state=state, error_code=None))
        if state == "synced":
            self.uow.session.execute(insert(projection_heads).values(resource_id=revision.resource_id,
                revision=revision.revision, position=pending["position"], digest=digest).on_conflict_do_update(
                    index_elements=[projection_heads.c.resource_id],
                    set_={"revision": revision.revision, "position": pending["position"], "digest": digest},
                    where=projection_heads.c.revision <= revision.revision))
            if producer.dirty_kind is not None:
                self.uow.session.execute(insert(inference_requests).values(event_id=revision.event_id,
                    resource_id=revision.resource_id, position=pending["position"], state="pending").on_conflict_do_nothing())
        self.advance_contiguous()
        receipt = ProjectionReceipt(revision.resource_id, revision.revision, state, digest)
        if producer.after_publish is not None:
            producer.after_publish(self.uow, self.actor, row, receipt)
        if state == "synced" and producer.dirty_kind is not None and self.actor is not None:
            from lzo.semantic.inference_jobs import try_schedule_inference
            try_schedule_inference(self.uow, self.actor, revision)
        return receipt

    def advance_contiguous(self, *, limit=1000):
        """Classify only known events and advance only the complete PG prefix."""
        lock_ledger(self.uow)
        current = self.uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
        events = self.uow.session.execute(select(outbox_events).where(
            outbox_events.c.position > current).order_by(outbox_events.c.position).limit(limit)).mappings()
        for event in events:
            classification = self.uow.session.execute(select(projection_classifications).where(
                projection_classifications.c.position == event["position"])).mappings().first()
            if classification is None:
                version = nonsemantic.get(event["event_type"])
                if version is None:
                    break
                self.uow.session.execute(insert(projection_classifications).values(
                    position=event["position"], event_id=event["event_id"], kind="nonsemantic", version=version))
            elif classification["kind"] == "semantic":
                status = self.uow.session.execute(select(projection_events.c.state).where(
                    projection_events.c.event_id == event["event_id"])).scalar_one_or_none()
                if status not in ("synced", "superseded") and not _retired_output(
                        self.uow, event["event_id"], event["event_type"]):
                    break
            if event["position"] != current + 1:
                raise ValueError("Outbox position gap")
            current = event["position"]
        self.uow.session.execute(update(platform_watermarks).values(projection_watermark=current))
        return current
