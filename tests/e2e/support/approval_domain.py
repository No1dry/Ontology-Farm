"""Synthetic F06 domain adapter, loaded only by explicit acceptance configuration."""
from uuid import UUID
from sqlalchemy import select, text

from lzo.errors import ApiError
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import resource_heads
from lzo.governance.risk import ChangeDescriptor, register_policy
from lzo.governance.revisions import append_revision
from lzo.worker import register_handler
from lzo.worker.service import checkpoint

KIND = "schema.add_class"
JOB_KIND = "acceptance.synthetic_publish"


def resolve(uow, targets, command):
    if set(command) != {"resource_id", "class_iri"} or command["class_iri"] != "urn:synthetic:f06:Class":
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Only the synthetic fixture command is supported")
    target = UUID(command["resource_id"])
    if set(targets) != {target}:
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Synthetic target mismatch")
    revision = uow.session.execute(select(resource_heads.c.revision).where(
        resource_heads.c.resource_id == target)).scalar_one()
    return ChangeDescriptor(kind=KIND, target_ids=(target,), command=command,
        baseline={"resource:" + str(target): revision},
        impact={"synthetic": True, "class_additions": 1}, submit_action="schema.edit",
        review_action="schema.review", execute_action="schema.publish",
        operation_key="synthetic-class:" + str(target), policy_version=1)


def publish(ctx):
    with ctx.batch():
        change = resolve(ctx.uow, [UUID(ctx.payload["resource_id"])], ctx.payload["command"])
        ApprovalService(ctx.uow).require_executable(ctx.actor, UUID(ctx.payload["approval_id"]),
                                                    change.change_digest, change.baseline)
        ctx.uow.session.execute(text(
            "INSERT INTO f06_synthetic_effects(job_id) VALUES (:id)"), {"id": ctx.job_id})
        append_revision(ctx.uow, ctx.actor, UUID(ctx.payload["resource_id"]),
                        next(iter(change.baseline.values())), {"synthetic": True, "published": True},
                        "synthetic.published")
        checkpoint(ctx.job_id, ctx.lease_token, {"published": True})


register_policy(KIND, resolve)
register_handler(JOB_KIND, publish, action="schema.publish",
                 references=lambda payload: (UUID(payload["resource_id"]), UUID(payload["approval_id"])))
