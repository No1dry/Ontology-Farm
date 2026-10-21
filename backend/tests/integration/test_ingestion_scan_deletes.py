"""Durable scan proof, source ordering and F05 scheduling against real servers."""
import pytest
from lzo.errors import Conflict
from lzo.ingestion.sync import SyncService
from lzo.ingestion.scheduler import Scheduler
from test_ingestion_rule_versions import configuration_case


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
@pytest.mark.parametrize("condition", ["page_failure", "partial_keys", "scope_changed", "snapshot_lost"])
def test_incomplete_or_different_scan_never_infers_deletes(scan_case, backend, condition):
    c=scan_case(backend)
    previous=c.run()
    c.execute("DELETE FROM records WHERE a=2")
    scan=c.run(condition)
    with pytest.raises(Conflict,match="SCAN_NOT_COMPARABLE"):
        c.deletions(scan,previous)
    c.assert_no_deletion_side_effect(previous)


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_complete_same_scope_scan_generates_only_missing_keys(scan_case, backend):
    c=scan_case(backend)
    previous=c.run()
    c.execute("DELETE FROM records WHERE a=2")
    current=c.run()
    deleted=c.deletions(current,previous)
    assert [x.key.canonical_key for x in deleted]==['[2,1]']
    assert all(x.operation=='delete' for x in deleted)
    assert c.deletions(current,previous)==deleted
    with pytest.raises(Conflict):c.deletions(c.run(mode='incremental'),current)


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_old_event_cannot_overwrite_new_source_revision(scan_case,backend):
    c=scan_case(backend)
    c.assert_source_ordering()


def test_scheduler_two_ticks_enqueue_once(scan_case):
    c=scan_case('postgresql')
    c.assert_scheduler_contention()


def test_production_poll_enqueues_due_schedule_and_survives_scheduler_failure(scan_case,caplog):
    from sqlalchemy import select,update,text,create_engine
    from datetime import datetime,timezone,timedelta
    from lzo.ingestion.scheduler import ScheduleService
    from lzo.ingestion.schemas import ScheduleCommand
    from lzo.ingestion.models import source_schedules,source_scans
    from lzo.worker.runner import Runner,poll_once
    from lzo.worker.service import register_handler,enqueue
    from lzo.worker.models import jobs
    c=scan_case('postgresql');s=c.s
    with s.transaction() as uow:
        ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60))
        uow.session.execute(update(source_schedules).values(next_run_at=datetime.now(timezone.utc)-timedelta(seconds=1)));uow.commit()
    assert poll_once(Runner(s.app,c.settings))
    with s.transaction() as uow:
        assert uow.session.execute(select(source_scans.c.id)).first()
        assert uow.session.execute(select(jobs.c.status)).scalar_one()=='succeeded'
        schema=uow.session.execute(text('SELECT current_schema()')).scalar_one()
    calls=[];register_handler('test.i03.unrelated',lambda ctx:calls.append(ctx.job_id),action='source.manage')
    with s.transaction() as uow:
        job=enqueue(uow,s.actor('alice'),'test.i03.unrelated',{'resource_id':str(c.source.id)},None);uow.commit()
    owner=create_engine(c.settings.migration_database_url)
    try:
        with owner.begin() as conn:conn.execute(text(f'REVOKE SELECT ON "{schema}".source_schedules FROM lzo_app'))
        assert poll_once(Runner(s.app,c.settings))
    finally:
        with owner.begin() as conn:conn.execute(text(f'GRANT SELECT ON "{schema}".source_schedules TO lzo_app'))
        owner.dispose()
    assert calls==[job.id]
    assert 'Source scheduler tick failed' in caplog.text


def test_revoked_schedule_initiator_cannot_enqueue(scan_case):
    from datetime import timedelta
    from sqlalchemy import select
    from lzo.ingestion.scheduler import ScheduleService
    from lzo.ingestion.schemas import ScheduleCommand
    from lzo.worker.models import jobs
    c=scan_case('postgresql');s=c.s
    with s.transaction() as uow:
        schedule=ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60));uow.commit()
    s.revoke(s.source_grant)
    assert Scheduler(s.app,c.settings).tick(schedule.next_run_at+timedelta(seconds=1))==()
    with s.transaction() as uow:assert uow.session.execute(select(jobs.c.id)).first() is None


