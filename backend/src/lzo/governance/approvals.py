"""Independent, version-bound approvals in the caller's transaction."""
from dataclasses import asdict
from datetime import timezone
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, Column, DateTime, String, Table, exists, func, insert, select
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID

from lzo.auth.models import resource_scopes
from lzo.auth.permissions import authorized_statement, current_version, require_permission, validate_actor
from lzo.contracts import ActorContext, Page, ResourceScope
from lzo.errors import ApiError, Conflict, Forbidden
from lzo.governance.approval_schemas import ApprovalDetail, ApprovalRef
from lzo.governance.history import revision_visibility_dependencies
from lzo.governance.models import metadata, platform_watermarks
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import classify, digest_change, resolve_change, scoped_requirements_json, system_policy_version

approvals = Table("approvals", metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("submitter_id", PGUUID(as_uuid=True)), Column("kind", String),
    Column("operation_key", String), Column("risk", String), Column("change_digest", String),
    Column("baseline", JSONB), Column("descriptor", JSONB), Column("target_ids", JSONB),
    Column("impact", JSONB), Column("reasons", JSONB), Column("position", BigInteger),
    Column("submitted_at", DateTime(timezone=True)))
decisions = Table("approval_decisions", metadata,
    Column("approval_id", PGUUID(as_uuid=True), primary_key=True),
    Column("reviewer_id", PGUUID(as_uuid=True)), Column("decision", String),
    Column("comment", String), Column("decided_at", DateTime(timezone=True)))
dependencies = Table("approval_dependencies", metadata,
    Column("approval_id", PGUUID(as_uuid=True), primary_key=True),
    Column("resource_id", PGUUID(as_uuid=True), primary_key=True))


def _snapshot(change):
    value = asdict(change)
    value["system_policy_version"] = system_policy_version(change.kind)
    if change.ordinary_import is None:value.pop("ordinary_import")
    else:value["ordinary_import"]["effect_classes"]=list(change.ordinary_import.effect_classes)
    value["target_ids"] = [str(x) for x in change.target_ids]
    value["changed_fields"] = list(change.changed_fields)
    if change.operation_target_ids is None:
        # Existing immutable approval snapshots and digests retain their exact
        # default-ALL format; no migration or historical rewrite is needed.
        value.pop("operation_target_ids")
    else:
        value["operation_target_ids"] = [str(x) for x in change.operation_target_ids]
    if change.scoped_action_requirements:
        value["scoped_action_requirements"] = scoped_requirements_json(change.scoped_action_requirements)
    else:
        value.pop("scoped_action_requirements")
    return value


