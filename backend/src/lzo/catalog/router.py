"""Additive /api/v1 catalog API; authenticated caller owns the core UoW."""
from datetime import date
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from lzo.auth.router import AuthRequest, authenticated
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.catalog.schemas import (CreateModel, CreateDocument, VersionModel, VersionDocument,
    CatalogView, CatalogPage, DeviceBinding, DeviceModelView, CatalogRetry, CatalogRetryReceipt)
from lzo.catalog.service import CatalogService
from lzo.governance.approval_router import PreviewDetail
from lzo.governance.risk import classify

router = APIRouter(prefix='/api/v1/catalog', tags=['Catalog'])


def service(request, auth):
    auth.uow.ontology_settings = request.app.state.settings
    return CatalogService(auth.uow, request.app.state.storage_registry)


def key(request):
    return require_idempotency_key(request.headers.get('Idempotency-Key'))


def precondition(request, body):
    if parse_if_match(request.headers.get('If-Match')) != body.expected_revision:
        raise ApiError('REVISION_CONFLICT', 409, 'Revision differs from command')


def preview_detail(change):
    risk, reasons = classify(change)
    return PreviewDetail(kind=change.kind, target_ids=list(change.target_ids), command=change.command,
        change_digest=change.change_digest, baseline=change.baseline, impact=change.impact, risk=risk, reasons=reasons)


@router.post('/models', response_model=CatalogView, status_code=201, operation_id='createCatalogModel')
def create_model(body: CreateModel, request: Request, auth: AuthRequest = Depends(authenticated)):
    value = service(request, auth).create_model(auth.actor, body, key(request))
    auth.uow.commit()
    return value


@router.post('/documents', response_model=CatalogView, status_code=201, operation_id='createCatalogDocument')
def create_document(body: CreateDocument, request: Request, auth: AuthRequest = Depends(authenticated)):
    value = service(request, auth).create_document(auth.actor, body, key(request))
    auth.uow.commit()
    return value


@router.post('/models/{id}/retry', response_model=CatalogRetryReceipt, status_code=202, operation_id='retryCatalogModel')
def retry_model(id: UUID, body: CatalogRetry, request: Request, auth: AuthRequest = Depends(authenticated)):
    return retry_version(id, 'model', body, request, auth)


@router.post('/documents/{id}/retry', response_model=CatalogRetryReceipt, status_code=202, operation_id='retryCatalogDocument')
def retry_document(id: UUID, body: CatalogRetry, request: Request, auth: AuthRequest = Depends(authenticated)):
    return retry_version(id, 'document', body, request, auth)


def retry_version(id, kind, body, request, auth):
    from lzo.catalog.recovery import retry
    precondition(request, body)
    result = retry(service(request, auth), auth.actor, id, kind, body, key(request))
    auth.uow.commit()
    return result


@router.get('/models', response_model=CatalogPage, operation_id='listCatalogModels')
def list_models(request: Request, text: str | None = None, cursor: str | None = None, limit: int = 50,
                auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).list(auth.actor, 'model', text=text, cursor=cursor, limit=limit)


@router.get('/documents', response_model=CatalogPage, operation_id='listCatalogDocuments')
def list_documents(request: Request, text: str | None = None, document_type: str | None = None,
                   model_id: UUID | None = None, start: date | None = None, end: date | None = None,
                   cursor: str | None = None, limit: int = 50, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).list(auth.actor, 'document', text=text, document_type=document_type,
        model_id=model_id, start=start, end=end, cursor=cursor, limit=limit)


@router.get('/models/{id}', response_model=CatalogView, operation_id='getCatalogModel')
def get_model(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).get(auth.actor, id, kind='model')


@router.get('/documents/{id}', response_model=CatalogView, operation_id='getCatalogDocument')
def get_document(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).get(auth.actor, id, kind='document')


@router.post('/models/{id}/versions', response_model=CatalogView, status_code=201, operation_id='versionCatalogModel')
def version_model(id: UUID, body: VersionModel, request: Request, auth: AuthRequest = Depends(authenticated)):
    precondition(request, body)
    value = service(request, auth).version_model(auth.actor, id, body, key(request))
    auth.uow.commit()
    return value


@router.post('/documents/{id}/versions', response_model=CatalogView, status_code=201, operation_id='versionCatalogDocument')
def version_document(id: UUID, body: VersionDocument, request: Request, auth: AuthRequest = Depends(authenticated)):
    precondition(request, body)
    value = service(request, auth).version_document(auth.actor, id, body, key(request))
    auth.uow.commit()
    return value


@router.get('/models/{id}/versions/{revision}', response_model=CatalogView, operation_id='getCatalogModelVersion')
def get_model_version(id: UUID, revision: int, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).get(auth.actor, id, revision=revision, kind='model')


@router.get('/models/{id}/versions', response_model=CatalogPage, operation_id='listCatalogModelVersions')
def model_versions(id: UUID, request: Request, cursor: str | None = None, limit: int = 50,
                   auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).versions(auth.actor, id, kind='model', cursor=cursor, limit=limit)


@router.get('/documents/{id}/versions/{revision}', response_model=CatalogView, operation_id='getCatalogDocumentVersion')
def get_document_version(id: UUID, revision: int, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).get(auth.actor, id, revision=revision, kind='document')


@router.get('/documents/{id}/versions', response_model=CatalogPage, operation_id='listCatalogDocumentVersions')
def document_versions(id: UUID, request: Request, cursor: str | None = None, limit: int = 50,
                      auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).versions(auth.actor, id, kind='document', cursor=cursor, limit=limit)


@router.post('/models/{id}/preview', response_model=PreviewDetail, operation_id='previewCatalogModelVersion')
def preview_model(id: UUID, body: VersionModel, request: Request, auth: AuthRequest = Depends(authenticated)):
    precondition(request, body)
    value = service(request, auth).preview_version(auth.actor, id, body)
    auth.uow.commit()
    return preview_detail(value)


@router.post('/documents/{id}/preview', response_model=PreviewDetail, operation_id='previewCatalogDocumentVersion')
def preview_document(id: UUID, body: VersionDocument, request: Request, auth: AuthRequest = Depends(authenticated)):
    precondition(request, body)
    value = service(request, auth).preview_version(auth.actor, id, body)
    auth.uow.commit()
    return preview_detail(value)


@router.put('/models/{id}/devices', response_model=DeviceModelView, operation_id='bindCatalogDevice')
def bind_device(id: UUID, body: DeviceBinding, request: Request, auth: AuthRequest = Depends(authenticated)):
    precondition(request, body)
    value = service(request, auth).bind_device(auth.actor, id, body, key(request))
    auth.uow.commit()
    return value


@router.get('/models/{id}/devices', response_model=list[DeviceModelView], operation_id='listCatalogDevices')
def devices(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return service(request, auth).devices(auth.actor, id)