def test_disabled_queued_schedule_cannot_scan(scan_case):
    from datetime import timedelta
    from sqlalchemy import select
    from lzo.ingestion.scheduler import ScheduleService
    from lzo.ingestion.schemas import ScheduleCommand
    from lzo.ingestion.models import source_scans
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    c=scan_case('postgresql');s=c.s
    with s.transaction() as uow:
        schedule=ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60));uow.commit()
    queued=Scheduler(s.app,c.settings).tick(schedule.next_run_at+timedelta(seconds=1))
    assert len(queued)==1
    with s.transaction() as uow:
        ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=c.source.id,expected_revision=4,enabled=False,interval_seconds=60));uow.commit()
    assert Runner(s.app,c.settings).run_once()
    with s.transaction() as uow:
        assert uow.session.execute(select(source_scans.c.id)).first() is None
        # F05 deliberately sanitizes handler exception codes at job completion.
        assert uow.session.execute(select(jobs.c.error_code).where(jobs.c.id==queued[0].id)).scalar_one()=='HANDLER_FAILED'


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_real_database_late_event_preserves_materiality_and_refreshes_current(backend,configuration_case,source_servers):
    # Reuse the actual I02 governance/F06/S03 fixture and real I03 source factory
    # in the SAME isolated platform schema. No fabricated SourceChange is admitted.
    from conftest_ingestion import database_case as database_fixture,scan_case as scan_fixture
    from lzo.artifacts.store import ArtifactStore
    from lzo.ingestion.schemas import MappingProfile,FieldMap,AuthorityRule
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.rulings import RulingService
    from lzo.ingestion.models import source_records
    from lzo.ingestion.schemas import SourceChange
    from sqlalchemy import select
    from test_ingestion_rule_versions import version
    from test_ingestion_review_boundaries import publish
    from test_ingestion_support_currentness import adopt
    from test_ingestion_round_justification import refresh_after_review
    s=configuration_case;s.http=s.client;s.http_settings=s.config
    s.store=ArtifactStore(s.config.file_root/'artifacts',max_upload_bytes=s.config.artifact_max_upload_bytes)
    generator=database_fixture.__wrapped__(s,source_servers);factory=next(generator)
    try:
        c=scan_fixture.__wrapped__(factory)(backend);s.config=c.settings
        c.execute("UPDATE records SET value='2.5',updated_at='2026-03-01 12:00:00'")
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),MappingProfile(source_id=c.source.id,class_iri='urn:Pond',
                identity_paths=('a','b'),confirmed_identity_rule_id=None,name_path='value',fields=(FieldMap(source_path='value',predicate_iri='urn:area',datatype_iri='http://www.w3.org/2001/XMLSchema#decimal'),),geometry_path=None,source_crs=None,source_timezone='UTC'))
            authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(AuthorityRule(predicate_iri='urn:area',owner_policy='source_backed',owner_source_ids=(c.source.id,),source_priority=(c.source.id,),digest=''),)))
            bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
        s.i02_source=c.source;publish(s,bundle,authority)
        first=c.run()
        with s.transaction() as uow:change=SourceChange.model_validate(uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==first.id)).scalars().first())
        identity,mapped,ruling=adopt(s,change,mapping,bundle)
        c.execute("UPDATE records SET value='1.5',updated_at='2026-02-01 12:00:00'");late=c.run()
        assert late.last_success_watermark==first.last_success_watermark
        with s.transaction() as uow:
            assert RulingService(uow).currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        c.execute("UPDATE records SET value='4.5',updated_at='2026-04-01 12:00:00'")
        incomplete=c.run('page_failure')
        assert incomplete.state=='partial'
        with s.transaction() as uow:
            from lzo.semantic import facts
            assert RulingService(uow).currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
            assert facts.current_assertions(uow,identity.resource_id)
        c.run()
        with s.transaction() as uow:assert not RulingService(uow).currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        c.execute("UPDATE records SET value='3.5',updated_at='2026-03-15 12:00:00'");c.run()
        refreshed=refresh_after_review(s,ruling)
        assert any(v.normalized_value and v.normalized_value.value=='4.5' for v in refreshed.candidate_values)
        assert all(not v.normalized_value or v.normalized_value.value!='3.5' for v in refreshed.candidate_values)
    finally:
        try:next(generator)
        except StopIteration:pass


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_overlapping_scan_cannot_reorder_snapshot_generation(scan_case,backend):
    from lzo.worker.runner import Runner
    from lzo.worker.service import enqueue,register_handler
    from lzo.ingestion.sources import SourceService
    c=scan_case(backend);s=c.s;attempts=[]
    def compete():
        register_handler('test.i03.competing',lambda ctx:None,action='source.manage')
        with s.transaction() as uow:
            source=SourceService(uow).read(s.actor('alice'),c.source.id)
            enqueue(uow,s.actor('alice'),'test.i03.competing',{'resource_id':str(source.id),'configuration_revision':source.connection_revision,'mapping_version_id':None},None);uow.commit()
        runner=Runner(s.app,c.settings);ctx=runner.claim()
        assert ctx is not None
        with pytest.raises(Conflict) as error:SyncService(ctx).scan(ctx.actor,c.request(),ctx.job_id,ctx.lease_token)
        assert error.value.code=='SOURCE_SCAN_BUSY'
        attempts.append(error.value.code);runner.finish(ctx,error.value)
    c.after_first_page=compete
    result=c.run()
    assert result.state=='complete' and attempts==['SOURCE_SCAN_BUSY']


