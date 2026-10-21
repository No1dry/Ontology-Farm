"""I04 orchestration of S02 draft edits, without release publication."""
from uuid import uuid4
from sqlalchemy import insert
from lzo.contracts import ResourceScope
from lzo.errors import ApiError,Conflict
from lzo.governance.revisions import append_revision
from lzo.ingestion.models import import_previews,import_rows
from lzo.ingestion.schemas import PreviewReport,RowResult,SourceKey
from lzo.ingestion.sources import add_scoped,digest
from lzo.ontology import drafts
from lzo.ontology.schemas import SemanticDraftImport
from lzo.semantic import facts


def request_for(selection):
    if not selection.upload or not selection.semantic_format or selection.file_options or selection.scan_id:
        raise ApiError("IMPORT_SELECTION_INVALID",422,"Draft import requires one semantic original")
    if selection.target_draft_id and selection.target_draft_revision is None:
        raise ApiError("PRECONDITION_REQUIRED",428,"An exact target draft revision is required")
    return SemanticDraftImport(blob=selection.upload,format=selection.semantic_format,kind=selection.semantic_kind,
        base_release_id=selection.base_release_id,target_draft_id=selection.target_draft_id,
        expected_revision=selection.target_draft_revision or 0)


def authorize(uow,actor,selection):
    request=request_for(selection)
    if request.target_draft_id:drafts.get_draft(uow,actor,request.target_draft_id,"schema.edit")
    else:drafts.global_permission(uow,actor,"schema.edit")
    drafts.artifact_reference(uow,actor,request.blob)
    return request


def preview(uow,actor,command,source,bundle):
    request=authorize(uow,actor,command.selection)
    # Use S02's exact parse/profile/base/rights validation in a rolled-back
    # savepoint. The immutable preview never leaves a speculative draft behind.
    with uow.session.begin_nested() as trial:
        drafts.import_semantic_draft(uow,actor,request)
        trial.rollback()
    identifier=uuid4();refs={source.id,bundle.id,request.blob.version_id}
    if request.base_release_id:refs.add(request.base_release_id)
    if request.target_draft_id:refs.add(request.target_draft_id)
    protected={str(i):facts.json_value(facts.revision_ref(uow,i)) for i in refs}
    baseline={i:ref["revision"] for i,ref in protected.items()}
    config=source.configuration
    row=RowResult(locator="document",source_key=SourceKey(source_id=source.id,source_system=config.source_system,
        source_instance=config.source_instance,collection=config.collection,canonical_key="draft:"+request.blob.sha256),state="ready")
    report=PreviewReport(id=identifier,revision=1,selection=command.selection,bundle=bundle,input_digest=digest(facts.json_value(request.blob)),
        baseline=baseline,rows=(row,),ready_count=1,review_count=0,invalid_count=0,complete=True,
        change_digest=digest({"request":request.model_dump(mode="json"),"baseline":baseline}),approval_id=None)
    scope=config.scope
    add_scoped(uow,actor,identifier,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),"ingestion.preview","source.manage")
    payload={"report":report.model_dump(mode="json"),"actor_id":str(actor.subject_id),"protected_ids":sorted(map(str,refs)),"protected_refs":protected}
    ref=append_revision(uow,actor,identifier,0,payload,"ingestion.preview.created")
    drafts.bind_dependencies(uow,identifier,refs,ref.event_id)
    uow.session.execute(insert(import_previews).values(id=identifier,payload=payload))
    uow.session.execute(insert(import_rows).values(preview_id=identifier,ordinal=0,payload={"result":row.model_dump(mode="json"),
        "plan":{"draft_request":request.model_dump(mode="json")}}))
    return report


def apply_row(uow,actor,report,result):
    request=authorize(uow,actor,report.selection)
    draft=drafts.import_semantic_draft(uow,actor,request)
    return result.model_copy(update={"state":"applied","entity_id":draft.id,"revision":facts.revision_ref(uow,draft.id)})
