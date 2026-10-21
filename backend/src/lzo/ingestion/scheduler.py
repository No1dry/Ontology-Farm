"""Due-window serialization uses the shared ledger and queue, preserving identity."""
from datetime import datetime,timezone,timedelta
from uuid import UUID
import logging
from sqlalchemy import select,update,DateTime,or_,func
from sqlalchemy.dialects.postgresql import insert
from lzo.auth.permissions import current_version
from lzo.contracts import ActorContext,JobRef
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError,Conflict
from lzo.governance.revisions import append_revision,lock_ledger
from lzo.ingestion.models import sources,source_schedules
from lzo.ingestion.schemas import ScheduleView
from lzo.ingestion.sources import SourceService,ConnectionService
from lzo.worker.service import enqueue,register_handler
logger=logging.getLogger(__name__)


class ScheduleService:
    def __init__(self,uow):self.uow=uow

    def get(self,actor,source_id):
        source=SourceService(self.uow).read(actor,source_id)
        row=self.uow.session.execute(select(source_schedules).where(source_schedules.c.source_id==source_id)).mappings().first()
        if row is None:return ScheduleView(source_id=source_id,revision=source.revision,enabled=False,interval_seconds=3600,next_run_at=None)
        return ScheduleView(source_id=source_id,revision=source.revision,enabled=row['payload']['enabled'],
            interval_seconds=row['payload']['interval_seconds'],next_run_at=row['next_run_at'])

    def configure(self,uow,actor,command):
        SourceService(uow).read(actor,command.source_id,'source.manage');lock_ledger(uow)
        source=SourceService(uow).read(actor,command.source_id,'source.manage')
        if source.revision!=command.expected_revision:raise Conflict('REVISION_CONFLICT','Source changed')
        connection=ConnectionService(uow).get(actor,source.id)
        event=append_revision(uow,actor,source.id,source.revision,command.model_dump(mode='json'),'source.schedule.changed')
        when=datetime.now(timezone.utc)+timedelta(seconds=command.interval_seconds) if command.enabled else None
        values=dict(revision=event.revision,initiator_id=actor.subject_id,next_run_at=when,payload=command.model_dump(mode='json'))
        statement=insert(source_schedules).values(source_id=source.id,**values)
        uow.session.execute(statement.on_conflict_do_update(index_elements=['source_id'],set_=values))
        uow.session.execute(update(sources).where(sources.c.id==source.id).values(revision=event.revision))
        return self.get(actor,source.id)


class Scheduler:
    def __init__(self,engine,settings):self.engine,self.settings=engine,settings

    def tick(self,now:datetime)->tuple[JobRef,...]:
        if now.tzinfo is None:raise ValueError('Aware UTC schedule time required')
        retry_at=source_schedules.c.payload['_dispatch']['retry_at'].astext.cast(DateTime(timezone=True))
        with PgUnitOfWork(self.engine) as uow:
            identifiers=list(uow.session.execute(select(source_schedules.c.source_id).where(source_schedules.c.next_run_at<=now,
                or_(retry_at.is_(None),retry_at<=now))
                # Compare every window by when it next became eligible. Both
                # success and failure move that time forward, so recurring fresh
                # work and expired retries cannot permanently outrank each other.
                .order_by(func.coalesce(retry_at,source_schedules.c.next_run_at),source_schedules.c.next_run_at,source_schedules.c.source_id).limit(self.settings.job_claim_scan_limit)).scalars())
        results=[]
        for identifier in identifiers:
            with PgUnitOfWork(self.engine) as uow:
                version=current_version(uow,lock=True);lock_ledger(uow)
                row=uow.session.execute(select(source_schedules).where(source_schedules.c.source_id==identifier).with_for_update()).mappings().one()
                dispatch=row['payload'].get('_dispatch',{})
                if row['next_run_at'] is None or row['next_run_at']>now:continue
                if dispatch.get('retry_at') and datetime.fromisoformat(dispatch['retry_at'])>now:continue
                job=None
                try:
                    # Roll back any attempted enqueue before persisting retry
                    # metadata; authorization/ledger/row locks remain outermost.
                    with uow.session.begin_nested():
                        actor=ActorContext(row['initiator_id'],'schedule:'+row['next_run_at'].isoformat(),version)
                        source=SourceService(uow).read(actor,identifier,'source.manage')
                        connection=ConnectionService(uow).get(actor,identifier)
                        from lzo.ontology.models import catalog
                        bundle=uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one_or_none()
                        payload={'resource_id':str(identifier),'configuration_revision':source.connection_revision,
                            'schedule_revision':row['revision'],'due_at':row['next_run_at'].isoformat(),
                            'mapping_version_id':str(source.mapping_version_id) if source.mapping_version_id else None}
                        job=enqueue(uow,actor,'ingestion.scan',payload,bundle)
                except ApiError as error:
                    failures=min(dispatch.get('failures',0)+1,32)
                    delay=min(max(1,self.settings.job_retry_cap_seconds),max(1,self.settings.job_retry_base_seconds)*2**min(failures-1,16))
                    # Operational backoff does not alter the original due window,
                    # configuration revision or initiating actor's authority.
                    uow.session.execute(update(source_schedules).where(source_schedules.c.source_id==identifier).values(
                        payload={**row['payload'],'_dispatch':{'failures':failures,'retry_at':(now+timedelta(seconds=delay)).isoformat(),'error_code':error.code}}))
                    logger.warning('Source schedule not dispatched code=%s source_id=%s',error.code,identifier)
                else:
                    uow.session.execute(update(source_schedules).where(source_schedules.c.source_id==identifier).values(
                        next_run_at=now+timedelta(seconds=row['payload']['interval_seconds']),
                        payload={k:v for k,v in row['payload'].items() if k!='_dispatch'}))
                uow.commit()
                if job is not None:results.append(job)
        return tuple(results)