def test_scan_budget_stops_partial_and_can_retry_with_larger_budget(scan_case):
    c=scan_case('postgresql')
    original=c.settings;c.settings=original.model_copy(update={'job_max_batches_per_run':6})
    partial=c.run()
    assert partial.state=='partial' and partial.errors[0].code=='SOURCE_SCAN_BUDGET_EXCEEDED'
    c.settings=original
    assert c.run().state=='complete'


def test_abandoned_lease_restarts_with_a_new_snapshot(scan_case):
    from sqlalchemy import select,update,func,text
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.ingestion.models import source_scans
    c=scan_case('postgresql')
    class AbandonedAttempt(BaseException):pass
    def abandon():raise AbandonedAttempt()
    c.after_first_page=abandon
    with pytest.raises(AbandonedAttempt):c.run()
    c.after_first_page=None
    with c.s.transaction() as uow:
        first=uow.session.execute(select(source_scans.c.id)).scalar_one()
        uow.session.execute(update(jobs).values(lease_expires_at=func.clock_timestamp()-text("interval '1 second'")));uow.commit()
    assert Runner(c.s.app,c.settings).run_once()
    with c.s.transaction() as uow:
        rows=uow.session.execute(select(source_scans)).mappings().all()
        assert len(rows)==2
        assert next(r for r in rows if r['id']!=first)['payload']['result']['state']=='complete'


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_changed_ordering_configuration_requires_full_reconciliation(scan_case,backend):
    from sqlalchemy import select
    from lzo.ingestion.models import source_records
    from lzo.ingestion.sources import ConnectionService,current_source_change
    from lzo.ingestion.schemas import SourceChange
    c=scan_case(backend);s=c.s;old=c.run()
    with s.transaction() as uow:original=SourceChange.model_validate(uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==old.id)).scalars().first())
    datatype='TIMESTAMPTZ' if backend=='postgresql' else 'DATETIME'
    c.execute(f"ALTER TABLE records ADD other_time {datatype} NOT NULL DEFAULT '2020-01-01 12:00:00'")
    c.execute("UPDATE records SET value='new configuration'")
    with s.transaction() as uow:
        c.config=ConnectionService(uow).configure(uow,s.actor('alice'),c.config.model_copy(update={'updated_at_column':'other_time'}));uow.commit()
    reconciled=c.run()
    with s.transaction() as uow:assert current_source_change(uow,original).raw_fields['value']=='new configuration'
    with pytest.raises(Conflict):c.deletions(reconciled,old)


