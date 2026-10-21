"""Processing completion follows exact published inference coverage."""
from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle


def test_import_waits_for_published_coverage_but_not_unrelated_later_dirtiness(configuration_case):
    from uuid import uuid4
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.schemas import UploadBinding,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.semantic.inference_jobs import schedules,coverage,terminals
    from lzo.semantic.proofs import current_metadata
    from lzo.semantic.facts import register_object
    from lzo.semantic.schemas import CreateObjectInput
    from lzo.semantic.projection import register_nonsemantic
    for event in ('role.fixture_installed','grant.fixture_installed','object.fixture_created'):
        register_nonsemantic(event,'test-inference-fixture-v1')
    s=configuration_case;ordinary_bundle(s)
    from lzo.semantic import inference
    from lzo.governance.approvals import ApprovalService
    from lzo.auth.permissions import permission_context
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        change=inference.preview_inference_profile(uow,s.actor('alice'))
        approval=ApprovalService(uow).submit(s.actor('alice'),change.kind,list(change.target_ids),
            change.change_digest,change.baseline,{'command':change.command});uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'),approval.id,'approve','Independent inference activation');uow.commit()
    with s.transaction() as uow,permission_context(uow):
        uow.ontology_settings=s.config
        bundle=inference.activate_inference_profile(uow,s.actor('alice'),approval.id);uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None and getattr(error,'code',None)!='INFERENCE_PROJECTION_PENDING':raise error
    runner=DiagnosticRunner(s.app,s.config)
    def drain():
        for _ in range(200):
            if not runner.run_once():return
        raise AssertionError('Workers did not drain')
    drain();raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        service=IngestionService(uow)
        report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
        job=service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    for _ in range(100):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if state=='succeeded':break
        assert runner.run_once()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        effect=IngestionService(uow).rows(s.actor('alice'),job.id).items[0].revision
    for _ in range(150):
        with s.transaction() as uow:
            compute=uow.session.execute(select(jobs).join(schedules,schedules.c.job_id==jobs.c.id).where(
                schedules.c.event_id==effect.event_id)).mappings().first()
        if compute and compute['status']=='succeeded':break
        if not runner.run_once():
            from lzo.semantic.models import inference_requests
            with s.transaction() as uow:
                reason=uow.session.execute(select(inference_requests.c.state,inference_requests.c.error_code).where(
                    inference_requests.c.event_id==effect.event_id)).first()
            raise AssertionError(f'Import inference did not schedule: {reason}')
    assert compute and compute['status']=='succeeded'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert uow.session.execute(select(coverage).where(coverage.c.event_id==effect.event_id)).first() is None
        view=IngestionService(uow).get(s.actor('alice'),job.id)
        assert view.projection_state=='synced' and not view.complete,'Compute completion is not inference publication'
    drain()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        published=uow.session.execute(select(coverage).where(coverage.c.event_id==effect.event_id)).mappings().one()
        terminal=uow.session.execute(select(terminals).where(terminals.c.run_id==published['run_id'])).mappings().one()
        assert terminal['state']=='published'
        assert IngestionService(uow).get(s.actor('alice'),job.id).complete
        register_object(uow,s.actor('alice'),CreateObjectInput(resource_id=uuid4(),class_iris=('urn:Pond',),
            display_name='Later independent object',owner_scope_id=s.scopes['farm_a'],related_scope_ids=(),state='active',
            data_nature='synthetic',assertions=()),bundle);uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert IngestionService(uow).get(s.actor('alice'),job.id).complete
        assert current_metadata(uow,s.actor('alice')) is None
