"""Authenticated raw scans and persisted current selection. No semantic adoption."""
from datetime import datetime,timezone
from uuid import UUID,uuid4,uuid5
from sqlalchemy import select,insert,update,func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from lzo.contracts import ResourceScope,ActorContext
from lzo.errors import ApiError,Conflict
from lzo.governance.revisions import append_revision
from lzo.governance.models import resource_heads
from lzo.ingestion.models import source_scans,source_records,scan_members,source_positions,source_watermarks
from lzo.ingestion.schemas import SourceChange,SourcePosition,ScanResult,ParseIssue,ReadRequest
from lzo.ingestion.sources import SourceService,ConnectionService,add_scoped,digest
from lzo.ingestion.connectors.base import scope_digest
from lzo.ingestion.credentials import SourceCredentialStore
from lzo.artifacts.store import ArtifactStore
from lzo.artifacts.service import ArtifactService
from lzo.ontology.drafts import bind_dependencies
from lzo.worker.service import checkpoint
from lzo.worker.leases import fenced_row


def upsert(uow,table,keys,values):
    stmt=pg_insert(table).values(**keys,**values)
    uow.session.execute(stmt.on_conflict_do_update(index_elements=list(keys),set_=values))


def connector_for(source,credentials,store,settings):
    from lzo.ingestion.connectors.postgresql import PostgreSQLConnector
    from lzo.ingestion.connectors.mysql import MySQLConnector
    cls={'postgresql':PostgreSQLConnector,'mysql':MySQLConnector}[source.configuration.kind]
    return cls(credentials,source,store,ca_file=settings.source_ca_bundle,timeout_seconds=settings.source_scan_timeout_seconds)


class TaskCredentials:
    def __init__(self,ctx,source_id,revision):self.ctx,self.source_id,self.revision=ctx,source_id,revision

    def _authorize(self):
        source=SourceService(self.ctx.uow).read(self.ctx.actor,self.source_id,'source.manage')
        if source.connection_revision!=self.revision:raise Conflict('SOURCE_CONFIGURATION_CHANGED','Source configuration changed')
        if self.ctx.payload.get('schedule_revision') is not None and self.ctx.payload['schedule_revision']!=source.schedule_revision:
            raise Conflict('SOURCE_CONFIGURATION_CHANGED','Queued schedule changed')
        return self.source_id

    def authorize(self):
        with self.ctx.batch():return self._authorize()

    def resolve(self,ref):
        with self.ctx.batch():
            return SourceCredentialStore(self.ctx.runner.settings,self.ctx.uow,authorize=self._authorize).resolve(ref)


