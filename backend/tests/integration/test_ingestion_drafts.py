import io
import pytest
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,activate


@pytest.mark.parametrize("fmt,kind",[("turtle","schema"),("json-ld","shapes")])
def test_semantic_draft_selection_retains_s02_edit_boundary(configuration_case,fmt,kind):
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ontology.models import drafts,catalog
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.schemas import ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case;bundle=activate(s)
    raw=(b'@prefix owl: <http://www.w3.org/2002/07/owl#>. <urn:ImportedPond> a owl:Class .' if fmt=="turtle" else
        b'{"@id":"urn:ImportedShape","@type":"http://www.w3.org/ns/shacl#NodeShape","http://www.w3.org/ns/shacl#targetClass":{"@id":"urn:Pond"}}')
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(raw),"text/turtle" if fmt=="turtle" else "application/ld+json")
        ArtifactService(uow,store,s.config).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="schema.rdf",metadata={})
        before=uow.session.execute(select(func.count()).select_from(drafts)).scalar_one()
        service=IngestionService(uow)
        report=service.preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=blob,semantic_format=fmt,semantic_kind=kind,mode="full"),bundle_id=bundle.id))
        assert report.ready_count==1 and report.approval_id is None
        assert uow.session.execute(select(func.count()).select_from(drafts)).scalar_one()==before
        job=service.apply(uow,s.actor("alice"),ApplyImportCommand(preview_id=report.id,expected_revision=1,input_digest=report.input_digest))
        uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None:raise error
    runner=DiagnosticRunner(s.app,s.config)
    for _ in range(200):
        with s.transaction() as uow:
            status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if status in ("succeeded","failed","paused"):break
        assert runner.run_once()
    assert status=="succeeded"
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        result=IngestionService(uow).get(s.actor("alice"),job.id)
        assert result.schema_draft_id is not None
        draft=uow.session.execute(select(drafts).where(drafts.c.id==result.schema_draft_id)).mappings().one()
        assert draft["revision"]==1 and draft["state"]=="draft"
        assert uow.session.execute(select(catalog.c.active_bundle_id)).scalar_one()==bundle.id
        assert uow.session.execute(select(func.count()).select_from(drafts)).scalar_one()==before+1
