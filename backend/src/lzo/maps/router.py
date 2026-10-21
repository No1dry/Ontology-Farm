"""Typed map commands and private authorized byte endpoints."""
import hashlib
import json
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from lzo.artifacts.router import _store
from lzo.auth.permissions import current_version, permission_context
from lzo.auth.router import AuthRequest, authenticated
from lzo.auth.session import SESSION_COOKIE, SessionService
from lzo.contracts import ActorContext
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.governance.idempotency import execute_idempotent
from lzo.maps.schemas import MapResourceCreate, MapResourcePage, MapResourcePatch, MapResourceView
from lzo.maps.service import MapService, _context_service
from lzo.maps.tiles import archive_range

router = APIRouter(prefix='/api/v1/maps/resources', tags=['Maps'])
PRIVATE = {'Cache-Control': 'private,no-store', 'X-Content-Type-Options': 'nosniff'}


def service(auth, request):
    auth.uow.map_settings = request.app.state.settings
    auth.uow.artifact_store = _store(request)
    return MapService(auth.uow, auth.uow.artifact_store, auth.uow.map_settings)


def digest(request, body, expected):
    value = {'path': request.url.path, 'body': body.model_dump(mode='json', exclude_unset=True), 'expected_revision': expected}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


@router.get('', response_model=MapResourcePage, operation_id='listMapResources')
def list_resources(request: Request, response: Response, cursor: str | None = None,
                   limit: int | None = Query(default=None, ge=1, le=200), auth: AuthRequest = Depends(authenticated)):
    response.headers.update(PRIVATE)
    return service(auth, request).list(auth.actor, cursor, limit)


@router.post('', response_model=MapResourceView, status_code=201, operation_id='createMapResource')
def create_resource(body: MapResourceCreate, request: Request, response: Response,
                    auth: AuthRequest = Depends(authenticated)):
    key = require_idempotency_key(request.headers.get('Idempotency-Key'))
    expected = parse_if_match(request.headers['If-Match']) if 'If-Match' in request.headers else 0
    if expected != 0:
        raise ApiError('REVISION_CONFLICT', 409, 'New map revision must be zero')
    maps = service(auth, request)
    maps._destination_scope(auth.actor, body.owner_scope_id)
    # Reauthorize references before idempotency replay, too.
    from lzo.maps.service import referenced_versions
    maps.artifacts.authorize_versions(auth.actor, referenced_versions(body))
    def create():
        ref = maps.register(auth.actor, body)
        return maps.get(auth.actor, ref.resource_id).model_dump(mode='json')
    value = execute_idempotent(auth.uow, auth.actor, key, digest(request, body, 0), create)
    maps.get(auth.actor, UUID(value['id']))
    auth.uow.commit()
    response.headers.update(PRIVATE)
    response.headers['ETag'] = f'"{value["revision"]}"'
    return MapResourceView.model_validate(value)


@router.get('/{id}', response_model=MapResourceView, operation_id='getMapResource')
def get_resource(id: UUID, request: Request, response: Response, auth: AuthRequest = Depends(authenticated)):
    resource_id = id
    value = service(auth, request).get(auth.actor, resource_id)
    response.headers.update(PRIVATE)
    response.headers['ETag'] = f'"{value.revision}"'
    return value


@router.patch('/{id}', response_model=MapResourceView, operation_id='updateMapResource')
def update_resource(id: UUID, body: MapResourcePatch, request: Request, response: Response,
                    auth: AuthRequest = Depends(authenticated)):
    resource_id = id
    key = require_idempotency_key(request.headers.get('Idempotency-Key'))
    expected = parse_if_match(request.headers.get('If-Match'))
    maps = service(auth, request)
    maps.get(auth.actor, resource_id, 'edit')
    # New references must still be readable when replaying an old response.
    maps.artifacts.authorize_versions(auth.actor, {version for version in [body.artifact_version_id,
        body.style_version_id, *(body.asset_versions or {}).values()] if version is not None})
    def patch():
        maps.patch(auth.actor, resource_id, body, expected)
        return maps.get(auth.actor, resource_id).model_dump(mode='json')
    value = execute_idempotent(auth.uow, auth.actor, key, digest(request, body, expected), patch)
    result = MapResourceView.model_validate(value)
    from lzo.maps.service import referenced_versions
    maps.artifacts.authorize_versions(auth.actor, referenced_versions(result))
    auth.uow.commit()
    response.headers.update(PRIVATE)
    response.headers['ETag'] = f'"{value["revision"]}"'
    return result


@router.get('/{id}/style.json', operation_id='getMapStyle', response_model=dict)
def get_style(id: UUID, request: Request, response: Response, auth: AuthRequest = Depends(authenticated)):
    resource_id = id
    maps = service(auth, request)
    value = maps.style(auth.actor, resource_id)
    response.headers.update(PRIVATE)
    response.headers['ETag'] = f'"map-{resource_id}-{maps.get(auth.actor, resource_id).revision}"'
    return value


@router.get('/{id}/tiles/{z}/{x}/{y}', operation_id='getMapTile', response_class=Response,
    responses={200: {'content': {'image/png': {}, 'image/jpeg': {}, 'image/webp': {}, 'application/vnd.mapbox-vector-tile': {}}}})
def get_tile(id: UUID, z: int, x: int, y: int, request: Request, auth: AuthRequest = Depends(authenticated)):
    resource_id = id
    maps = service(auth, request)
    data, media, encoding = maps.tile(auth.actor, resource_id, z, x, y)
    headers = dict(PRIVATE)
    if encoding:
        headers['Content-Encoding'] = encoding
    view = maps.get(auth.actor, resource_id)
    headers['ETag'] = f'"map-{resource_id}-{view.revision}-{hashlib.sha256(data).hexdigest()}"'
    return Response(data, media_type=media, headers=headers)