def scan_job(ctx):
    from lzo.ingestion.sync import SyncService
    from lzo.ingestion.schemas import ReadRequest,ScanResult,ImportSelection,ImportPreviewCommand
    from lzo.ingestion.models import source_scans
    from lzo.ingestion.connectors.base import scope_digest
    with ctx.batch():
        ctx.uow.ontology_settings=ctx.uow.job_settings=ctx.runner.settings
        config=ConnectionService(ctx.uow).get(ctx.actor,UUID(ctx.payload['resource_id']))
        completed=None
        if (ctx.cursor or {}).get('state')=='complete':
            stored=ctx.uow.session.execute(select(source_scans.c.payload).where(
                source_scans.c.id==UUID(ctx.cursor['scan_id']),source_scans.c.job_id==ctx.job_id)).scalar_one()
            completed=ScanResult.model_validate(stored['result'])
        if (ctx.cursor or {}).get('preview_job_id'):return
    result=completed or SyncService(ctx).scan(ctx.actor,ReadRequest(source_id=config.source_id,mode='full',scope_digest=scope_digest(config)),ctx.job_id,ctx.lease_token)
    if result.state!='complete':raise ApiError('SOURCE_SCAN_INCOMPLETE',409,'Source snapshot did not complete')
    with ctx.batch():
        ctx.uow.ontology_settings=ctx.uow.job_settings=ctx.runner.settings
        source=SourceService(ctx.uow).read(ctx.actor,config.source_id,'source.manage')
        # Raw-only sources remain raw until an actual reviewed publication binds
        # their mapping. Scheduled execution uses the very same admission path.
        if source.mapping_version_id is None:return
        from lzo.ontology.releases import current_execution_bundle
        from lzo.ingestion.preview_jobs import enqueue_preview
        from lzo.worker.service import JobResultRef
        bundle=current_execution_bundle(ctx.uow,ctx.actor)
        if bundle.id!=ctx.bundle_id:raise Conflict('BUNDLE_NOT_ACTIVE','Scan configuration changed')
        child=enqueue_preview(ctx.uow,ctx.actor,ImportPreviewCommand(selection=ImportSelection(
            source_id=source.id,scan_id=result.id,mode='full'),bundle_id=bundle.id),
            auto_apply=ctx.payload.get('schedule_revision') is not None)
        ctx.set_result(JobResultRef(kind='ingestion.scan.preview',resource_id=child.id,api_path=f'/api/v1/jobs/{child.id}'))
        ctx.checkpoint({**ctx.cursor,'preview_job_id':str(child.id)})


register_handler('ingestion.scan',scan_job,action='source.manage')
from lzo.worker.service import register_result_kind
register_result_kind('ingestion.scan.preview','/api/v1/jobs/{resource_id}')
from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('source.schedule.changed','ingestion-v1')
