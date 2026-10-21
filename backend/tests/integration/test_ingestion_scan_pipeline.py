"""Actual source databases enter the same governed instance pipeline."""
from uuid import UUID
import pytest
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,version
from test_ingestion_review_boundaries import publish


@pytest.fixture
def governed_database(configuration_case,source_servers):
    from conftest_ingestion import database_case
    from lzo.artifacts.store import ArtifactStore
    s=configuration_case
    s.http=s.client;s.http_settings=s.config
    s.store=ArtifactStore(s.config.file_root/'artifacts',max_upload_bytes=s.config.artifact_max_upload_bytes)
    yield from database_case.__wrapped__(s,source_servers)


@pytest.mark.parametrize('backend,risk',[('postgresql','low'),('mysql','low'),('postgresql','high')])
def test_scheduled_scan_admits_updates_and_authenticated_tombstone(governed_database,backend,risk):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import (AuthorityRule,FieldMap,ImportAdmissionPolicy,ScheduleCommand,
        ImportSelection,ImportPreviewCommand,ApplyImportCommand)
    from test_ingestion_identity_rulings import profile
    from lzo.ingestion.scheduler import ScheduleService,Scheduler
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import import_previews,import_runs,source_scans,source_records
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.semantic.models import supports,objects
    from lzo.governance.approvals import ApprovalService
    from datetime import timedelta
    c=governed_database(backend);s=c.s;s.config=c.settings
    c.execute('DELETE FROM records WHERE a=1')
    c.execute("UPDATE records SET value='1'")
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        s.i02_source=SourceService(uow).read(s.actor('alice'),c.source.id)
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),profile(c.source.id).model_copy(update={
            'identity_paths':('a','b'),'name_path':'value','source_timezone':'UTC',
            'fields':(FieldMap(source_path='value',predicate_iri='urn:area',datatype_iri='http://www.w3.org/2001/XMLSchema#decimal'),)}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            AuthorityRule(predicate_iri='urn:area',owner_policy='source_backed',owner_source_ids=(c.source.id,),
                comparable_time_path='updated_at',important=risk=='high',digest=''),)))
        ruling=RuleService(uow).create_version(uow,s.actor('alice'),version('ruling',import_admission=ImportAdmissionPolicy(
            effect_classes=('create','type','facts','replace','withdraw'),predicate_iris=('urn:area',),class_iris=('urn:Pond',),
            owner_scope_ids=(s.scopes['farm_a'],),max_resources=2,max_rows=2,max_withdrawals=2)))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,ruling.id)
        uow.commit()
    publish(s,bundle,authority)
    with s.transaction() as uow:
        source=SourceService(uow).read(s.actor('alice'),c.source.id)
        schedule=ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=source.id,
            expected_revision=source.revision,enabled=True,interval_seconds=60));uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None and getattr(error,'code',None)!='JOB_YIELD':raise error
    runner=DiagnosticRunner(s.app,s.config)
    def drain():
        for _ in range(180):
            if not runner.run_once():return
        raise AssertionError('Bounded real worker did not drain')
    def scheduled(when):
        with s.transaction() as uow:
            before=uow.session.execute(select(func.count()).select_from(import_runs)).scalar_one()
        assert len(Scheduler(s.app,s.config).tick(when))==1
        drain()
        if risk=='high':
            from lzo.ingestion.schemas import PreviewReport
            with s.transaction() as uow:
                uow.ontology_settings=s.config
                assert uow.session.execute(select(func.count()).select_from(import_runs)).scalar_one()==before
                reports=[IngestionService(uow).get_preview(s.actor('alice'),identifier)
                    for identifier in uow.session.execute(select(import_previews.c.id)).scalars()]
                report=next(value for value in reports if value.rows[0].source_key.canonical_key and
                    not uow.session.execute(select(import_runs.c.id).where(import_runs.c.preview_id==value.id)).first())
                assert ApprovalService(uow).get(s.actor('alice'),report.approval_id).risk=='high'
                ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Independent scheduled import review')
                IngestionService(uow).apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
                    input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
            drain()
    scheduled(schedule.next_run_at+timedelta(seconds=1))
    with s.transaction() as uow:
        runs=uow.session.execute(select(import_runs.c.id)).scalars().all()
        assert len(runs)==1,'Completed source scan must enqueue governed preview and ordinary application'
        uow.ontology_settings=s.config
        first=IngestionService(uow).get(s.actor('alice'),runs[0])
        assert first.applied_count==1
        rows=IngestionService(uow).rows(s.actor('alice'),runs[0]).items
        target=rows[0].entity_id
        first_scan=uow.session.execute(select(source_scans.c.id).where(source_scans.c.source_id==c.source.id)).scalar_one()
    c.execute("UPDATE records SET updated_at='2026-02-01 12:00:00',value='2'")
    scheduled(schedule.next_run_at+timedelta(seconds=62))
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(import_runs)).scalar_one()==2
        assert uow.session.execute(select(objects.c.revision).where(objects.c.id==target)).scalar_one()==2
        assert uow.session.execute(select(func.count()).select_from(supports).where(supports.c.active.is_(True))).scalar_one()==1
    # Manual scan selection uses the registered snapshot, with no upload origin.
    c.execute('DELETE FROM records')
    from lzo.worker.service import enqueue
    with s.transaction() as uow:
        source=SourceService(uow).read(s.actor('alice'),c.source.id)
        job=enqueue(uow,s.actor('alice'),'ingestion.scan',{'resource_id':str(source.id),
            'configuration_revision':source.connection_revision,'mapping_version_id':str(source.mapping_version_id)},bundle.id)
        uow.commit()
    drain()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        scan=uow.session.execute(select(source_scans.c.id).where(source_scans.c.job_id==job.id)).scalar_one()
        service=IngestionService(uow)
        report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
            source_id=source.id,scan_id=scan,mode='full'),bundle_id=bundle.id))
        assert report.ready_count==1 and report.rows[0].locator.startswith('missing/')
        tombstone=uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==scan)).scalar_one()
        assert tombstone['operation']=='delete' and tombstone['position']['cursor']['previous_scan']!=str(first_scan)
        assert ApprovalService(uow).get(s.actor('alice'),report.approval_id).risk==risk,'Withdrawn predicates retain their classification'
        if risk=='high':ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Independent important withdrawal')
        service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    drain()
    with s.transaction() as uow:
        assert uow.session.execute(select(objects.c.revision).where(objects.c.id==target)).scalar_one()==3
        assert uow.session.execute(select(func.count()).select_from(supports).where(supports.c.active.is_(True))).scalar_one()==0
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==3
