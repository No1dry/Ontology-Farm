"""Typed ontology routes using the shared session, idempotency and revision contract."""
from dataclasses import asdict, is_dataclass
from uuid import UUID
from fastapi import APIRouter, Depends, Request
from lzo.auth.router import AuthRequest, authenticated
from lzo.contracts import JobRef, Page
from lzo.errors import Conflict, parse_if_match, require_idempotency_key
from lzo.governance.approval_schemas import ApprovalRef
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.risk import digest_change
from lzo.ontology import drafts as service
from lzo.ontology import releases
from lzo.ontology.schemas import (CreateDraftRequest, UpdateDraftRequest, SubmitDraftRequest,
    PublishReleaseRequest, SemanticDraftImport, SemanticDraftImportBody, SchemaDraftView, SchemaReleaseView, ImpactReport,
    FormSchemaView, InheritanceView, SchemaDiffView, SchemaDocument)
from lzo.semantic.semantic_profile import ancestors, form_schema

router = APIRouter(prefix="/api/v1/schema", tags=["Schema"])


def setup(auth):
    auth.uow.ontology_settings = auth.service.settings
    return auth.uow


def write(auth, request, operation, payload, command):
    from fastapi.encoders import jsonable_encoder
    result = execute_idempotent(setup(auth), auth.actor,
        require_idempotency_key(request.headers.get("Idempotency-Key")),
        digest_change({"operation": operation, **payload}), lambda: jsonable_encoder(command()))
    auth.uow.commit()
    return result


@router.get("/drafts", response_model=Page[SchemaDraftView], operation_id="listSchemaDrafts")
def list_drafts(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return service.list_drafts(setup(auth), auth.actor, cursor)


@router.post("/drafts", response_model=SchemaDraftView, status_code=201, operation_id="createSchemaDraft")
def create_draft(body: CreateDraftRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    service.global_permission(setup(auth), auth.actor, "schema.edit")
    service.document_refs(auth.uow, auth.actor, body.document, body.base_release_id)
    return write(auth, request, "schema.draft.create", body.model_dump(mode="json"),
                 lambda: service.create_draft(auth.uow, auth.actor, body))


@router.post("/drafts/imports", response_model=SchemaDraftView, status_code=201, operation_id="importSemanticDraft",
    openapi_extra={"parameters": [{"name": "If-Match", "in": "header", "required": False,
        "schema": {"type": "string"},
        "description": "Required when target_draft_id is supplied; the sole expected revision source, quoted as \"<revision>\". New targets start at revision 0."}]})
def import_draft(body: SemanticDraftImportBody, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    if body.target_draft_id:
        service.get_draft(auth.uow, auth.actor, body.target_draft_id, "schema.edit")
        expected = parse_if_match(request.headers.get("If-Match"))
    else:
        service.global_permission(auth.uow, auth.actor, "schema.edit")
        expected = 0
    service.artifact_reference(auth.uow, auth.actor, body.blob)
    command = SemanticDraftImport(**body.model_dump(), expected_revision=expected)
    return write(auth, request, "schema.draft.import", command.model_dump(mode="json"),
                 lambda: service.import_semantic_draft(auth.uow, auth.actor, command))


@router.get("/drafts/{id}", response_model=SchemaDraftView, operation_id="getSchemaDraft")
def get_draft(id: UUID, auth: AuthRequest = Depends(authenticated)):
    return service.draft_view(service.get_draft(setup(auth), auth.actor, id), uow=auth.uow, actor=auth.actor)


@router.patch("/drafts/{id}", response_model=SchemaDraftView, operation_id="updateSchemaDraft")
def update_draft(id: UUID, body: UpdateDraftRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    service.get_draft(setup(auth), auth.actor, id, "schema.edit")
    expected = parse_if_match(request.headers.get("If-Match"))
    return write(auth, request, "schema.draft.update", {"id": str(id), "revision": expected,
        **body.model_dump(mode="json")}, lambda: service.update_draft(auth.uow, auth.actor, id, body.document, expected))


@router.get("/drafts/{id}/form-schema", response_model=FormSchemaView, operation_id="getDraftFormSchema")
def draft_form(id: UUID, class_iri: str, auth: AuthRequest = Depends(authenticated)):
    row = service.get_draft(setup(auth), auth.actor, id)
    return form_schema(SchemaDocument.model_validate(row["document"]), class_iri)


@router.get("/drafts/{id}/inheritance", response_model=InheritanceView, operation_id="getDraftInheritance")
def inheritance(id: UUID, class_iri: str, auth: AuthRequest = Depends(authenticated)):
    row = service.get_draft(setup(auth), auth.actor, id)
    document = SchemaDocument.model_validate(row["document"])
    return InheritanceView(class_iri=class_iri, ancestors=ancestors(document, class_iri),
                           fields=form_schema(document, class_iri).fields, conflicts=())


@router.post("/drafts/{id}/validate", response_model=ImpactReport, operation_id="validateSchemaDraft")
def validate(id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    setup(auth)
    require_idempotency_key(request.headers.get("Idempotency-Key"))
    row = service.get_draft(auth.uow, auth.actor, id, "schema.edit")
    if row["revision"] != parse_if_match(request.headers.get("If-Match")):
        raise Conflict("REVISION_CONFLICT", "Draft revision changed")
    return releases.preview_release(auth.actor, id)


@router.post("/drafts/{id}/submit", response_model=ApprovalRef, status_code=201, operation_id="submitSchemaDraft")
def submit(id: UUID, body: SubmitDraftRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    service.get_draft(setup(auth), auth.actor, id, "schema.edit")
    expected = parse_if_match(request.headers.get("If-Match"))
    return write(auth, request, "schema.draft.submit", {"id": str(id), "revision": expected,
        **body.model_dump(mode="json")}, lambda: releases.submit_draft(auth.uow, auth.actor, id, body.impact_digest, expected))


@router.get("/releases", response_model=Page[SchemaReleaseView], operation_id="listSchemaReleases")
def list_releases(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return releases.list_releases(setup(auth), auth.actor, cursor)


@router.post("/releases", response_model=JobRef, status_code=202, operation_id="publishSchemaRelease")
def publish(body: PublishReleaseRequest, request: Request, auth: AuthRequest = Depends(authenticated)):
    service.get_draft(setup(auth), auth.actor, body.draft_id, "schema.publish")
    expected = parse_if_match(request.headers.get("If-Match"))
    return write(auth, request, "schema.release.publish", {"revision": expected, **body.model_dump(mode="json")},
        lambda: releases.publish_release(auth.uow, auth.actor, body.draft_id, body.approval_id, expected))


@router.get("/releases/{id}", response_model=SchemaReleaseView, operation_id="getSchemaRelease")
def get_release(id: UUID, auth: AuthRequest = Depends(authenticated)):
    return releases.get_release(setup(auth), auth.actor, id)


@router.get("/releases/{id}/form-schema", response_model=FormSchemaView, operation_id="getReleasedFormSchema")
def released_form(id: UUID, class_iri: str, auth: AuthRequest = Depends(authenticated)):
    return releases.get_released_form_schema(setup(auth), auth.actor, id, class_iri)


@router.get("/releases/{id}/diff", response_model=SchemaDiffView, operation_id="diffSchemaRelease")
def diff(id: UUID, base_release_id: UUID, auth: AuthRequest = Depends(authenticated)):
    return releases.diff_release(setup(auth), auth.actor, id, base_release_id)
