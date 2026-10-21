"""Authenticated artifact upload, metadata and private content routes."""
import hashlib
import json
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.responses import StreamingResponse

from lzo.artifacts.schemas import (ArtifactDetail, ArtifactLinkWrite, ArtifactMutationDetail,
                                    ArtifactReviewWrite, ApiErrorDetail)
from lzo.artifacts.service import ArtifactService
from lzo.artifacts.store import ArtifactStore
from lzo.auth.permissions import current_version, permission_context
from lzo.auth.router import AuthRequest, authenticated
from lzo.auth.session import SESSION_COOKIE, SessionService
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.governance.idempotency import execute_idempotent

_ERROR_RESPONSES = {status: {"model": ApiErrorDetail} for status in
                    (401, 403, 404, 409, 413, 422, 428, 503)}
router = APIRouter(prefix="/api/v1/artifacts", tags=["Artifacts"], responses=_ERROR_RESPONSES)


def _store(request: Request) -> ArtifactStore:
    settings = request.app.state.settings
    return ArtifactStore(settings.file_root / "artifacts", max_upload_bytes=settings.artifact_max_upload_bytes)


def _digest(data) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"),
        ensure_ascii=False).encode("utf-8")).hexdigest()


def _metadata(value: str | None):
    if value is None:
        return {}
    if len(value) > 16384:
        raise ApiError("INVALID_METADATA", 422, "Artifact metadata is too large")
    try:
        data = json.loads(value)
    except (json.JSONDecodeError, TypeError):
        raise ApiError("INVALID_METADATA", 422, "Artifact metadata must be JSON") from None
    if not isinstance(data, dict):
        raise ApiError("INVALID_METADATA", 422, "Artifact metadata must be a JSON object")
    return data


def _session(request: Request, resource_id: UUID):
    """Hold only a short authorization transaction, in state -> session order."""
    with PgUnitOfWork(request.app.state.engine) as uow:
        current_version(uow, lock=True)
        session = SessionService(uow, request.app.state.settings)
        row = session.read(request.cookies.get(SESSION_COOKIE), lock=True)
        session.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
        actor = session.actor(row, request.state.request_id)
        with permission_context(uow):
            ArtifactService(uow, None, request.app.state.settings).authorize_upload(actor, resource_id)
        return actor


@router.post("", response_model=ArtifactDetail, status_code=201, operation_id="uploadArtifact",
    openapi_extra={"requestBody": {"required": True, "content": {
        "application/octet-stream": {"schema": {"type": "string", "format": "binary"}}}}})
async def upload(request: Request, resource_id: UUID, filename: str,
                 previous_version_id: UUID | None = None,
                 x_artifact_metadata: str | None = Header(default=None)):
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    expected = parse_if_match(request.headers.get("If-Match"))
    metadata = _metadata(x_artifact_metadata)
    media_type = request.headers.get("Content-Type", "application/octet-stream").split(";", 1)[0].strip()
    _session(request, resource_id)
    # No business transaction is held while Request.stream transfers the file.
    store = _store(request)
    staged = await store.stage_async(request.stream(), media_type)
    try:
        with PgUnitOfWork(request.app.state.engine) as uow:
            current_version(uow, lock=True)
            session = SessionService(uow, request.app.state.settings)
            row = session.read(request.cookies.get(SESSION_COOKIE), lock=True)
            session.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
            actor = session.actor(row, request.state.request_id)
            with permission_context(uow):
                service = ArtifactService(uow, store, request.app.state.settings)
                service.authorize_upload(actor, resource_id)
                digest = _digest({"resource_id": str(resource_id), "filename": filename,
                    "previous_version_id": str(previous_version_id) if previous_version_id else None,
                    "metadata": metadata, "media_type": staged.ref.media_type, "sha256": staged.ref.sha256,
                    "size_bytes": staged.ref.size_bytes, "expected_revision": expected})
                result = execute_idempotent(uow, actor, key, digest, lambda: service.register_upload(
                    actor, resource_id, staged, filename=filename, metadata=metadata,
                    previous_version_id=previous_version_id,
                    expected_resource_revision=expected).model_dump(mode="json"))
                uow.commit()
    finally:
        store.discard(staged)
    return ArtifactDetail.model_validate(result)


