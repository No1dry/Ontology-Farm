"""Bounded preview planning on F05, with immutable registered input membership."""
from uuid import UUID
from sqlalchemy import insert,select,update
from lzo.errors import ApiError,Conflict
from lzo.ingestion.models import preview_jobs,preview_work_rows,source_records
from lzo.ingestion.schemas import ImportPreviewCommand,SourceChange,RowResult,ApplyImportCommand
from lzo.ingestion.sources import SourceService
from lzo.ontology.drafts import authorize,settings_for
from lzo.ontology.releases import current_execution_bundle
from lzo.semantic import facts
from lzo.worker.models import job_dependencies
from lzo.worker.service import enqueue,register_handler,register_result_kind,JobResultRef


def references(payload):
    if set(payload)!={"command","auto_apply"} or type(payload["auto_apply"]) is not bool:
        raise ApiError("JOB_POLICY_CHANGED",409,"Exact preview descriptor required")
    selection=ImportPreviewCommand.model_validate(payload["command"]).selection
    return tuple(dict.fromkeys(i for i in (selection.source_id,selection.upload.version_id if selection.upload else None,
        selection.scan_id,selection.base_release_id,selection.target_draft_id) if i))


def enqueue_preview(uow,actor,command,*,auto_apply=False):
    source=SourceService(uow).read(actor,command.selection.source_id,"source.manage")
    bundle=current_execution_bundle(uow,actor)
    if bundle.id!=command.bundle_id:raise Conflict("BUNDLE_NOT_ACTIVE","Preview requires current configuration")
    if command.selection.semantic_kind in ("schema","shapes"):
        from lzo.ingestion.draft_imports import authorize as authorize_draft
        authorize_draft(uow,actor,command.selection)
    job=enqueue(uow,actor,"ingestion.preview",{"command":command.model_dump(mode="json"),"auto_apply":auto_apply},bundle.id)
    uow.session.execute(insert(preview_jobs).values(job_id=job.id,payload={"source_ref":facts.json_value(facts.revision_ref(uow,source.id))}))
    return job


def dispatch(uow,actor,command):
    from lzo.ingestion.pipeline import IngestionService
    service=IngestionService(uow);selection=command.selection;settings=settings_for(uow)
    source=SourceService(uow).read(actor,selection.source_id,"source.manage")
    bundle=current_execution_bundle(uow,actor)
    if bundle.id!=command.bundle_id:raise Conflict("BUNDLE_NOT_ACTIVE","Preview requires current configuration")
    if selection.scan_id or selection.upload and selection.upload.size_bytes>settings.ingestion_preview_sync_bytes:
        return enqueue_preview(uow,actor,command)
    if selection.semantic_kind in ("schema","shapes"):return service.preview(actor,command)
    changes=service._changes(actor,selection)
    if len(changes)>settings.ingestion_preview_sync_rows:return enqueue_preview(uow,actor,command)
    profile=service._profile(actor,source.id,bundle)
    from lzo.ingestion.planning import arrange
    changes=arrange(uow,actor,profile,changes)
    rows,plans,refs,baseline=service.plan_changes(actor,source,profile,bundle,changes)
    return service.finish_preview(actor,command,source,bundle,changes,rows,plans,refs,baseline)


