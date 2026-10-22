"""Authenticated backend-only telemetry and object data API."""
from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from lzo.auth.router import AuthRequest, authenticated
from lzo.errors import require_idempotency_key
from lzo.telemetry.schemas import BatchInput, FileReplayInput, BatchAccepted, BatchStatus, TelemetryPage, ObjectDataView
from lzo.telemetry.service import TelemetryService
from lzo.telemetry.replay import accept_file, CAPABILITIES
from lzo.storage.object_view import ObjectDataService

router = APIRouter(prefix='/api/v1', tags=['Telemetry'])


def service(request, auth):
    auth.uow.ontology_settings = request.app.state.settings
    return TelemetryService(auth.uow, request.app.state.storage_registry)


@router.post('/telemetry/batches', response_model=BatchAccepted, status_code=202, operation_id='acceptTelemetryBatch')
def accept_batch(body: BatchInput, request: Request, auth: AuthRequest = Depends(authenticated)):
    result = service(request, auth).accept(auth.actor, body, require_idempotency_key(request.headers.get('Idempotency-Key')))
    auth.uow.commit()
    return result


@router.post('/telemetry/replay', response_model=BatchAccepted, status_code=202, operation_id='replayTelemetryFile')
def replay_file(body: FileReplayInput, request: Request, auth: AuthRequest = Depends(authenticated)):
    result = accept_file(service(request, auth), auth.actor, body, require_idempotency_key(request.headers.get('Idempotency-Key')))
    auth.uow.commit()
    return result


@router.get('/telemetry/batches/{id}', response_model=BatchStatus, operation_id='getTelemetryBatch')
def batch_status(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).status(auth.actor, id)


@router.post('/telemetry/batches/{id}/retry', status_code=202, operation_id='retryTelemetryBatch')
def retry_batch(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    require_idempotency_key(request.headers.get('Idempotency-Key'))
    result = service(request, auth).retry(auth.actor, id, request.headers.get('Idempotency-Key'))
    auth.uow.commit()
    return result


@router.get('/telemetry/capabilities', operation_id='getTelemetryCapabilities')
def capabilities(auth: AuthRequest = Depends(authenticated)):
    return CAPABILITIES


def query_endpoint(kind):
    def endpoint(request: Request, object_id: UUID | None = None, start: datetime | None = None,
                 end: datetime | None = None, cursor: str | None = None, limit: int = 50,
                 history: bool = False, latest: bool = False, device_id: UUID | None = None,
                 channel_id: str | None = None, mission_id: str | None = None, metric_id: str | None = None,
                 auth: AuthRequest = Depends(authenticated)):
        return service(request, auth).query(auth.actor, kind=kind, object_id=object_id, start=start, end=end,
            cursor=cursor, limit=limit, history=history, latest=latest, device_id=device_id,
            channel_id=channel_id, mission_id=mission_id, metric_id=metric_id)
    return endpoint


for path, kind in (('observations', 'observation'), ('events', 'event'), ('media', 'media'), ('tracks', 'track')):
    router.add_api_route('/telemetry/'+path, query_endpoint(kind), methods=['GET'], response_model=TelemetryPage, operation_id='listTelemetry'+path.title())


@router.get('/objects/{id}/data', response_model=ObjectDataView, operation_id='getObjectData')
def object_data(id: UUID, request: Request, observations_start: datetime | None = None,
                observations_end: datetime | None = None, observations_device_id: UUID | None = None,
                observations_metric_id: str | None = None, auth: AuthRequest = Depends(authenticated)):
    svc = service(request, auth)
    return ObjectDataService(auth.uow, svc.registry).get(auth.actor, id, observations_start=observations_start,
        observations_end=observations_end, observations_device_id=observations_device_id, observations_metric_id=observations_metric_id)


@router.get('/telemetry/retention-preview', operation_id='previewTelemetryRetention')
def retention(object_id: UUID, before: datetime, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).retention_preview(auth.actor, object_id, before)
