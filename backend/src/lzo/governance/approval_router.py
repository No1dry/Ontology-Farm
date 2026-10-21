"""Current-right approval API; previews are derived by trusted domain adapters."""
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from lzo.auth.router import AuthRequest, authenticated
from lzo.contracts import JsonObject, Page
from lzo.errors import Conflict, parse_if_match, require_idempotency_key
from lzo.governance.approval_schemas import ApprovalDecision, ApprovalDetail, ApprovalWrite
from lzo.governance.approvals import ApprovalService
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.revisions import lock_ledger
from lzo.governance.risk import classify, digest_change

router = APIRouter(prefix="/api/v1/approvals", tags=["Approvals"])


class PreviewWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=128)
    target_ids: list[UUID] = Field(min_length=1, max_length=200)
    command: JsonObject


class PreviewDetail(PreviewWrite):
    change_digest: str
    baseline: dict[str, int]
    impact: JsonObject
    risk: str
    reasons: list[str]


def service(auth):
    return ApprovalService(auth.uow, auth.service.settings.list_page_size,
                           auth.service.settings.list_max_page_size)


@router.post("/preview", response_model=PreviewDetail, operation_id="previewApproval")
def preview(body: PreviewWrite, auth: AuthRequest = Depends(authenticated)):
    change = service(auth).preview(auth.actor, body.kind, body.target_ids, body.command)
    risk, reasons = classify(change)
    return PreviewDetail(kind=change.kind, target_ids=list(change.target_ids), command=change.command,
        change_digest=change.change_digest, baseline=change.baseline, impact=change.impact, risk=risk, reasons=reasons)


@router.get("", response_model=Page[ApprovalDetail], operation_id="listApprovals")
def list_approvals(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return service(auth).list(auth.actor, cursor)


@router.get("/{approval_id}", response_model=ApprovalDetail, operation_id="getApproval")
def get_approval(approval_id: UUID, auth: AuthRequest = Depends(authenticated)):
    return service(auth).get(auth.actor, approval_id)


@router.post("", response_model=ApprovalDetail, status_code=201, operation_id="submitApproval")
def submit(body: ApprovalWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    svc = service(auth)
    # Check current rights before replay. Preview validates authoritative inputs.
    svc.preview(auth.actor, body.kind, body.target_ids, body.command)
    result = execute_idempotent(auth.uow, auth.actor,
        require_idempotency_key(request.headers.get("Idempotency-Key")),
        digest_change({"operation": "approval.submit", **body.model_dump(mode="json")}),
        lambda: svc.submit(auth.actor, body.kind, body.target_ids, body.change_digest,
            body.baseline, {"command": body.command}).model_dump(mode="json"))
    auth.uow.commit()
    return result


@router.post("/{approval_id}/decisions", response_model=ApprovalDetail, operation_id="decideApproval")
def decide(approval_id: UUID, body: ApprovalDecision, request: Request,
           auth: AuthRequest = Depends(authenticated)):
    svc = service(auth)
    row = svc._row(auth.actor, approval_id)
    lock_ledger(auth.uow)
    change = svc._current(row)
    svc._permission(auth.actor, change, "review")
    expected = parse_if_match(request.headers.get("If-Match"))

    def command():
        if svc.get(auth.actor, approval_id).revision != expected:
            raise Conflict("REVISION_CONFLICT", "Approval revision changed")
        return svc.decide(auth.actor, approval_id, body.decision, body.comment).model_dump(mode="json")

    result = execute_idempotent(auth.uow, auth.actor,
        require_idempotency_key(request.headers.get("Idempotency-Key")),
        digest_change({"operation": "approval.decide", "id": str(approval_id),
                       "revision": expected, **body.model_dump()}), command)
    auth.uow.commit()
    return result