def test_partial_scan_history_matches_durable_failed_result(scan_case):
    from sqlalchemy import select
    from lzo.governance.models import revisions
    c=scan_case('postgresql');partial=c.run('page_failure')
    assert partial.state=='partial'
    with c.s.transaction() as uow:
        last=uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id==partial.id).order_by(revisions.c.revision.desc()).limit(1)).scalar_one()
        assert last['result']['state']=='partial'


@pytest.mark.parametrize('poll_delay',[0,10])
def test_failing_earliest_schedules_allow_later_healthy_source(scan_case,poll_delay):
    from datetime import datetime,timezone,timedelta
    from sqlalchemy import select,update
    from lzo.ingestion.scheduler import ScheduleService
    from lzo.ingestion.schemas import ScheduleCommand
    from lzo.ingestion.models import source_schedules
    from lzo.worker.models import jobs
    cases=[scan_case('postgresql') for _ in range(3)];s=cases[0].s
    s.grant('admin','source_manager',scope='farm_a')
    now=datetime.now(timezone.utc)
    with s.transaction() as uow:
        for index,c in enumerate(cases):
            ScheduleService(uow).configure(uow,s.actor('alice' if index<2 else 'admin'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60))
            uow.session.execute(update(source_schedules).where(source_schedules.c.source_id==c.source.id).values(next_run_at=now-timedelta(seconds=30-index)))
        uow.commit()
    s.revoke(s.source_grant)
    settings=cases[0].settings.model_copy(update={'job_claim_scan_limit':2,'job_retry_base_seconds':1,'job_retry_cap_seconds':2})
    assert Scheduler(s.app,settings).tick(now)==()
    # Fresh processes and a poll after retry expiry must both make progress.
    later=now+timedelta(seconds=poll_delay)
    dispatched=Scheduler(s.app,settings).tick(later)
    assert len(dispatched)==1
    assert Scheduler(s.app,settings).tick(later)==()
    with s.transaction() as uow:
        job=uow.session.execute(select(jobs)).mappings().one()
        assert job['initiator_id']==s.users['admin']
        assert job['payload']['resource_id']==str(cases[2].source.id)
        assert job['payload']['schedule_revision']==4
        assert job['payload']['configuration_revision']==3
        failed=uow.session.execute(select(source_schedules).where(source_schedules.c.source_id.in_([c.source.id for c in cases[:2]]))).mappings().all()
        assert all(row['initiator_id']==s.users['alice'] and row['revision']==4 and row['next_run_at']<now for row in failed)
        assert all(row['payload']['_dispatch']['failures']==(2 if poll_delay else 1) and row['payload']['_dispatch']['error_code']=='FORBIDDEN' for row in failed)
        retry_at=max(datetime.fromisoformat(row['payload']['_dispatch']['retry_at']) for row in failed)
        due={str(row['source_id']):row['next_run_at'].isoformat() for row in failed}
    s.grant('alice','source_manager',scope='farm_a')
    assert len(Scheduler(s.app,settings).tick(retry_at))==2
    assert Scheduler(s.app,settings).tick(retry_at)==()
    with s.transaction() as uow:
        restored=uow.session.execute(select(jobs).where(jobs.c.initiator_id==s.users['alice'])).mappings().all()
        assert len(restored)==2 and all(row['payload']['due_at']==due[row['payload']['resource_id']] for row in restored)
        assert all(row['payload']['schedule_revision']==4 for row in restored)


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_final_batch_rollback_reports_only_committed_scan_progress(scan_case,backend):
    from sqlalchemy import select,func
    from lzo.ingestion.models import source_scans,source_watermarks,source_positions,scan_members,source_records
    from lzo.governance.models import revisions
    c=scan_case(backend);s=c.s;previous=c.run()
    with s.transaction() as uow:
        positions=list(uow.session.execute(select(source_positions).order_by(source_positions.c.canonical_key)).mappings())
    c.execute("UPDATE records SET updated_at='2026-05-01 12:00:00',value='tentative'")
    c.settings=c.settings.model_copy(update={'job_batch_timeout_seconds':2})
    partial=c.run('final_batch_timeout')
    assert partial.state=='partial' and partial.errors[0].code=='JOB_BATCH_TIMEOUT'
    assert c.tentative_final['members']==3
    assert c.tentative_final['watermark']!=previous.last_success_watermark
    with s.transaction() as uow:
        watermark=uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==c.source.id)).scalar_one()
        count=uow.session.execute(select(func.count()).select_from(scan_members).where(scan_members.c.scan_id==partial.id)).scalar_one()
        history=uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id==partial.id).order_by(revisions.c.revision)).scalars().all()
        durable=uow.session.execute(select(source_scans.c.payload).where(source_scans.c.id==partial.id)).scalar_one()['result']
        assert count==2
        assert partial.read_count==count
        assert partial.last_success_watermark==watermark['watermark']==previous.last_success_watermark
        assert watermark['last_complete_scan']==str(previous.id) and watermark['owner_job_id'] is None
        assert partial.cursor==history[-2]['result']['cursor']
        assert partial.model_dump(mode='json')==durable==history[-1]['result']
        assert list(uow.session.execute(select(source_positions).order_by(source_positions.c.canonical_key)).mappings())==positions
        assert uow.session.execute(select(func.count()).select_from(source_records).where(source_records.c.scan_id==partial.id)).scalar_one()==count


