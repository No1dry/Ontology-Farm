"""Scoped job reads and idempotent, revision-checked state commands."""
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from lzo.auth.router import AuthRequest, authenticated
from lzo.contracts import JobRef, Page
from lzo.errors import Conflict, parse_if_match, require_idempotency_key
from lzo.governance.idempotency import execute_idempotent
from lzo.worker.health import HealthOverview, overview
from lzo.worker.service import JobService, JobView

router = APIRouter(prefix="/api/v1", tags=["Jobs"])


@router.get("/jobs", response_model=Page[JobView], operation_id="listJobs")
def list_jobs(request: Request, response: Response, cursor: str | None = None,
              auth: AuthRequest = Depends(authenticated)):
    response.headers["Cache-Control"] = "no-store"
    return JobService(auth.uow, request.app.state.settings).list(auth.actor, cursor)


@router.get("/jobs/{job_id}", response_model=JobView, operation_id="getJob")
def get_job(job_id: UUID, request: Request, response: Response,
            auth: AuthRequest = Depends(authenticated)):
    result = JobService(auth.uow, request.app.state.settings).get(auth.actor, job_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{result.revision}"'
    return result


def mutate(job_id, command, request, auth):
    service = JobService(auth.uow, request.app.state.settings)
    # Authorization precedes idempotency replay and any locking.
    service.row(auth.actor, job_id, edit=True)
    expected = parse_if_match(request.headers.get("If-Match"))
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    digest = hashlib.sha256(json.dumps({"job": str(job_id), "command": command,
                                      "revision": expected}, sort_keys=True).encode()).hexdigest()
    def apply():
        row = service.row(auth.actor, job_id, edit=True, lock=True)
        allowed = {"pause": {"queued", "running"}, "resume": {"paused"}, "retry": {"failed"}}
        if row["status"] not in allowed[command]:
            raise Conflict("JOB_STATE_CONFLICT", "Job command is not allowed")
        ref = service.set_state(auth.actor, job_id, "paused" if command == "pause" else "queued", expected)
        return {"id": str(ref.id), "status": ref.status}
    result = execute_idempotent(auth.uow, auth.actor, key, digest, apply)
    auth.uow.commit()
    return result


@router.post("/jobs/{job_id}/pause", response_model=JobRef, operation_id="pauseJob")
def pause(job_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutate(job_id, "pause", request, auth)


@router.post("/jobs/{job_id}/resume", response_model=JobRef, operation_id="resumeJob")
def resume(job_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutate(job_id, "resume", request, auth)


@router.post("/jobs/{job_id}/retry", response_model=JobRef, operation_id="retryJob")
def retry(job_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutate(job_id, "retry", request, auth)


@router.get("/health/overview", response_model=HealthOverview, operation_id="getHealthOverview")
def health(request: Request, response: Response, auth: AuthRequest = Depends(authenticated)):
    response.headers["Cache-Control"] = "no-store"
    return overview(auth.uow, auth.actor, request.app.state.settings)