class SyncService:
    def __init__(self,ctx=None,*,uow=None,connector_factory=connector_for):
        self.ctx,self.uow,self.connector_factory=ctx,uow,connector_factory

    def scan(self,actor:ActorContext,request:ReadRequest,job_id:UUID,lease_token:UUID)->ScanResult:
        ctx=self.ctx
        if ctx is None or ctx.job_id!=job_id or ctx.lease_token!=lease_token or ctx.actor.subject_id!=actor.subject_id:
            raise Conflict('JOB_BATCH_REQUIRED','Actual fenced job context required')
        identifier=uuid4();connector=None
        with ctx.batch():
            source=SourceService(ctx.uow).read(ctx.actor,request.source_id,'source.manage')
            if ctx.payload.get('schedule_revision') is not None and ctx.payload['schedule_revision']!=source.schedule_revision:
                raise Conflict('SOURCE_CONFIGURATION_CHANGED','Queued schedule changed')
            config=ConnectionService(ctx.uow).get(ctx.actor,source.id)
            if request.scope_digest!=scope_digest(config):raise Conflict('SOURCE_SCOPE_CHANGED','Source scope changed')
            if ctx.payload.get('configuration_revision')!=source.connection_revision:raise Conflict('SOURCE_CONFIGURATION_CHANGED','Queued configuration changed')
            if ctx.payload.get('mapping_version_id')!=(str(source.mapping_version_id) if source.mapping_version_id else None):
                raise Conflict('SOURCE_CONFIGURATION_CHANGED','Queued mapping changed')
            old=ctx.uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==source.id)).scalar_one_or_none() or {}
            if old.get('owner_job_id'):
                from lzo.worker.models import jobs
                owner=ctx.uow.session.execute(select(jobs).where(jobs.c.id==UUID(old['owner_job_id']))).mappings().first()
                now=ctx.uow.session.execute(select(func.clock_timestamp())).scalar_one()
                if owner and owner['status']=='running' and owner['lease_expires_at']>now and (
                    not old.get('owner_lease') or str(owner['lease_token'])==old['owner_lease']):
                    raise Conflict('SOURCE_SCAN_BUSY','Source snapshot already owned')
            generation=old.get('generation',0)+1
            state={**old,'generation':generation,'owner_job_id':str(job_id),'owner_lease':str(lease_token),'owner_scan_id':str(identifier)}
            upsert(ctx.uow,source_watermarks,{'source_id':source.id},{'payload':state})
            scope=source.configuration.scope
            add_scoped(ctx.uow,ctx.actor,identifier,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),'source.scan','source.manage')
            result=ScanResult(id=identifier,source_id=source.id,scope_digest=request.scope_digest,snapshot_token='',state='running',
                read_count=0,cursor=None,last_success_watermark=old.get('watermark'),errors=())
            meta={'result':result.model_dump(mode='json'),'mode':request.mode,'generation':generation,
                'connection_revision':source.connection_revision,'mapping_version_id':str(source.mapping_version_id) if source.mapping_version_id else None,
                'started_at':datetime.now(timezone.utc).isoformat()}
            ctx.uow.session.execute(insert(source_scans).values(id=identifier,source_id=source.id,job_id=job_id,payload=meta))
            event=append_revision(ctx.uow,ctx.actor,identifier,0,meta,'source.scan.started')
            bind_dependencies(ctx.uow,identifier,{source.id},event.event_id)
            checkpoint(job_id,lease_token,{'scan_id':str(identifier),'state':'running'})
        store=ArtifactStore(ctx.runner.settings.file_root/'artifacts',max_upload_bytes=ctx.runner.settings.artifact_max_upload_bytes)
        credentials=TaskCredentials(ctx,source.id,source.connection_revision)
        connector=self.connector_factory(source,credentials,store,ctx.runner.settings)
        try:
            current=request
            while True:
                # Reserve a batch to record partial failure and release ownership;
                # budget exhaustion must not strand a resumeless scan forever.
                if ctx._batches>=ctx.runner.settings.job_max_batches_per_run-2:
                    raise ApiError('SOURCE_SCAN_BUDGET_EXCEEDED',409,'Increase source page size or worker batch budget')
                ctx.heartbeat()
                page=connector.read_page(config,current)
                with ctx.batch():
                    credentials._authorize()
                    live=SourceService(ctx.uow).read(ctx.actor,source.id)
                    state=ctx.uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==source.id)).scalar_one()
                    if state.get('owner_scan_id')!=str(identifier) or live.mapping_version_id!=source.mapping_version_id:
                        raise Conflict('SOURCE_CONFIGURATION_CHANGED','Scan ownership or mapping changed')
                    if result.snapshot_token and result.snapshot_token!=page.source_snapshot_token:raise Conflict('SNAPSHOT_LOST','Snapshot changed')
                    keys=tuple(c.key.canonical_key for c in page.changes)
                    if page.seen_keys!=keys or len(set(keys))!=len(keys):raise Conflict('SCAN_KEYS_INCOMPLETE','Page key membership is incomplete')
                    registered=set()
                    for change in page.changes:
                        if change.key.source_id!=source.id or str(change.position.epoch)!=page.source_snapshot_token:raise Conflict('SCAN_BINDING_INVALID','Source event differs from snapshot')
                        if ctx.uow.session.execute(select(scan_members).where(scan_members.c.scan_id==identifier,scan_members.c.canonical_key==change.key.canonical_key)).first():
                            raise Conflict('SCAN_KEYS_INCOMPLETE','Duplicate scan key')
                        if change.snapshot.version_id not in registered:
                            ArtifactService(ctx.uow,store,ctx.runner.settings)._register_upload(ctx.actor,identifier,change.snapshot,
                                filename='source-page.json',metadata={'scan_id':str(identifier),'source_id':str(source.id)},action='source.manage')
                            registered.add(change.snapshot.version_id)
                        self._record(ctx.uow,ctx.actor,identifier,change,source)
                        ctx.uow.session.execute(insert(scan_members).values(scan_id=identifier,canonical_key=change.key.canonical_key,change_id=change.change_id))
                    tentative=result.model_copy(update={'snapshot_token':page.source_snapshot_token,'read_count':result.read_count+len(keys),
                        'cursor':page.next_cursor,'state':'complete' if page.end_of_scan else 'running'})
                    if page.end_of_scan:
                        records=ctx.uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==identifier)).scalars().all()
                        for record in records:self._select_current(ctx.uow,SourceChange.model_validate(record),generation,request.mode)
                        water=page.watermark
                        previous=state.get('watermark')
                        if previous and water and previous.get('scope_digest')==water.get('scope_digest') and previous['updated_at']>water['updated_at']:water=previous
                        state.update(owner_job_id=None,owner_scan_id=None,watermark=water,at=datetime.now(timezone.utc).isoformat(),last_complete_scan=str(identifier))
                        upsert(ctx.uow,source_watermarks,{'source_id':source.id},{'payload':state})
                        tentative=tentative.model_copy(update={'last_success_watermark':water})
                    meta['result']=tentative.model_dump(mode='json')
                    ctx.uow.session.execute(update(source_scans).where(source_scans.c.id==identifier).values(payload=meta))
                    revision=ctx.uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==identifier)).scalar_one()
                    append_revision(ctx.uow,ctx.actor,identifier,revision,meta,'source.scan.progress')
                    checkpoint(job_id,lease_token,{'scan_id':str(identifier),'state':tentative.state,'cursor':tentative.cursor,'read_count':tentative.read_count})
                # F05 fences, checks elapsed time and commits on context exit.
                # Until that succeeds, failure reporting must use committed state.
                result=tentative
                if page.end_of_scan:return result
                current=request.model_copy(update={'cursor':page.next_cursor})
        except ApiError as error:
            # Keep previously committed raw pages, never advance successful state.
            result=result.model_copy(update={'state':'partial' if result.read_count else 'failed',
                'errors':(ParseIssue(locator='scan',field_path=None,code=error.code,message='Source scan did not complete'),)})
            with ctx.batch():
                meta['result']=result.model_dump(mode='json')
                ctx.uow.session.execute(update(source_scans).where(source_scans.c.id==identifier).values(payload=meta))
                state=ctx.uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==source.id)).scalar_one()
                if state.get('owner_scan_id')==str(identifier):
                    state.update(owner_job_id=None,owner_scan_id=None)
                    upsert(ctx.uow,source_watermarks,{'source_id':source.id},{'payload':state})
                revision=ctx.uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id==identifier)).scalar_one()
                append_revision(ctx.uow,ctx.actor,identifier,revision,meta,'source.scan.progress')
                checkpoint(job_id,lease_token,{'scan_id':str(identifier),'state':result.state})
            return result
        finally:connector.close()

    def _record(self,uow,actor,scan_id,change,source):
        scope=source.configuration.scope
        add_scoped(uow,actor,change.change_id,ResourceScope(None,scope.owner_scope_id,scope.related_scope_ids,scope.security_class),'source.record','source.manage')
        uow.session.execute(insert(source_records).values(id=change.change_id,scan_id=scan_id,ordinal=change.position.ordinal,payload=change.model_dump(mode='json')))
        event=append_revision(uow,actor,change.change_id,0,change.model_dump(mode='json'),'source.record.registered')
        bind_dependencies(uow,change.change_id,{source.id,scan_id,change.snapshot.version_id},event.event_id)

    def _select_current(self,uow,change,generation,mode):
        keys={'source_id':change.key.source_id,'canonical_key':change.key.canonical_key}
        old=uow.session.execute(select(source_positions).where(source_positions.c.source_id==change.key.source_id,
            source_positions.c.canonical_key==change.key.canonical_key)).mappings().first()
        value={'generation':generation,'version':change.position.source_version,'content':change.content_sha256,'conflict':False,
            'operation':change.operation,'scope_digest':change.position.cursor['scope_digest']}
        if old:
            prior=old['payload'];version=change.position.source_version
            if prior.get('generation',0)>generation:return
            if prior.get('scope_digest')!=value['scope_digest']:
                if mode=='full':upsert(uow,source_positions,keys,{'change_id':change.change_id,'payload':value})
                else:upsert(uow,source_positions,keys,{'change_id':old['change_id'],'payload':{**prior,'conflict':True}})
                return
            if prior.get('delete_fence') and mode!='full' and (version is None or version<=prior['delete_fence']):return
            if version and prior.get('version'):
                from lzo.ingestion.datetimes import source_datetime
                if source_datetime(version)<source_datetime(prior['version']):return
                if source_datetime(version)==source_datetime(prior['version']):
                    if change.content_sha256==prior['content']:return
                    upsert(uow,source_positions,keys,{'change_id':old['change_id'],'payload':{**prior,'conflict':True}});return
            elif mode!='full':
                upsert(uow,source_positions,keys,{'change_id':old['change_id'],'payload':{**prior,'conflict':True}});return
        upsert(uow,source_positions,keys,{'change_id':change.change_id,'payload':value})

    def deletions(self,actor:ActorContext,scan_id:UUID,previous_scan_id:UUID)->tuple[SourceChange,...]:
        uow=self.uow
        if uow is None:raise Conflict('UNIT_OF_WORK_REQUIRED','Deletion reconciliation requires a caller transaction')
        from lzo.governance.revisions import lock_ledger
        from lzo.ontology.drafts import authorize
        for identifier in (scan_id,previous_scan_id):authorize(uow,actor,identifier)
        lock_ledger(uow)
        rows=[uow.session.execute(select(source_scans).where(source_scans.c.id==i)).mappings().one() for i in (scan_id,previous_scan_id)]
        a,b=[r['payload'] for r in rows];current,previous=[ScanResult.model_validate(r['result']) for r in (a,b)]
        source=SourceService(uow).read(actor,current.source_id,'source.manage')
        water=uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==source.id)).scalar_one()
        comparable=(current.source_id==previous.source_id and current.state==previous.state=='complete' and a['mode']==b['mode']=='full'
            and current.scope_digest==previous.scope_digest and a['connection_revision']==b['connection_revision']==source.connection_revision
            and a['mapping_version_id']==b['mapping_version_id']==(str(source.mapping_version_id) if source.mapping_version_id else None)
            and a['generation']>b['generation'] and water.get('last_complete_scan')==str(scan_id) and current.snapshot_token and previous.snapshot_token)
        if not comparable:raise Conflict('SCAN_NOT_COMPARABLE','SCAN_NOT_COMPARABLE: complete same-scope snapshots required')
        old=uow.session.execute(select(source_records.c.payload).join(scan_members,scan_members.c.change_id==source_records.c.id).where(
            scan_members.c.scan_id==previous_scan_id,scan_members.c.canonical_key.not_in(select(scan_members.c.canonical_key).where(scan_members.c.scan_id==scan_id)))).scalars().all()
        results=[]
        ordinal=uow.session.execute(select(func.max(source_records.c.ordinal)).where(source_records.c.scan_id==scan_id)).scalar_one() or 0
        for raw in old:
            original=SourceChange.model_validate(raw);identifier=uuid5(scan_id,'missing:'+original.key.canonical_key)
            existing=uow.session.execute(select(source_records.c.payload).where(source_records.c.id==identifier)).scalar_one_or_none()
            if existing:results.append(SourceChange.model_validate(existing));continue
            ordinal+=1
            change=original.model_copy(update={'change_id':identifier,'operation':'delete','raw_fields':{},
                'content_sha256':digest({'missing_key':original.key.canonical_key,'scan_id':str(scan_id)}),
                'idempotency_key':str(identifier),'received_at':datetime.fromisoformat(a['started_at']),
                'position':SourcePosition(stream_id=str(source.id),epoch=UUID(current.snapshot_token),ordinal=ordinal,source_version=None,
                    cursor={'scope_digest':current.scope_digest,'missing_from_scan':str(scan_id),'previous_scan':str(previous_scan_id)}),
                'locator':'missing/'+original.key.canonical_key})
            self._record(uow,actor,scan_id,change,source)
            upsert(uow,source_positions,{'source_id':source.id,'canonical_key':original.key.canonical_key},
                {'change_id':identifier,'payload':{'generation':a['generation'],'version':None,'content':change.content_sha256,'conflict':False,
                    'operation':'delete','scope_digest':current.scope_digest,'delete_fence':(current.last_success_watermark or {}).get('updated_at')}})
            results.append(change)
        return tuple(results)


from lzo.semantic.projection import register_nonsemantic
register_nonsemantic('source.scan.started','ingestion-v1')
register_nonsemantic('source.scan.progress','ingestion-v1')