def test_recovered_retry_competes_fairly_with_recurring_due_windows(scan_case):
    from datetime import datetime,timezone,timedelta
    from sqlalchemy import select,update
    from lzo.ingestion.scheduler import ScheduleService
    from lzo.ingestion.schemas import ScheduleCommand
    from lzo.ingestion.models import source_schedules
    from lzo.worker.models import jobs
    recovered,healthy=[scan_case('postgresql') for _ in range(2)];s=recovered.s
    s.grant('admin','source_manager',scope='farm_a')
    now=datetime.now(timezone.utc);original_due=now-timedelta(seconds=30)
    with s.transaction() as uow:
        for index,c in enumerate((recovered,healthy)):
            ScheduleService(uow).configure(uow,s.actor('alice' if index==0 else 'admin'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60))
            uow.session.execute(update(source_schedules).where(source_schedules.c.source_id==c.source.id).values(next_run_at=original_due+timedelta(seconds=index)))
        uow.commit()
    settings=recovered.settings.model_copy(update={'job_claim_scan_limit':1,'job_retry_base_seconds':1,'job_retry_cap_seconds':2})
    s.revoke(s.source_grant)
    assert Scheduler(s.app,settings).tick(now)==()
    with s.transaction() as uow:
        failed=uow.session.execute(select(source_schedules).where(source_schedules.c.source_id==recovered.source.id)).mappings().one()
        assert failed['next_run_at']==original_due
        assert failed['payload']['_dispatch']['error_code']=='FORBIDDEN'
        assert datetime.fromisoformat(failed['payload']['_dispatch']['retry_at'])<now+timedelta(seconds=61)
    s.grant('alice','source_manager',scope='farm_a')
    dispatched=[]
    # Fresh schedulers poll slowly enough that healthy recurring work is always
    # eligible again. An expired recovered retry must still get bounded turns.
    for tick in range(1,7):
        current=Scheduler(s.app,settings).tick(now+timedelta(seconds=61*tick))
        assert len(current)==1
        dispatched.extend(job.id for job in current)
    with s.transaction() as uow:
        rows=uow.session.execute(select(jobs)).mappings().all()
        a=[r for r in rows if r['payload']['resource_id']==str(recovered.source.id)]
        b=[r for r in rows if r['payload']['resource_id']==str(healthy.source.id)]
        assert a and b
        assert len(a)>=2 and len(b)>=2
        original=[r for r in a if datetime.fromisoformat(r['payload']['due_at'])==original_due]
        assert len(original)==1
        assert all(r['initiator_id']==s.users['alice'] for r in a)
        assert all(r['initiator_id']==s.users['admin'] for r in b)
        assert all(r['payload']['schedule_revision']==4 and r['payload']['configuration_revision']==3 for r in rows)
        windows=[(r['payload']['resource_id'],r['payload']['due_at']) for r in rows]
        assert len(windows)==len(set(windows))==len(set(dispatched))==6