class ApprovalService:
    def __init__(self, uow, page_size=50, max_page_size=200):
        self.uow = uow
        self.page_size = min(max(page_size, 1), max_page_size)

    def _permission(self, actor, change, phase):
        validate_actor(self.uow, actor)
        # Resolve all targets through current view rights before operation
        # checks, so missing/hidden targets and target ordering reveal nothing.
        targets = set(change.target_ids)
        rows = self.uow.session.execute(select(resource_scopes).where(
            resource_scopes.c.resource_id.in_(targets),
            resource_scopes.c.resource_id.in_(authorized_statement(actor, "view")))).mappings().all()
        if len(rows) != len(targets):
            raise ApiError("NOT_FOUND", 404, "Resource not found")
        action = {"submit": change.submit_action, "review": change.review_action,
                  "execute": change.execute_action or change.submit_action}[phase]
        if change.global_security_class:
            require_permission(actor, action, ResourceScope(None, None, (), change.global_security_class), uow=self.uow)
        for row in rows:
            if (not change.global_security_class and (change.operation_target_ids is None
                    or row["resource_id"] in change.operation_target_ids)):
                scope = ResourceScope(row["resource_id"], None, (), row["security_class"])
                require_permission(actor, action, scope, uow=self.uow)
        scopes = {row["resource_id"]:row["security_class"] for row in rows}
        for requirement in change.scoped_action_requirements:
            if requirement.phase == phase:
                for identifier in requirement.target_ids:
                    require_permission(actor, requirement.action,
                        ResourceScope(identifier, None, (), scopes[identifier]), uow=self.uow)

    def preview(self, actor, kind, target_ids, command):
        validate_actor(self.uow, actor)
        lock_ledger(self.uow)
        change = resolve_change(self.uow, kind, target_ids, command)
        self._permission(actor, change, "submit")
        return change

    def submit(self, actor, kind, target_ids, change_digest, baseline, impact) -> ApprovalRef:
        if set(impact) != {"command"} or not isinstance(impact["command"], dict):
            raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "A command descriptor is required")
        change = self.preview(actor, kind, target_ids, impact["command"])
        if change.change_digest != change_digest or change.baseline != baseline:
            raise Conflict("APPROVAL_STALE", "Preview changed; preview again")
        risk, reasons = classify(change)
        approval_id = uuid4()
        primary = self.uow.session.execute(select(resource_scopes).where(
            resource_scopes.c.resource_id == change.target_ids[0])).mappings().one()
        self.uow.session.execute(insert(resource_scopes).values(
            resource_id=approval_id, resource_kind="approval", security_class=primary["security_class"],
            scope_id=primary["scope_id"]))
        event = append_revision(self.uow, actor, approval_id, 0,
            {"kind": kind, "risk": risk, "change_digest": change_digest,
             "baseline": baseline, "impact": change.impact, "reasons": reasons,
             "policy_version": change.policy_version, "system_policy_version": system_policy_version(change.kind)}, "approval.submitted")
        self.uow.session.execute(insert(approvals).values(id=approval_id, submitter_id=actor.subject_id,
            kind=kind, operation_key=digest_change(change.operation_key), risk=risk,
            change_digest=change_digest, baseline=baseline, descriptor=_snapshot(change),
            target_ids=[str(x) for x in change.target_ids], impact=change.impact, reasons=reasons,
            position=self.uow.session.execute(select(platform_watermarks.c.event_position)).scalar_one(),
            submitted_at=func.clock_timestamp()))
        self.uow.session.execute(insert(dependencies), [
            {"approval_id": approval_id, "resource_id": x} for x in change.target_ids])
        self._history_refs(event.event_id, change.target_ids)
        return self.get(actor, approval_id)

    def _history_refs(self, event_id, targets):
        self.uow.session.execute(insert(revision_visibility_dependencies), [
            {"event_id": event_id, "resource_id": x} for x in targets])

    def _statement(self, actor):
        validate_actor(self.uow, actor)
        hidden = exists(select(dependencies.c.resource_id).where(
            dependencies.c.approval_id == approvals.c.id,
            dependencies.c.resource_id.not_in(authorized_statement(actor, "view"))))
        return select(approvals, decisions.c.reviewer_id, decisions.c.decision,
            decisions.c.comment, decisions.c.decided_at).outerjoin(
                decisions, decisions.c.approval_id == approvals.c.id).where(~hidden)

    def _row(self, actor, approval_id):
        row = self.uow.session.execute(self._statement(actor).where(
            approvals.c.id == approval_id)).mappings().first()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Resource not found")
        return row

    def _detail(self, row):
        row = dict(row)
        for key in ("submitted_at", "decided_at"):
            if row[key]:
                row[key] = row[key].astimezone(timezone.utc)
        return ApprovalDetail(**{key: row[key] for key in (
            "id", "submitter_id", "reviewer_id", "risk", "change_digest", "baseline", "kind",
            "target_ids", "impact", "reasons", "comment", "submitted_at", "decided_at")},
            policy_version=row["descriptor"]["policy_version"],
            system_policy_version=row["descriptor"]["system_policy_version"],
            command=row["descriptor"]["command"],
            revision=2 if row["decision"] else 1,
            status={"approve": "approved", "reject": "rejected"}.get(row["decision"], "pending"))

    def get(self, actor, approval_id):
        return self._detail(self._row(actor, approval_id))

    def list(self, actor, cursor):
        statement = self._statement(actor)
        if cursor:
            try:
                statement = statement.where(approvals.c.id > UUID(cursor))
            except ValueError:
                raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
        rows = self.uow.session.execute(statement.order_by(approvals.c.id).limit(
            self.page_size + 1)).mappings().all()
        return Page([self._detail(x) for x in rows[:self.page_size]],
                    str(rows[self.page_size - 1]["id"]) if len(rows) > self.page_size else None)

    def _current(self, row):
        newer = self.uow.session.execute(select(approvals.c.id).where(
            approvals.c.submitter_id == row["submitter_id"],
            approvals.c.operation_key == row["operation_key"],
            approvals.c.position > row["position"]).limit(1)).first()
        if newer:
            raise Conflict("APPROVAL_STALE", "Approval was superseded; preview again")
        change = resolve_change(self.uow, row["kind"], [UUID(x) for x in row["target_ids"]],
                                row["descriptor"]["command"])
        if change.unresolved_conflicts:
            raise Conflict("CONFLICT_RULING_REQUIRED", "Field conflicts need separate adjudication")
        if _snapshot(change) != row["descriptor"]:
            raise Conflict("APPROVAL_STALE", "Bound content or source versions changed; preview again")
        return change

    def decide(self, actor, approval_id, decision, comment) -> ApprovalRef:
        if decision not in {"approve", "reject"} or not comment or len(comment) > 4000:
            raise ApiError("INVALID_APPROVAL_DECISION", 422, "A decision and comment are required")
        validate_actor(self.uow, actor)
        lock_ledger(self.uow)
        row = self._row(actor, approval_id)
        if actor.subject_id == row["submitter_id"]:
            raise Forbidden("SEPARATION_OF_DUTIES", "Independent reviewer required")
        if row["decision"]:
            raise Conflict("APPROVAL_DECIDED", "Decision is immutable; submit a new preview")
        change = self._current(row)
        self._permission(actor, change, "review")
        submitter = ActorContext(row["submitter_id"], actor.request_id, current_version(self.uow))
        self._permission(submitter, change, "submit")
        self.uow.session.execute(insert(decisions).values(approval_id=approval_id,
            reviewer_id=actor.subject_id, decision=decision, comment=comment, decided_at=func.clock_timestamp()))
        event = append_revision(self.uow, actor, approval_id, 1,
            {"decision": decision, "comment": comment, "change_digest": row["change_digest"],
             "reviewer_id": str(actor.subject_id)}, "approval.decided")
        self._history_refs(event.event_id, change.target_ids)
        return self.get(actor, approval_id)

    def require_executable(self, actor, approval_id, change_digest, baseline) -> ApprovalRef:
        validate_actor(self.uow, actor)
        lock_ledger(self.uow)
        row = self._row(actor, approval_id)
        if actor.subject_id != row["submitter_id"]:
            raise Forbidden("FORBIDDEN", "Only the initiating identity can execute")
        change = self._current(row)
        if change_digest != row["change_digest"] or baseline != row["baseline"]:
            raise Conflict("APPROVAL_STALE", "Command differs from approved preview")
        self._permission(actor, change, "submit")
        self._permission(actor, change, "execute")
        if row["risk"] == "high":
            if row["decision"] != "approve":
                raise Conflict("APPROVAL_REQUIRED", "Independent approval required")
            reviewer = ActorContext(row["reviewer_id"], actor.request_id, current_version(self.uow))
            self._permission(reviewer, change, "review")
        elif row["decision"] == "reject":
            raise Conflict("APPROVAL_REQUIRED", "Approval was rejected")
        return self._detail(row)
