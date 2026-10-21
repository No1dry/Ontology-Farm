"""Bounded original upload with short preflight and post-transfer transactions."""
import asyncio
import json
from uuid import UUID, uuid4
from fastapi import APIRouter, Depends, Request, Response
from pydantic import ValidationError
from sqlalchemy import select
from starlette.datastructures import UploadFile
from starlette.requests import Request as FormRequest
from python_multipart.multipart import parse_options_header
from lzo.auth.router import AuthRequest, authenticated
from lzo.auth.models import grants, roles
from lzo.auth.permissions import current_version, permission_context, grant_predicate, validate_actor
from lzo.auth.session import SESSION_COOKIE, SessionService
from lzo.artifacts.router import _store
from lzo.contracts import Page
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError, require_idempotency_key, parse_if_match
from lzo.governance.idempotency import execute_idempotent
from lzo.ingestion.schemas import EvidenceCreate, EvidenceView, SourceCreate, SourceView
from lzo.ingestion.schemas import (MappingProfile, RuleVersion, RuleImpact, ImpactCommand, PublishRuleCommand,
    ConfigurationStageCommand, ConflictView, RulingView, RulingCommand,MergePreviewCommand,MergeCommand,MergeProposal,
    RefreshConflictCommand,ReviewRulingCommand)
from lzo.contracts import ExecutionBundle, RevisionRef
from lzo.ingestion.sources import SourceService, digest, add_scoped
from lzo.ingestion.evidence import EvidenceService

router = APIRouter(prefix="/api/v1", tags=["Ingestion"])

from lzo.ingestion.pipeline import IngestionService
from lzo.ingestion.schemas import ImportPreviewCommand, ApplyImportCommand, PreviewReport, ImportRunView, RowResult
from lzo.contracts import JobRef


def import_service(auth,request):
    auth.uow.ontology_settings=auth.uow.job_settings=request.app.state.settings
    return IngestionService(auth.uow)