def _run(ctx):
    from lzo.ingestion.pipeline import IngestionService
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    command=ImportPreviewCommand.model_validate(ctx.payload["command"])
    while True:
        with ctx.batch():
            uow=ctx.uow;uow.ontology_settings=uow.job_settings=ctx.runner.settings
            service=IngestionService(uow);source=SourceService(uow).read(ctx.actor,command.selection.source_id,"source.manage")
            meta=uow.session.execute(select(preview_jobs.c.payload).where(preview_jobs.c.job_id==ctx.job_id)).scalar_one()
            if facts.json_value(facts.revision_ref(uow,source.id))!=meta["source_ref"]:
                raise Conflict("IMPORT_PREVIEW_STALE","Source configuration changed during preview")
            bundle=current_execution_bundle(uow,ctx.actor)
            if bundle.id!=command.bundle_id:raise Conflict("IMPORT_PREVIEW_STALE","Published configuration changed during preview")
            if (ctx.cursor or {}).get("phase")=="complete":break
            if command.selection.semantic_kind in ("schema","shapes"):
                report=service.preview(ctx.actor,command)
            else:
                profile=service._profile(ctx.actor,source.id,bundle)
                if not ctx.cursor:
                    changes=service._changes(ctx.actor,command.selection)
                    if len(changes)>ctx.runner.settings.ingestion_max_rows:raise ApiError("PREVIEW_LIMIT_EXCEEDED",422,"Preview exceeds configured row budget")
                    from lzo.ingestion.planning import arrange
                    changes=arrange(uow,ctx.actor,profile,changes)
                    for ordinal,change in enumerate(changes):
                        uow.session.execute(insert(preview_work_rows).values(job_id=ctx.job_id,ordinal=ordinal,change_id=change.change_id))
                        uow.session.execute(pg_insert(job_dependencies).values(job_id=ctx.job_id,resource_id=change.change_id).on_conflict_do_nothing())
                    ctx.checkpoint({"phase":"planning","ordinal":-1,"count":len(changes)})
                    continue
                work=uow.session.execute(select(preview_work_rows,source_records.c.payload.label("change")).join(
                    source_records,source_records.c.id==preview_work_rows.c.change_id).where(preview_work_rows.c.job_id==ctx.job_id,
                    preview_work_rows.c.ordinal>ctx.cursor["ordinal"]).order_by(preview_work_rows.c.ordinal)
                    .limit(ctx.runner.settings.ingestion_preview_rows_per_batch)).mappings().all()
                if work:
                    all_work=uow.session.execute(select(preview_work_rows,source_records.c.payload.label('change')).join(
                        source_records,source_records.c.id==preview_work_rows.c.change_id).where(preview_work_rows.c.job_id==ctx.job_id)
                        .order_by(preview_work_rows.c.ordinal)).mappings().all()
                    prior=[entry['payload']['plan'] for entry in all_work if entry['ordinal']<=ctx.cursor['ordinal']
                        and entry['payload'] and entry['payload'].get('plan')]
                    rows,plans,refs,baseline=service.plan_changes(ctx.actor,source,profile,bundle,
                        tuple(SourceChange.model_validate(entry['change']) for entry in work),prefix_plans=prior,
                        all_changes=tuple(SourceChange.model_validate(entry['change']) for entry in all_work))
                    for entry,row,plan in zip(work,rows,plans):
                        pins={str(i):facts.json_value(facts.revision_ref(uow,i)) for i in refs}
                        uow.session.execute(update(preview_work_rows).where(preview_work_rows.c.job_id==ctx.job_id,
                            preview_work_rows.c.ordinal==entry["ordinal"]).values(payload={"result":row.model_dump(mode="json"),
                                "plan":plan,"protected_refs":pins,"baseline":baseline}))
                        for identifier in refs:
                            uow.session.execute(pg_insert(job_dependencies).values(job_id=ctx.job_id,resource_id=identifier).on_conflict_do_nothing())
                    ctx.checkpoint({**ctx.cursor,"ordinal":work[-1]["ordinal"]})
                    continue
                changes=[];rows=[];plans=[];refs={source.id,profile.id,bundle.id};baseline={}
                for entry in uow.session.execute(select(preview_work_rows,source_records.c.payload.label("change")).join(
                        source_records,source_records.c.id==preview_work_rows.c.change_id).where(preview_work_rows.c.job_id==ctx.job_id)
                        .order_by(preview_work_rows.c.ordinal)).mappings():
                    value=entry["payload"]
                    if value is None:raise Conflict("IMPORT_PREVIEW_INCOMPLETE","A staged row is incomplete")
                    for key,ref in value["protected_refs"].items():
                        identifier=UUID(key);authorize(uow,ctx.actor,identifier)
                        if facts.json_value(facts.revision_ref(uow,identifier))!=ref:raise Conflict("IMPORT_PREVIEW_STALE","Planning dependency changed")
                        refs.add(identifier)
                    for key,revision in value["baseline"].items():
                        if key in baseline and baseline[key]!=revision:raise Conflict("IMPORT_PREVIEW_STALE","Row baselines disagree")
                        baseline[key]=revision
                    changes.append(SourceChange.model_validate(entry["change"]));rows.append(RowResult.model_validate(value["result"]));plans.append(value["plan"])
                report=service.finish_preview(ctx.actor,command,source,bundle,changes,rows,plans,refs,baseline)
            ctx.set_result(JobResultRef(kind="ingestion.preview",resource_id=report.id,api_path=f"/api/v1/imports/previews/{report.id}"))
            ctx.checkpoint({"phase":"complete","preview_id":str(report.id)})
            if ctx.payload["auto_apply"] and report.ready_count and report.approval_id:
                from lzo.governance.approvals import ApprovalService
                approval=ApprovalService(uow).get(ctx.actor,report.approval_id)
                if approval.risk=="low":
                    service.apply(uow,ctx.actor,ApplyImportCommand(preview_id=report.id,expected_revision=report.revision,
                        input_digest=report.input_digest,approval_id=report.approval_id))
            break


register_handler("ingestion.preview",_run,references=references,action="source.manage")
register_result_kind("ingestion.preview","/api/v1/imports/previews/{resource_id}")
