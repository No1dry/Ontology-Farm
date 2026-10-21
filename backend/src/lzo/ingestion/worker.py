"""Ingestion handlers reuse F05 lease, current initiating actor and atomic batches."""
from uuid import UUID
from sqlalchemy import insert, select
from lzo.errors import ApiError, Conflict
from lzo.ingestion.models import import_rows, import_run_rows
from lzo.ingestion.schemas import MappedRecord, RowResult, ParseIssue
from lzo.ingestion.pipeline import IngestionService
from lzo.ontology.releases import current_execution_bundle
from lzo.worker.service import register_handler, register_result_kind, JobResultRef


def _references(payload):
    if set(payload)!={"preview_id"}:raise ApiError("JOB_POLICY_CHANGED",409,"Invalid import descriptor")
    return (UUID(payload["preview_id"]),)


def apply_import(ctx):
    preview_id=UUID(ctx.payload["preview_id"])
    # Each original/staged row is immutable. Result+semantic effect+checkpoint
    # share the real F05 transaction; failed row diagnostics use a savepoint.
    while True:
        with ctx.batch():
            ctx.uow.ontology_settings=ctx.uow.job_settings=ctx.runner.settings
            service=IngestionService(ctx.uow,ctx)
            report=service.get_preview(ctx.actor,preview_id)
            if current_execution_bundle(ctx.uow,ctx.actor)!=report.bundle:
                raise Conflict("IMPORT_PREVIEW_STALE","Configuration changed before the row")
            ordinal=(ctx.cursor or {}).get("ordinal",-1)
            row=ctx.uow.session.execute(select(import_rows).where(import_rows.c.preview_id==preview_id,
                import_rows.c.ordinal>ordinal).order_by(import_rows.c.ordinal).limit(1)).mappings().first()
            if row is None:
                ctx.set_result(JobResultRef(kind="ingestion.import",resource_id=ctx.job_id,api_path=f"/api/v1/imports/{ctx.job_id}"))
                break
            result=RowResult.model_validate(row["payload"]["result"])
            plan=row["payload"]["plan"]
            if result.state=="ready":
                from lzo.ingestion.admission import require
                require(ctx.uow,ctx.actor,report)
                try:
                    with ctx.uow.session.begin_nested():
                        if "draft_request" in plan:
                            from lzo.ingestion.draft_imports import apply_row
                            service._baseline(ctx.actor,report)
                            result=apply_row(ctx.uow,ctx.actor,report,result)
                        else:
                            result=service.process_row(ctx.uow,ctx.actor,MappedRecord.model_validate(plan["mapped"]),
                                report.bundle,plan["expected_revision"])
                except ApiError as error:
                    if error.status in (401,403,404):raise
                    result=result.model_copy(update={"state":"failed","issue_refs":(ParseIssue(locator=result.locator,
                        field_path=None,code=error.code,message=error.message),)})
            outcome={"result":result.model_dump(mode="json")}
            if result.state=="applied" and "draft_request" not in plan:
                from lzo.governance.models import revisions
                from lzo.ingestion.sources import digest
                event=ctx.uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==result.revision.event_id)).scalar_one()
                effect=event["effect"];key="row" if "row" in effect else "create" if "create" in effect else "patch"
                outcome.update(before_ref=plan["before_ref"],effect_digest=digest(plan["effect"]),decision_ref=effect[key]["decision_ref"])
            ctx.uow.session.execute(insert(import_run_rows).values(run_id=ctx.job_id,ordinal=row["ordinal"],payload=outcome))
            ctx.checkpoint({"ordinal":row["ordinal"]})


def _run(ctx):
    return IngestionService(context=ctx).run_batch(ctx.actor,ctx.job_id,ctx.lease_token)


register_handler("ingestion.apply",_run,references=_references,action="source.manage")
register_result_kind("ingestion.import","/api/v1/imports/{resource_id}")