def _stream(stream, start, end, reauthorize):
    try:
        stream.seek(start)
        remaining = end - start + 1
        while remaining:
            if reauthorize:
                reauthorize()
            data = stream.read(min(65536, remaining))
            if not data:
                raise ApiError('BLOB_CORRUPT', 503, 'Stored artifact ended unexpectedly')
            remaining -= len(data)
            yield data
    finally:
        stream.close()


def _archive_etag(view, blob):
    return f'"map-{view.id}-{view.revision}-{blob.version_id}-{blob.sha256}"'


def open_archive_range(actor: ActorContext, resource_id: UUID, range_header: str | None) -> StreamingResponse:
    from lzo.auth.permissions import _current_uow
    uow = _current_uow.get()
    maps = _context_service(uow)
    view = maps.get(actor, resource_id)
    if view.kind != 'pmtiles':
        raise ApiError('MAP_ARCHIVE_UNSUPPORTED', 422, 'Only PMTiles resources expose a Range archive')
    blob = maps._blob(actor, view.artifact_version_id)
    headers = {**PRIVATE, 'Accept-Ranges': 'bytes', 'ETag': _archive_etag(view, blob)}
    try:
        start, end, status = archive_range(range_header, blob.size_bytes)
    except ApiError:
        headers['Content-Range'] = f'bytes */{blob.size_bytes}'
        return JSONResponse({'code': 'MAP_RANGE_INVALID', 'message': 'One satisfiable byte range is required',
            'request_id': actor.request_id, 'details': None}, status_code=416, headers=headers)
    stream = maps.artifacts.content(actor, blob.version_id)
    headers['Content-Length'] = str(end - start + 1)
    if status == 206:
        headers['Content-Range'] = f'bytes {start}-{end}/{blob.size_bytes}'
    return StreamingResponse(_stream(stream, start, end, getattr(uow, 'map_reauthorize', None)),
        status_code=status, media_type='application/vnd.pmtiles', headers=headers)


def _authorize_stream_request(request, resource_id, expected_revision=None):
    with PgUnitOfWork(request.app.state.engine) as uow:
        current_version(uow, lock=True)
        sessions = SessionService(uow, request.app.state.settings)
        row = sessions.read(request.cookies.get(SESSION_COOKIE))
        actor = sessions.actor(row, request.state.request_id)
        with permission_context(uow):
            maps = MapService(uow, _store(request), request.app.state.settings)
            view = maps.get(actor, resource_id)
            if expected_revision is not None and view.revision != expected_revision:
                raise ApiError('MAP_RESOURCE_CHANGED', 409, 'Map resource changed during transfer')
            return view


@router.get('/{id}/archive', operation_id='getMapArchive', response_class=StreamingResponse,
    responses={200: {'content': {'application/vnd.pmtiles': {}}}, 206: {'content': {'application/vnd.pmtiles': {}}}, 416: {'description': 'Invalid or unsatisfiable byte range'}})
def get_archive(id: UUID, request: Request):
    resource_id = id
    # Release the initial authorization transaction before transferring bytes.
    with PgUnitOfWork(request.app.state.engine) as uow:
        current_version(uow, lock=True)
        sessions = SessionService(uow, request.app.state.settings)
        row = sessions.read(request.cookies.get(SESSION_COOKIE))
        actor = sessions.actor(row, request.state.request_id)
        uow.map_settings, uow.artifact_store = request.app.state.settings, _store(request)
        with permission_context(uow):
            maps = _context_service(uow)
            view = maps.get(actor, resource_id)
            revision = view.revision
            uow.map_reauthorize = lambda: _authorize_stream_request(request, resource_id, revision)
            range_header = request.headers.get('Range')
            if request.headers.get('If-Range'):
                if view.kind != 'pmtiles' or request.headers['If-Range'] != _archive_etag(view, maps._blob(actor, view.artifact_version_id)):
                    range_header = None
            return open_archive_range(actor, resource_id, range_header)


@router.get('/{id}/assets/{asset_path:path}', operation_id='getMapAsset', response_class=StreamingResponse)
def get_asset(id: UUID, asset_path: str, request: Request):
    resource_id = id
    from lzo.maps.tiles import asset_path as validate_path
    with PgUnitOfWork(request.app.state.engine) as uow:
        current_version(uow, lock=True)
        sessions = SessionService(uow, request.app.state.settings)
        actor = sessions.actor(sessions.read(request.cookies.get(SESSION_COOKIE)), request.state.request_id)
        with permission_context(uow):
            maps = MapService(uow, _store(request), request.app.state.settings)
            view = maps.get(actor, resource_id)
            validate_path(asset_path)
            if asset_path not in view.asset_versions:
                raise ApiError('NOT_FOUND', 404, 'Map asset not found')
            blob = maps._blob(actor, view.asset_versions[asset_path])
            media = {'png':'image/png', 'jpg':'image/jpeg', 'jpeg':'image/jpeg', 'webp':'image/webp', 'json':'application/json', 'pbf':'application/x-protobuf'}.get(asset_path.rsplit('.', 1)[-1], 'application/octet-stream')
            stream = maps.artifacts.content(actor, blob.version_id)
            return StreamingResponse(_stream(stream, 0, blob.size_bytes - 1,
                lambda: _authorize_stream_request(request, resource_id, view.revision)), media_type=media,
                headers={**PRIVATE, 'Content-Length': str(blob.size_bytes), 'ETag': f'"{blob.version_id}-{blob.sha256}"'})
