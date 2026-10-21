from uuid import uuid4
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle
from owned_fuseki_fixture import owned_fuseki


def test_pg_commit_rdf_failure_retries_same_revision_without_duplicate_support(configuration_case,owned_fuseki):
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import source_uploads,import_run_rows
    from lzo.ingestion.schemas import UploadBinding,ImportPreviewCommand,ImportSelection,ApplyImportCommand
    from lzo.semantic import facts
    from lzo.semantic.schemas import CreateObjectInput
    from lzo.semantic.projection import ProjectionService,graph_iri
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.models import supports,projection_events
    from lzo.governance.models import revisions
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case;bundle=ordinary_bundle(s)
    config=s.config.model_copy(update={"fuseki_url":owned_fuseki.url,"job_max_attempts":1})
    repo=FusekiRepository(owned_fuseki.url,timeout_seconds=1)
    with s.transaction() as uow:
        uow.ontology_settings=uow.job_settings=config
        persisted=facts.register_object(uow,s.actor("alice"),CreateObjectInput(resource_id=uuid4(),class_iris=("urn:Pond",),
            display_name="Known persisted graph",owner_scope_id=s.scopes["farm_a"],state="active",data_nature="synthetic"),bundle)
        assert ProjectionService(uow,repo).project_revision(persisted.revision).state=="synced"
        uow.commit()
    known=repo.read_graph(graph_iri(persisted.revision.resource_id,"accepted"))
    assert known
    runner=Runner(s.app,config)
    for _ in range(200):
        if not runner.run_once():break
    raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        service=IngestionService(uow)
        report=service.preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=binding.blob,file_options=binding.options,mode="full"),bundle_id=bundle.id))
        job=service.apply(uow,s.actor("alice"),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    owned_fuseki.stop()
    for _ in range(200):
        with s.transaction() as uow:
            status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if status in ("succeeded","failed","paused"):break
        assert runner.run_once()
    assert status=="succeeded"
    with s.transaction() as uow:
        uow.ontology_settings=config
        result=IngestionService(uow).rows(s.actor("alice"),job.id).items[0]
        assert result.state=="applied" and result.revision.revision==1
        view=IngestionService(uow).get(s.actor("alice"),job.id)
        assert view.projection_state=="pending" and not view.complete
        support_count=uow.session.execute(select(func.count()).select_from(supports)).scalar_one()
        event_count=uow.session.execute(select(func.count()).select_from(revisions).where(revisions.c.resource_id==result.entity_id)).scalar_one()
        projection_job=uow.session.execute(select(jobs).where(jobs.c.kind=="rdf.project",
            jobs.c.payload["event_id"].astext==str(result.revision.event_id))).mappings().one()
        projection_id=projection_job["id"]
    for _ in range(200):
        with s.transaction() as uow:
            projection_job=uow.session.execute(select(jobs).where(jobs.c.id==projection_id)).mappings().one()
        if projection_job["status"]=="failed":break
        assert runner.run_once()
    assert projection_job["status"]=="failed" and projection_job["error_code"]=="HANDLER_FAILED"
    with s.transaction() as uow:
        uow.ontology_settings=config
        assert IngestionService(uow).get(s.actor("alice"),job.id).projection_state=="failed"
    owned_fuseki.start()
    assert repo.read_graph(graph_iri(persisted.revision.resource_id,"accepted"))==known
    from lzo.worker.service import JobService
    with s.transaction() as uow:
        view=JobService(uow,config).get(s.actor("alice"),projection_id)
        JobService(uow,config).set_state(s.actor("alice"),projection_id,"queued",view.revision)
        uow.commit()
    for _ in range(200):
        with s.transaction() as uow:
            projection_job=uow.session.execute(select(jobs).where(jobs.c.id==projection_id)).mappings().one()
        if projection_job["status"]=="succeeded":break
        assert runner.run_once()
    assert projection_job["status"]=="succeeded" and projection_job["attempts"]==2
    with s.transaction() as uow:
        uow.ontology_settings=config
        receipt=ProjectionService(uow,repo).project_revision(result.revision)
        assert receipt.state=="synced" and ProjectionService(uow,repo).project_revision(result.revision)==receipt
        assert uow.session.execute(select(func.count()).select_from(supports)).scalar_one()==support_count
        assert uow.session.execute(select(func.count()).select_from(revisions).where(revisions.c.resource_id==result.entity_id)).scalar_one()==event_count==1
        assert uow.session.execute(select(func.count()).select_from(import_run_rows).where(import_run_rows.c.run_id==job.id)).scalar_one()==1
        assert uow.session.execute(select(projection_events.c.state).where(projection_events.c.event_id==result.revision.event_id)).scalar_one()=="synced"
        uow.commit()
    assert {t.object.value for t in repo.read_graph(graph_iri(result.entity_id,"accepted")) if t.predicate_iri=="urn:area"}=={"25000.0"}