@router.post("/imports/preview",response_model=PreviewReport,status_code=201,operation_id="previewImport",responses={202:{"model":JobRef}})
def preview_import(body:ImportPreviewCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    service=import_service(auth,request)
    SourceService(auth.uow).read(auth.actor,body.selection.source_id,"source.manage")
    key=require_idempotency_key(request.headers.get("Idempotency-Key"))
    from lzo.ingestion.preview_jobs import dispatch
    result=execute_idempotent(auth.uow,auth.actor,key,digest({"operation":"import.preview","command":body.model_dump(mode="json")}),
        lambda:facts_json(dispatch(auth.uow,auth.actor,body)))
    if "status" in result:
        from fastapi.responses import JSONResponse
        from lzo.worker.service import JobService
        job=JobService(auth.uow,request.app.state.settings).get(auth.actor,UUID(result["id"])).job
        auth.uow.commit();return JSONResponse(status_code=202,content=facts_json(job))
    view=service.get_preview(auth.actor,UUID(result["id"]))
    auth.uow.commit();return view


@router.post("/imports/apply",response_model=JobRef,status_code=202,operation_id="applyImport")
def apply_import(body:ApplyImportCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    service=import_service(auth,request)
    preview=service.get_preview(auth.actor,body.preview_id)
    SourceService(auth.uow).read(auth.actor,preview.selection.source_id,"source.manage")
    if parse_if_match(request.headers.get("If-Match"))!=body.expected_revision:
        raise ApiError("REVISION_CONFLICT",409,"Revision differs from command")
    key=require_idempotency_key(request.headers.get("Idempotency-Key"))
    result=execute_idempotent(auth.uow,auth.actor,key,digest({"operation":"import.apply","command":body.model_dump(mode="json")}),
        lambda:facts_json(service.apply(auth.uow,auth.actor,body)))
    auth.uow.commit();return JobRef(UUID(result["id"]),result["status"])


def facts_json(value):
    from lzo.semantic.facts import json_value
    return json_value(value)


@router.get("/imports/previews/{id}",response_model=PreviewReport,operation_id="getImportPreview")
def get_import_preview(id:UUID,request:Request,auth:AuthRequest=Depends(authenticated)):
    return import_service(auth,request).get_preview(auth.actor,id)


@router.get("/imports/previews/{id}/rows",response_model=Page[RowResult],operation_id="listImportPreviewRows")
def get_preview_rows(id:UUID,request:Request,cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    return import_service(auth,request).rows(auth.actor,id,cursor,preview=True)


@router.get("/imports/{id}",response_model=ImportRunView,operation_id="getImport")
def get_import(id:UUID,request:Request,auth:AuthRequest=Depends(authenticated)):
    return import_service(auth,request).get(auth.actor,id)


@router.get("/imports/{id}/rows",response_model=Page[RowResult],operation_id="listImportRows")
def get_import_rows(id:UUID,request:Request,cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    return import_service(auth,request).rows(auth.actor,id,cursor)

from lzo.ingestion.schemas import SourceCredentialInput,CredentialRef,ConnectionConfig,ConnectionCheck,ScheduleCommand,ScheduleView
from lzo.ingestion.credentials import SourceCredentialStore
from lzo.ingestion.sources import ConnectionService
from lzo.ingestion.scheduler import ScheduleService


def source_command(id,body,request,auth,action):
    SourceService(auth.uow).read(auth.actor,id,action)
    if parse_if_match(request.headers.get('If-Match'))!=body.expected_revision:
        raise ApiError('REVISION_CONFLICT',409,'Revision differs from command')
    if hasattr(body,'source_id') and body.source_id!=id:raise ApiError('SOURCE_CONFIGURATION_MISMATCH',422,'Source differs from route')
    return require_idempotency_key(request.headers.get('Idempotency-Key'))


@router.put('/sources/{id}/credentials',response_model=CredentialRef,operation_id='setSourceCredentials')
def set_credentials(id:UUID,body:SourceCredentialInput,request:Request,auth:AuthRequest=Depends(authenticated)):
    key=source_command(id,body,request,auth,'sources.credentials.manage')
    store=SourceCredentialStore(request.app.state.settings)
    result=execute_idempotent(auth.uow,auth.actor,key,store.command_digest(id,body),
        lambda:store.set(auth.uow,auth.actor,id,body).model_dump(mode='json'))
    auth.uow.commit();return CredentialRef.model_validate(result)


@router.get('/sources/{id}/connection',response_model=ConnectionConfig,operation_id='getSourceConnection')
def get_connection(id:UUID,auth:AuthRequest=Depends(authenticated)):
    return ConnectionService(auth.uow).get(auth.actor,id)


@router.put('/sources/{id}/connection',response_model=ConnectionConfig,operation_id='configureSourceConnection')
def configure_connection(id:UUID,body:ConnectionConfig,request:Request,auth:AuthRequest=Depends(authenticated)):
    key=source_command(id,body,request,auth,'source.manage')
    result=execute_idempotent(auth.uow,auth.actor,key,digest({'operation':'source.connection','body':body.model_dump(mode='json')}),
        lambda:ConnectionService(auth.uow).configure(auth.uow,auth.actor,body).model_dump(mode='json'))
    auth.uow.commit();return ConnectionConfig.model_validate(result)


@router.post('/sources/{id}/test',response_model=ConnectionCheck,operation_id='testSourceConnection')
def test_connection(id:UUID,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.sync import connector_for
    source=SourceService(auth.uow).read(auth.actor,id,'source.manage')
    config=ConnectionService(auth.uow).get(auth.actor,id)
    credentials=SourceCredentialStore(request.app.state.settings,auth.uow,
        authorize=lambda:SourceService(auth.uow).read(auth.actor,id,'source.manage').id)
    return connector_for(source,credentials,_store(request),request.app.state.settings).check(config)


@router.get('/sources/{id}/schedule',response_model=ScheduleView,operation_id='getSourceSchedule')
def get_schedule(id:UUID,auth:AuthRequest=Depends(authenticated)):
    return ScheduleService(auth.uow).get(auth.actor,id)


@router.put('/sources/{id}/schedule',response_model=ScheduleView,operation_id='configureSourceSchedule')
def configure_schedule(id:UUID,body:ScheduleCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    key=source_command(id,body,request,auth,'source.manage')
    result=execute_idempotent(auth.uow,auth.actor,key,digest({'operation':'source.schedule','body':body.model_dump(mode='json')}),
        lambda:ScheduleService(auth.uow).configure(auth.uow,auth.actor,body).model_dump(mode='json'))
    auth.uow.commit();return ScheduleView.model_validate(result)


def session(request, uow):
    current_version(uow, lock=True)
    sessions = SessionService(uow, request.app.state.settings)
    row = sessions.read(request.cookies.get(SESSION_COOKIE), lock=True)
    sessions.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
    return sessions.actor(row, request.state.request_id)


@router.post("/sources", response_model=SourceView, status_code=201, operation_id="createSource")
def create_source(body: SourceCreate, request: Request, auth: AuthRequest = Depends(authenticated)):
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    with auth.uow.session.begin_nested() as check:
        SourceService().create(auth.uow, auth.actor, body)
        check.rollback()
    result = execute_idempotent(auth.uow, auth.actor, key, digest({"source": body.model_dump(mode="json")}),
        lambda: SourceService().create(auth.uow, auth.actor, body).model_dump(mode="json"))
    SourceService(auth.uow).read(auth.actor, UUID(result["id"]), "source.manage")
    auth.uow.commit()
    return SourceView.model_validate(result)


@router.get("/sources", response_model=Page[SourceView], operation_id="listSources")
def list_sources(request: Request, cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    config = request.app.state.settings
    return SourceService(auth.uow).list(auth.actor, cursor, min(config.list_page_size, config.list_max_page_size))


@router.get("/sources/{id}", response_model=SourceView, operation_id="getSource")
def get_source(id: UUID, response: Response, auth: AuthRequest = Depends(authenticated)):
    result = SourceService(auth.uow).read(auth.actor, id)
    response.headers.update({"Cache-Control": "no-store", "ETag": f'"{result.revision}"'})
    return result


@router.get("/sources/{id}/mapping", response_model=MappingProfile, operation_id="getSourceMapping")
def get_mapping(id: UUID, auth: AuthRequest = Depends(authenticated)):
    return SourceService(auth.uow).get_mapping(auth.actor, id)


@router.put("/sources/{id}/mapping", response_model=MappingProfile, operation_id="saveSourceMapping")
def save_mapping(id: UUID, body: MappingProfile, request: Request, auth: AuthRequest = Depends(authenticated)):
    from lzo.errors import Conflict
    expected = parse_if_match(request.headers.get("If-Match"))
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    service = SourceService(auth.uow)
    current = service.read(auth.actor, id, "source.manage")
    if body.source_id != id: raise ApiError("SOURCE_MAPPING_MISMATCH", 422, "Source differs from route")
    def save():
        from lzo.governance.revisions import lock_ledger
        lock_ledger(auth.uow)
        if service.read(auth.actor, id, "source.manage").revision != expected:
            raise Conflict("REVISION_CONFLICT", "Source changed")
        return service.save_mapping(auth.uow, auth.actor, body).model_dump(mode="json")
    result = execute_idempotent(auth.uow, auth.actor, key,
        digest({"operation":"mapping.save", "source":str(id), "expected":expected, "profile":body.model_dump(mode="json",exclude={"id","revision","digest"})}), save)
    service.get_mapping(auth.actor, id, UUID(result["id"]))
    auth.uow.commit()
    return MappingProfile.model_validate(result)


def governed_command(auth,request,body,operation,execute,validate_result=None):
    key=require_idempotency_key(request.headers.get("Idempotency-Key"))
    payload=body.model_dump(mode="json")
    if isinstance(body,RuleVersion):
        for name in ("id","revision","digest"):payload.pop(name,None)
        for rule in payload["alignment_rules"]:
            rule.pop("id",None);rule.pop("digest",None)
        for rule in (*payload["authority_rules"],*payload["ruling_policies"]):rule.pop("digest",None)
    result=execute_idempotent(auth.uow,auth.actor,key,digest({"operation":operation,"body":payload}),execute)
    if validate_result is not None:validate_result(result)
    auth.uow.commit()
    return result


@router.post("/rules/versions",response_model=RuleVersion,status_code=201,operation_id="createRuleVersion")
def create_rule_version(body:RuleVersion,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    from lzo.ontology.drafts import global_permission
    global_permission(auth.uow,auth.actor,"schema.edit")
    # Validate current referenced rights even on an actor-command replay.
    with auth.uow.session.begin_nested() as check:
        RuleService(auth.uow).create_version(auth.uow,auth.actor,body)
        check.rollback()
    return governed_command(auth,request,body,"rule.create",lambda:RuleService(auth.uow).create_version(
        auth.uow,auth.actor,body).model_dump(mode="json"))


@router.get("/rules/versions",response_model=Page[RuleVersion],operation_id="listRuleVersions")
def list_rule_versions(request:Request,cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    return RuleService(auth.uow).list(auth.actor,cursor,min(request.app.state.settings.list_page_size,request.app.state.settings.list_max_page_size))


@router.get("/rules/versions/{id}",response_model=RuleVersion,operation_id="getRuleVersion")
def get_rule_version(id:UUID,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    return RuleService(auth.uow).get(auth.actor,id)


@router.post("/rules/bundles",response_model=ExecutionBundle,status_code=201,operation_id="stageRuleConfiguration")
def stage_rule_configuration(body:ConfigurationStageCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    from lzo.ontology.drafts import global_permission
    from lzo.semantic.facts import json_value
    global_permission(auth.uow,auth.actor,"schema.publish")
    from lzo.ingestion.models import mapping_profiles
    from lzo.ontology.drafts import authorize
    for identifier in (body.authority_version_id,body.ruling_policy_version_id):authorize(auth.uow,auth.actor,identifier)
    for identifier in body.profile_ids:
        authorize(auth.uow,auth.actor,identifier)
        source_id=auth.uow.session.execute(select(mapping_profiles.c.source_id).where(mapping_profiles.c.id==identifier)).scalar_one_or_none()
        if source_id is None:raise ApiError("NOT_FOUND",404,"Mapping not found")
        SourceService(auth.uow).read(auth.actor,source_id,"source.manage")
    return governed_command(auth,request,body,"rule.stage",lambda:json_value(RuleService(auth.uow).stage_configuration(
        auth.actor,body.profile_ids,body.authority_version_id,body.ruling_policy_version_id)))


@router.post("/rules/impact",response_model=RuleImpact,status_code=201,operation_id="previewRuleImpact")
def preview_rule_impact(body:ImpactCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    from lzo.ontology.drafts import global_permission
    global_permission(auth.uow,auth.actor,"schema.edit")
    from lzo.ontology.releases import get_execution_bundle
    with permission_context(auth.uow):get_execution_bundle(auth.actor,body.target_bundle_id)
    for identifier in body.selection.source_ids:SourceService(auth.uow).read(auth.actor,identifier,"source.manage")
    def check(result):
        from lzo.governance.approvals import ApprovalService
        impact=RuleService(auth.uow).get_impact(auth.actor,UUID(result["id"]))
        approval=ApprovalService(auth.uow).get(auth.actor,impact.approval_id)
        for identifier in approval.impact["affected_source_ids"]:SourceService(auth.uow).read(auth.actor,UUID(identifier),"source.manage")
    return governed_command(auth,request,body,"rule.impact",lambda:RuleService(auth.uow).impact(auth.actor,body).model_dump(mode="json"),check)


@router.get("/rules/impact/{id}",response_model=RuleImpact,operation_id="getRuleImpact")
def get_rule_impact(id:UUID,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    return RuleService(auth.uow).get_impact(auth.actor,id)


@router.post("/rules/{id}/publish",response_model=RevisionRef,operation_id="publishRules")
def publish_rules(id:UUID,body:PublishRuleCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rules import RuleService
    from lzo.semantic.facts import json_value
    if id!=body.rule_version_id or parse_if_match(request.headers.get("If-Match"))!=body.expected_revision:
        raise ApiError("REVISION_CONFLICT",409,"Rule version differs")
    # Rights and approval are checked before idempotency retrieval as well as
    # during execution; replay returns history, never a second catalog effect.
    from lzo.ontology.drafts import global_permission
    global_permission(auth.uow,auth.actor,"schema.publish")
    RuleService(auth.uow).get(auth.actor,id)
    from lzo.governance.approvals import ApprovalService
    approval=ApprovalService(auth.uow).get(auth.actor,body.approval_id)
    for identifier in approval.impact.get("affected_source_ids",()):
        SourceService(auth.uow).read(auth.actor,UUID(identifier),"source.manage")
    return governed_command(auth,request,body,"rule.publish",lambda:json_value(RuleService(auth.uow).publish(auth.uow,auth.actor,body)))


@router.get("/conflicts",response_model=Page[ConflictView],operation_id="listConflicts")
def list_conflicts(request:Request,cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    return RulingService(auth.uow).list_conflicts(auth.actor,cursor,min(request.app.state.settings.list_page_size,request.app.state.settings.list_max_page_size))


@router.get("/conflicts/{id}",response_model=ConflictView,operation_id="getConflict")
def get_conflict(id:UUID,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    return RulingService(auth.uow).get(auth.actor,id)


@router.get("/conflicts/{id}/rulings",response_model=Page[RulingView],operation_id="listConflictRulings")
def list_rulings(id:UUID,request:Request,cursor:str|None=None,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    return RulingService(auth.uow).list_rulings(auth.actor,id,cursor,min(request.app.state.settings.list_page_size,request.app.state.settings.list_max_page_size))


@router.post("/conflicts/{id}/rulings",response_model=RulingView,status_code=201,operation_id="resolveConflict")
def resolve_conflict(id:UUID,body:RulingCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    if parse_if_match(request.headers.get("If-Match"))!=body.expected_revision:
        raise ApiError("REVISION_CONFLICT",409,"Conflict revision differs")
    from lzo.artifacts.service import _permission
    _permission(auth.uow,auth.actor,id,"conflict.resolve")
    RulingService(auth.uow).get(auth.actor,id)
    return governed_command(auth,request,body,"ruling.resolve:"+str(id),lambda:RulingService(auth.uow).resolve(
        auth.uow,auth.actor,id,body).model_dump(mode="json"))


@router.post("/conflicts/{id}/review",response_model=RulingView,operation_id="reevaluateConflictRuling")
def review_conflict(id:UUID,body:ReviewRulingCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    from lzo.ontology.drafts import authorize
    if parse_if_match(request.headers.get("If-Match"))!=body.expected_revision:
        raise ApiError("REVISION_CONFLICT",409,"Conflict revision differs")
    service=RulingService(auth.uow);authorize(auth.uow,auth.actor,id,"conflict.resolve")
    def execute():
        conflict=service.get(auth.actor,id);ruling=service.ruling(auth.actor,body.ruling_ref.resource_id)
        if conflict.revision!=body.expected_revision or ruling.conflict_id!=id or ruling.revision!=body.ruling_ref:
            raise ApiError("REVISION_CONFLICT",409,"Review input changed")
        return service.reevaluate(auth.uow,auth.actor,ruling.id,ruling.dependencies,False).model_dump(mode="json")
    return governed_command(auth,request,body,"ruling.review:"+str(id),execute)


@router.post("/conflicts/{id}/refresh",response_model=ConflictView,operation_id="refreshConflictCandidates")
def refresh_conflict(id:UUID,body:RefreshConflictCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.rulings import RulingService
    from lzo.ontology.drafts import authorize
    if parse_if_match(request.headers.get("If-Match"))!=body.expected_revision:
        raise ApiError("REVISION_CONFLICT",409,"Conflict revision differs")
    authorize(auth.uow,auth.actor,id,"conflict.resolve")
    RulingService(auth.uow).get(auth.actor,id)
    return governed_command(auth,request,body,"ruling.refresh:"+str(id),lambda:RulingService(auth.uow).refresh(
        auth.uow,auth.actor,id,body.expected_revision,body.ruling_ref).model_dump(mode="json"))


@router.post("/identity/merges/preview",response_model=MergeProposal,status_code=201,operation_id="previewIdentityMerge")
def preview_identity_merge(body:MergePreviewCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.identity import IdentityService
    from lzo.semantic.facts import get_object_row
    from lzo.ontology.drafts import authorize
    for identifier in (body.canonical_id,*body.duplicate_ids):get_object_row(auth.uow,auth.actor,identifier,"identity.merge")
    return governed_command(auth,request,body,"identity.merge.preview",lambda:IdentityService(auth.uow).preview_merge(
        auth.actor,body.canonical_id,body.duplicate_ids).model_dump(mode="json"),lambda result:authorize(auth.uow,auth.actor,UUID(result["id"])))


@router.post("/identity/merges",response_model=RevisionRef,operation_id="mergeIdentities")
def merge_identities(body:MergeCommand,request:Request,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.identity import IdentityService
    from lzo.semantic.facts import json_value
    from lzo.ontology.drafts import authorize
    authorize(auth.uow,auth.actor,body.proposal_id,"identity.merge")
    from lzo.ingestion.models import merge_proposals
    from lzo.semantic.facts import get_object_row
    stored=auth.uow.session.execute(select(merge_proposals.c.payload).where(merge_proposals.c.id==body.proposal_id)).scalar_one_or_none()
    if stored is None:raise ApiError("NOT_FOUND",404,"Merge proposal not found")
    for identifier in stored["plan"]["affected_ids"]:
        get_object_row(auth.uow,auth.actor,UUID(identifier),"identity.merge")
        get_object_row(auth.uow,auth.actor,UUID(identifier),"edit")
    if parse_if_match(request.headers.get("If-Match"))!=1:raise ApiError("REVISION_CONFLICT",409,"Unconsumed proposal required")
    return governed_command(auth,request,body,"identity.merge",lambda:json_value(IdentityService(auth.uow).merge(
        auth.uow,auth.actor,body.proposal_id,body.approval_id)))


@router.get("/identity/aliases/{id}",response_model=UUID,operation_id="resolveIdentityAlias")
def resolve_identity_alias(id:UUID,auth:AuthRequest=Depends(authenticated)):
    from lzo.ingestion.identity import IdentityService
    return IdentityService(auth.uow).resolve_alias(auth.actor,id)


async def multipart(request):
    config = request.app.state.settings
    # Bound the actual body before Starlette may spool a file. Content-Length
    # and the multipart spool threshold are not byte limits.
    body = bytearray()
    async def receive_body():
        async for chunk in request.stream():
            if len(body) + len(chunk) > config.artifact_max_upload_bytes + 32768:
                raise ApiError("UPLOAD_TOO_LARGE", 413, "Evidence form exceeds byte limit")
            body.extend(chunk)
    try:
        await asyncio.wait_for(receive_body(), timeout=config.ingestion_transfer_seconds)
        media, params = parse_options_header(request.headers.get("content-type", ""))
        boundary = params.get(b"boundary", b"")
        if media != b"multipart/form-data" or not 1 <= len(boundary) <= 70 or not bytes(body).rstrip(b"\r\n").endswith(b"--"+boundary+b"--"):
            raise ValueError()
        async def receive():
            return {"type":"http.request", "body":bytes(body), "more_body":False}
        form_request = FormRequest(request.scope, receive)
        async with form_request.form(max_files=1, max_fields=1, max_part_size=16384) as form:
            if sorted(k for k,v in form.multi_items()) != ["file", "metadata"]:
                raise ValueError()
            file, metadata = form["file"], form["metadata"]
            if not isinstance(file, UploadFile) or not isinstance(metadata, str) or not file.size:
                raise ValueError()
            if file.size > config.artifact_max_upload_bytes:
                raise ApiError("UPLOAD_TOO_LARGE", 413, "Evidence file exceeds byte limit")
            def pairs(items):
                values = {}
                for name, value in items:
                    if name in values:
                        raise ValueError()
                    values[name] = value
                return values
            def nonfinite(value):
                raise ValueError()
            command = EvidenceCreate.model_validate(json.loads(metadata, object_pairs_hook=pairs, parse_constant=nonfinite))
            # Exact scope/target authorization before staging/registration.
            with PgUnitOfWork(request.app.state.engine) as uow:
                actor = session(request, uow)
                with permission_context(uow):
                    EvidenceService(settings=config).preflight(uow, actor, command,
                        parse_if_match(request.headers.get("If-Match")) if command.replaces_id else None)
            store = _store(request)
            async def chunks():
                while chunk := await file.read(1024*1024):
                    yield chunk
            staged = await asyncio.wait_for(store.stage_async(chunks(), file.content_type or "application/octet-stream"),
                timeout=config.ingestion_transfer_seconds)
            return command, store, staged
    except ApiError:
        raise
    except TimeoutError:
        raise ApiError("UPLOAD_TIMEOUT", 422, "Evidence transfer did not complete") from None
    except Exception:
        raise ApiError("INVALID_MULTIPART", 422, "Evidence requires one complete file and typed metadata") from None


@router.post("/artifacts/evidence", response_model=EvidenceView, status_code=201, operation_id="createEvidence",
    openapi_extra={"requestBody":{"required":True,"content":{"multipart/form-data":{"schema":{
        "type":"object","required":["file","metadata"],"additionalProperties":False,
        "properties":{"file":{"type":"string","format":"binary"},"metadata":{"$ref":"#/components/schemas/EvidenceCreate"}}},
        "encoding":{"metadata":{"contentType":"application/json"}}}}}})
async def create_evidence(request: Request):
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    config = request.app.state.settings
    # Intake requires a complete active evidence-management grant. Exact scope
    # is in the bounded multipart metadata and is checked before registration.
    with PgUnitOfWork(request.app.state.engine) as uow:
        actor = session(request, uow)
        validate_actor(uow, actor)
        if not uow.session.execute(select(grants.c.id).select_from(grants.join(roles)).where(
            grant_predicate(actor, "evidence.manage")).limit(1)).first():
            raise ApiError("FORBIDDEN", 403, "Evidence management permission is required")
    command, store, staged = await multipart(request)
    try:
        expected = parse_if_match(request.headers.get("If-Match")) if command.replaces_id else None
        with PgUnitOfWork(request.app.state.engine) as uow:
            actor = session(request, uow)
            with permission_context(uow):
                service = EvidenceService(uow, store, config)
                with uow.session.begin_nested() as check:
                    service.preflight(uow, actor, command, expected)
                    check.rollback()
                fingerprint = digest({"evidence":command.model_dump(mode="json"), "sha256":staged.ref.sha256,
                    "size":staged.ref.size_bytes, "media_type":staged.ref.media_type, "expected_revision":expected})
                result = execute_idempotent(uow, actor, key, fingerprint, lambda: service.create(
                    uow, actor, staged, staged.ref.media_type, command, expected_revision=expected).model_dump(mode="json"))
                service.read(actor, UUID(result["id"]))
                uow.commit()
        return EvidenceView.model_validate(result)
    finally:
        store.discard(staged)


@router.get("/artifacts/evidence/{id}", response_model=EvidenceView, operation_id="getEvidence")
def get_evidence(id: UUID, request: Request, response: Response, auth: AuthRequest = Depends(authenticated)):
    result = EvidenceService(auth.uow, _store(request), request.app.state.settings).read(auth.actor, id)
    response.headers.update({"Cache-Control":"no-store", "ETag":f'"{result.revision.revision}"'})
    return result