@router.get("/{version_id}", response_model=ArtifactDetail, operation_id="getArtifact")
def get(version_id: UUID, response: Response, request: Request,
        auth: AuthRequest = Depends(authenticated)):
    detail = ArtifactService(auth.uow, _store(request), request.app.state.settings).get(auth.actor, version_id)
    response.headers["Cache-Control"] = "no-store"
    response.headers["ETag"] = f'"{detail.revision}"'
    return detail


@router.get("/{version_id}/content", operation_id="downloadArtifact")
def content(version_id: UUID, request: Request):
    # Finish the current-right transaction before streaming potentially large
    # content so revocations are not blocked by a slow reader.
    with PgUnitOfWork(request.app.state.engine) as uow:
        current_version(uow, lock=True)
        session = SessionService(uow, request.app.state.settings)
        row = session.read(request.cookies.get(SESSION_COOKIE))
        actor = session.actor(row, request.state.request_id)
        with permission_context(uow):
            service = ArtifactService(uow, _store(request), request.app.state.settings)
            detail = service.get(actor, version_id)
            stream = service.content(actor, version_id)
            from lzo.artifacts.service import _scope
            generated = _scope(uow, detail.resource_id)["resource_kind"] in {"semantic.export", "evidence"}
    def chunks():
        try:
            while piece := stream.read(1024 * 1024):
                if generated:
                    with PgUnitOfWork(request.app.state.engine) as current:
                        current_version(current, lock=True)
                        sessions = SessionService(current, request.app.state.settings)
                        login = sessions.read(request.cookies.get(SESSION_COOKIE))
                        reader = sessions.actor(login, request.state.request_id)
                        with permission_context(current):
                            ArtifactService(current, None, request.app.state.settings).authorize_version(reader, version_id)
                yield piece
        finally:
            stream.close()
    safe_types = {"application/pdf", "image/png", "image/jpeg", "image/gif", "image/webp",
                  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                  "application/msword"}
    disposition = "attachment; filename=\"download\"; filename*=UTF-8''" + quote(detail.filename, safe="")
    return StreamingResponse(chunks(), media_type=detail.media_type if detail.media_type in safe_types
        else "application/octet-stream", headers={"Content-Disposition": disposition,
            "Content-Length": str(detail.size_bytes), "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
            "Referrer-Policy": "no-referrer"})


@router.post("/{version_id}/links", response_model=ArtifactMutationDetail,
             status_code=201, operation_id="linkArtifact")
def link(version_id: UUID, body: ArtifactLinkWrite, request: Request,
         auth: AuthRequest = Depends(authenticated)):
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    expected = parse_if_match(request.headers.get("If-Match"))
    service = ArtifactService(auth.uow, _store(request), request.app.state.settings)
    service.authorize_link(auth.actor, version_id, body.target_resource_id)
    digest = _digest({"path": request.url.path, "body": body.model_dump(mode="json"),
                      "expected_revision": expected})
    result = execute_idempotent(auth.uow, auth.actor, key, digest, lambda: service.link(
        auth.actor, version_id, body.target_resource_id, body.relation, expected).model_dump(mode="json"))
    auth.uow.commit()
    return ArtifactMutationDetail.model_validate(result)


@router.post("/{version_id}/reviews", response_model=ArtifactMutationDetail,
             status_code=201, operation_id="reviewArtifact")
def review(version_id: UUID, body: ArtifactReviewWrite, request: Request,
           auth: AuthRequest = Depends(authenticated)):
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    expected = parse_if_match(request.headers.get("If-Match"))
    service = ArtifactService(auth.uow, _store(request), request.app.state.settings)
    service.authorize_version(auth.actor, version_id, "edit")
    digest = _digest({"path": request.url.path, "body": body.model_dump(mode="json"),
                      "expected_revision": expected})
    result = execute_idempotent(auth.uow, auth.actor, key, digest, lambda: service.review(
        auth.actor, version_id, body.decision, body.comment, expected).model_dump(mode="json"))
    auth.uow.commit()
    return ArtifactMutationDetail.model_validate(result)
