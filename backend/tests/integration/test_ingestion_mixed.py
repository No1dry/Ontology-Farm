"""Excluded rows stay separate from the single accepted row and its outbox."""
from uuid import uuid4
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case
from test_ingestion_pipeline import ordinary_bundle


def test_mixed_ready_identity_conflict_invalid_value_never_adopts_excluded_rows(configuration_case):
    import io,json
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.schemas import FileOptions,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import source_records,source_evaluations,import_run_rows
    from lzo.semantic.models import objects
    from lzo.governance.approvals import ApprovalService
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case
    with s.transaction() as uow:
        s.i02_mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'id':uuid4(),'identity_paths':('name',)}));uow.commit()
    bundle=ordinary_bundle(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        data=[{'code':'A','name':'Ready','area':'1'},{'code':'B','name':'','area':'2'},
              {'code':'C','name':'Invalid','area':'not-a-number'}]
        blob=store.put(io.BytesIO(json.dumps(data).encode()),'application/json')
        ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,filename='mixed.json',metadata={})
        options=FileOptions(format='json',encoding='utf-8',delimiter=',',sheet=None,header_row=1,records_path='',
            key_paths=('code',),source_crs=None,source_timezone=None,data_nature='synthetic')
        service=IngestionService(uow)
        report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=blob,file_options=options,mode='full'),bundle_id=bundle.id))
        assert (report.ready_count,report.review_count,report.invalid_count)==(1,1,1)
        assert all(row.issue_refs and row.issue_refs[0].locator for row in report.rows if row.state!='ready')
        assert uow.session.execute(select(func.count()).select_from(source_evaluations)).scalar_one()==0
        ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Review bounded mixed import')
        job=service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    runner=Runner(s.app,s.config)
    for _ in range(150):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        run=IngestionService(uow).get(s.actor('alice'),job.id)
        assert (run.applied_count,run.review_count,run.failed_count,run.complete)==(1,1,1,False)
        assert uow.session.execute(select(func.count()).select_from(source_evaluations)).scalar_one()==1
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==3
        rows=IngestionService(uow).rows(s.actor('alice'),job.id).items
        for row in rows:
            if row.state!='applied' and row.entity_id:
                assert uow.session.execute(select(objects.c.id).where(objects.c.id==row.entity_id)).first() is None
