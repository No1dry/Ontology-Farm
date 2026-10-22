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
from lzo.storage.blobs import get_blob_store
from lzo.auth.permissions import current_version, permission_context
from lzo.auth.router import AuthRequest, authenticated
from lzo.auth.session import SESSION_COOKIE, SessionService
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.governance.idempotency import execute_idempotent
from lzo.artifacts.uploads import UploadService
from lzo.storage.blobs import parse_range
from pydantic import BaseModel, Field

_ERROR_RESPONSES = {status: {"model": ApiErrorDetail} for status in
                    (401, 403, 404, 409, 413, 422, 428, 503)}
router = APIRouter(prefix="/api/v1/artifacts", tags=["Artifacts"], responses=_ERROR_RESPONSES)


def _store(request: Request):
    settings = request.app.state.settings
    return get_blob_store(settings)


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


class UploadSessionCreate(BaseModel):
    resource_id: UUID
    filename: str = Field(min_length=1, max_length=255)
    media_type: str
    size_bytes: int = Field(gt=0)
    sha256: str = Field(pattern='^[0-9a-f]{64}$')
    metadata: dict = Field(default_factory=dict)
    previous_version_id: UUID | None = None


@router.post('/uploads', status_code=201, operation_id='createArtifactUpload')
def create_upload(body: UploadSessionCreate, request: Request, auth: AuthRequest = Depends(authenticated)):
    result = UploadService(auth.uow, _store(request), request.app.state.settings).create(auth.actor,
        **body.model_dump(), idempotency_key=require_idempotency_key(request.headers.get('Idempotency-Key')),
        expected_resource_revision=parse_if_match(request.headers.get('If-Match')))
    auth.uow.commit()
    return result


@router.get('/uploads/{upload_id}', operation_id='getArtifactUpload')
def upload_status(upload_id: UUID, request: Request, response: Response,
                  auth: AuthRequest = Depends(authenticated)):
    response.headers['Cache-Control'] = 'no-store'
    return UploadService(auth.uow, _store(request), request.app.state.settings).status(auth.actor, upload_id)


@router.put('/uploads/{upload_id}/parts/{number}', operation_id='putArtifactUploadPart')
async def upload_part(upload_id: UUID, number: int, request: Request):
    from contextlib import contextmanager
    @contextmanager
    def transaction():
        with PgUnitOfWork(request.app.state.engine) as uow:
            current_version(uow, lock=True)
            session = SessionService(uow, request.app.state.settings)
            row = session.read(request.cookies.get(SESSION_COOKIE), lock=True)
            session.csrf(row, request.headers.get('X-CSRF-Token'), request.headers.get('Origin'))
            actor = session.actor(row, request.state.request_id)
            with permission_context(uow):
                yield uow, actor
    store = _store(request)
    settings = request.app.state.settings
    with transaction() as (uow, actor):
        part_bytes = UploadService(uow, store, settings).status(actor, upload_id)['part_bytes']
    async def bounded():
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > part_bytes:
                raise ApiError('UPLOAD_TOO_LARGE', 413, 'Upload part exceeds budget')
            yield chunk
    staged = await store.stage_async(bounded(), 'application/octet-stream')
    try:
        with transaction() as (uow, actor):
            result = UploadService(uow, store, settings).part(actor, upload_id, number, staged)
            uow.commit()
            return result
    finally:
        store.discard(staged)


@router.post('/uploads/{upload_id}/complete', response_model=ArtifactDetail, operation_id='completeArtifactUpload')
def complete_upload(upload_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    result = UploadService(auth.uow, _store(request), request.app.state.settings).complete(auth.actor, upload_id)
    auth.uow.commit()
    return result


@router.delete('/uploads/{upload_id}', operation_id='cancelArtifactUpload')
def cancel_upload(upload_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    result = UploadService(auth.uow, _store(request), request.app.state.settings).cancel(auth.actor, upload_id)
    auth.uow.commit()
    return result


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
            requested = parse_range(request.headers.get('Range'), detail.size_bytes)
            stream = service.content(actor, version_id, requested)
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
    range_headers = {'Accept-Ranges': 'bytes'}
    if requested:
        range_headers['Content-Range'] = f'bytes {requested[0]}-{requested[1]}/{detail.size_bytes}'
    return StreamingResponse(chunks(), status_code=206 if requested else 200,
        media_type=detail.media_type if detail.media_type in safe_types
        else "application/octet-stream", headers={"Content-Disposition": disposition,
            "Content-Length": str(requested[1]-requested[0]+1 if requested else detail.size_bytes), "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox",
            "Referrer-Policy": "no-referrer", **range_headers})


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
