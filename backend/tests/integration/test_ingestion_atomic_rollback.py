"""A real PostgreSQL outcome-write failure rolls back the complete F05 row."""
import re
from sqlalchemy import create_engine,select,func,text
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle


def test_pg_failure_after_semantic_write_retries_without_half_row(configuration_case):
    from lzo.ingestion.models import source_uploads,source_evaluations,import_run_rows
    from lzo.ingestion.schemas import UploadBinding,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.semantic.models import objects,supports,assertions
    from lzo.governance.models import revisions
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.worker.service import JobService
    s=configuration_case;bundle=ordinary_bundle(s);raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        uow.job_settings=s.config.model_copy(update={'job_max_attempts':1})
        schema=uow.session.execute(text('SELECT current_schema()')).scalar_one()
        assert re.fullmatch(r'f03_[0-9a-f]{32}',schema),'Only the owned disposable schema may be fault-injected'
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        service=IngestionService(uow)
        report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
        job=service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    owner=create_engine(s.config.migration_database_url)
    def fault(enabled):
        with owner.begin() as conn:
            conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            if enabled:
                conn.execute(text("""CREATE FUNCTION i04_outcome_fault() RETURNS trigger LANGUAGE plpgsql AS $$
                    BEGIN RAISE EXCEPTION 'I04_OWNED_OUTCOME_FAILURE'; END $$"""))
                conn.execute(text('CREATE TRIGGER i04_outcome_fault AFTER INSERT ON ingestion_run_rows '
                    'FOR EACH ROW EXECUTE FUNCTION i04_outcome_fault()'))
            else:
                conn.execute(text('DROP TRIGGER i04_outcome_fault ON ingestion_run_rows'))
                conn.execute(text('DROP FUNCTION i04_outcome_fault()'))
    runner=Runner(s.app,s.config.model_copy(update={'job_max_attempts':1}))
    def until_terminal():
        for _ in range(180):
            with s.transaction() as uow:
                state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
            if state in ('failed','succeeded','paused'):return state
            assert runner.run_once()
        raise AssertionError('Import worker exceeded bounded attempts')
    fault(True)
    try:
        assert until_terminal()=='failed'
        with s.transaction() as uow:
            for table in (objects,supports,assertions,source_evaluations,import_run_rows):
                assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==0
            assert not uow.session.execute(select(revisions.c.event_id).where(revisions.c.event_type=='object.created')).first()
            assert not uow.session.execute(select(jobs.c.cursor).where(jobs.c.id==job.id)).scalar_one()
    finally:
        fault(False);owner.dispose()
    with s.transaction() as uow:
        service=JobService(uow,s.config);view=service.get(s.actor('alice'),job.id)
        service.set_state(s.actor('alice'),job.id,'queued',view.revision);uow.commit()
    assert until_terminal()=='succeeded'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        result=IngestionService(uow).rows(s.actor('alice'),job.id).items[0]
        assert result.state=='applied' and result.revision.revision==1
        for table in (objects,supports,assertions,source_evaluations,import_run_rows):
            assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==1
