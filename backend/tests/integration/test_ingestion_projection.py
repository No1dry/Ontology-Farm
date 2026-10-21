"""Typed raw inputs must reach the same governed mapper and semantic store."""
import io
from uuid import uuid4
import pytest
from test_ingestion_rule_versions import configuration_case, activate, raw_change


def test_typed_registered_source_mapping_keeps_distinct_supports(configuration_case):
    from lzo.ingestion.schemas import FieldMap
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.identity import IdentityService
    from lzo.ingestion.mapping import MappingService
    from test_ingestion_review_boundaries import publish
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        profile=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={"fields":(
            FieldMap(source_path="values",predicate_iri="urn:area",term_kind="rdf",multiple=True),)}))
        bundle=RuleService(uow).stage_configuration(s.actor("alice"),(profile.id,),s.i02_authority.id,s.i02_ruling.id)
        uow.commit()
    publish(s,bundle,s.i02_authority)
    values=[{"kind":"literal","value":v,"datatype_iri":"http://www.w3.org/2001/XMLSchema#decimal"} for v in ("01.00","2.50")]
    change=raw_change(s,fields={"values":values})
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        identity=IdentityService(uow).resolve(uow,s.actor("alice"),change,profile,True)
        mapped=MappingService(uow).plan(s.actor("alice"),change,profile,bundle,identity)
        assert {a.object.value for a in mapped.facts}=={"01.00","2.50"}
        assert len({a.assertion_id for a in mapped.facts})==len({a.support.support_id for a in mapped.facts})==2
        assert all(a.support.raw_value["value"]==values for a in mapped.facts)
        assert MappingService(uow).plan(s.actor("alice"),change,profile,bundle,identity)==mapped
        uow.commit()


@pytest.mark.parametrize("fmt",["turtle","json-ld"])
def test_semantic_registry_authenticates_original_bytes(configuration_case,fmt):
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ingestion.schemas import SemanticFileOptions
    from lzo.ingestion.sources import UploadService
    from lzo.ingestion.semantic_files import SemanticFileParser
    from lzo.ingestion.models import source_records
    from lzo.errors import ApiError
    from sqlalchemy import select,func
    s=configuration_case
    raw=(b'<urn:pond> a <urn:Pond>; <urn:area> "01.00"^^<http://www.w3.org/2001/XMLSchema#decimal>.' if fmt=="turtle" else
        b'{"@id":"urn:pond","@type":"urn:Pond","urn:area":{"@value":"01.00","@type":"http://www.w3.org/2001/XMLSchema#decimal"}}')
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(raw),"text/turtle" if fmt=="turtle" else "application/ld+json")
        ArtifactService(uow,store,s.config).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="pond.rdf",metadata={})
        service=UploadService(uow,store,s.config)
        options=SemanticFileOptions(format=fmt,data_nature="synthetic")
        binding=service.register(s.actor("alice"),s.i02_source.id,blob,options,str(uuid4()))
        assert binding.parser_contract=="semantic-v1"
        parsed=SemanticFileParser(store,binding).parse()
        assert parsed.complete
        forged=parsed.model_copy(update={"changes":(parsed.changes[0].model_copy(update={"raw_fields":{"forged":True}}),)})
        with pytest.raises(ApiError):service.persist(s.actor("alice"),binding,forged)
        service.persist(s.actor("alice"),binding,parsed)
        service.persist(s.actor("alice"),binding,parsed)
        assert uow.session.execute(select(func.count()).select_from(source_records)).scalar_one()==1
        uow.commit()


@pytest.mark.parametrize('temporal',['current','future_geometry_only'])
def test_source_geometry_is_one_atomic_supported_row(configuration_case,temporal):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import AuthorityRule, ImportPreviewCommand, ImportSelection, UploadBinding
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import source_uploads
    from test_ingestion_rule_versions import version
    from test_ingestion_review_boundaries import publish
    from sqlalchemy import select
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        profile=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={
            "geometry_path":"geometry","source_crs":"EPSG:4326",
            **({'fields':()} if temporal=='future_geometry_only' else {})}))
        authority=RuleService(uow).create_version(uow,s.actor("alice"),version("authority",authority_rules=(
            AuthorityRule(predicate_iri="urn:area",owner_policy="source_backed",owner_source_ids=(s.i02_source.id,),digest=""),
            AuthorityRule(predicate_iri="urn:lzo:geometry",owner_policy="source_backed",owner_source_ids=(s.i02_source.id,),digest=""))))
        bundle=RuleService(uow).stage_configuration(s.actor("alice"),(profile.id,),authority.id,s.i02_ruling.id)
        uow.commit()
    publish(s,bundle,authority)
    raw_change(s,fields={"geometry":{"type":"Point","coordinates":[120,30]}},
        temporal={'start':'2099-01-01T00:00:00Z','end':'2100-01-01T00:00:00Z','seen':'2026-01-01T00:00:00Z'}
            if temporal=='future_geometry_only' else None)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        report=IngestionService(uow).preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(
            source_id=s.i02_source.id,upload=binding.blob,file_options=binding.options,mode="full"),bundle_id=bundle.id))
        assert report.ready_count==1 and report.invalid_count==0
        from lzo.governance.approvals import ApprovalService
        from lzo.ingestion.schemas import ApplyImportCommand
        ApprovalService(uow).decide(s.actor("admin"),report.approval_id,"approve","Review atomic geometry and facts")
        job=IngestionService(uow).apply(uow,s.actor("alice"),ApplyImportCommand(preview_id=report.id,
            expected_revision=1,input_digest=report.input_digest,approval_id=report.approval_id))
        uow.commit()
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
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
        from lzo.semantic.source_rows import selected_geometry
        from lzo.semantic.models import geometry_supports
        from lzo.governance.models import revisions
        result=IngestionService(uow).rows(s.actor("alice"),job.id).items[0]
        assert result.state=="applied" and result.revision.revision==1
        geometry=selected_geometry(uow,result.entity_id)
        if temporal=='future_geometry_only':
            from datetime import datetime,timezone
            from lzo.semantic.models import objects
            assert geometry is None
            boundary=uow.session.execute(select(objects.c.next_transition_at).where(objects.c.id==result.entity_id)).scalar_one()
            assert boundary==datetime(2099,1,1,tzinfo=timezone.utc),'Geometry-only support must schedule its validity boundary'
            uow.facts_as_of=datetime(2099,1,2,tzinfo=timezone.utc)
            geometry=selected_geometry(uow,result.entity_id)
            assert geometry is not None,'Future support becomes eligible without needing an already active spatial index'
            from lzo.semantic.geometry import list_geometry
            assert len(list_geometry(uow,s.actor('alice'),identifier=result.entity_id,bbox='119,29,121,31').features)==1
        assert geometry["normalized"]=={"type":"Point","coordinates":[120,30]}
        lineage=uow.session.execute(select(geometry_supports).where(geometry_supports.c.resource_id==result.entity_id)).mappings().one()
        assert lineage["revision_ref"]["event_id"]==str(result.revision.event_id)
        event=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==result.revision.event_id)).scalar_one()
        assert event["effect"]["operation"]=="source.row"
        if temporal=='current':assert event["geometry"]["normalized"]==geometry["normalized"]
        else:assert event['geometry'] is None
        assert len(result.assertion_ids)==(1 if temporal=='current' else 0)


@pytest.mark.parametrize("fmt",["turtle","json-ld"])
def test_semantic_import_reuses_governed_pipeline_and_roundtrips(configuration_case,fmt):
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ingestion.sources import SourceService,digest
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import FieldMap,ImportSelection,ImportPreviewCommand
    from lzo.ingestion.pipeline import IngestionService
    from test_ingestion_review_boundaries import publish
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        profile=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={
            "identity_paths":("subject.value",),"name_path":"subject.value","fields":(
                FieldMap(source_path="terms."+digest("urn:area"),predicate_iri="urn:area",term_kind="rdf",multiple=True),)}))
        bundle=RuleService(uow).stage_configuration(s.actor("alice"),(profile.id,),s.i02_authority.id,s.i02_ruling.id)
        uow.commit()
    publish(s,bundle,s.i02_authority)
    raw=(b'<urn:pond> a <urn:Pond>; <urn:area> "01.00"^^<http://www.w3.org/2001/XMLSchema#decimal>, "2.50"^^<http://www.w3.org/2001/XMLSchema#decimal>.' if fmt=="turtle" else
        b'{"@id":"urn:pond","@type":"urn:Pond","urn:area":[{"@value":"01.00","@type":"http://www.w3.org/2001/XMLSchema#decimal"},{"@value":"2.50","@type":"http://www.w3.org/2001/XMLSchema#decimal"}]}')
    with s.transaction() as uow:
        uow.ontology_settings=s.config;store=store_for(uow)
        blob=store.put(io.BytesIO(raw),"text/turtle" if fmt=="turtle" else "application/ld+json")
        ArtifactService(uow,store,s.config).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="pond.rdf",metadata={})
        report=IngestionService(uow).preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(
            source_id=s.i02_source.id,upload=blob,semantic_format=fmt,semantic_kind="instances",mode="full"),bundle_id=bundle.id))
        assert report.ready_count==1 and report.invalid_count==report.review_count==0
        from lzo.ingestion.schemas import ApplyImportCommand
        from lzo.governance.approvals import ApprovalService
        ApprovalService(uow).decide(s.actor("admin"),report.approval_id,"approve","Review exact typed semantic import")
        job=IngestionService(uow).apply(uow,s.actor("alice"),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id))
        uow.commit()
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from sqlalchemy import select
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
        result=IngestionService(uow).rows(s.actor("alice"),job.id).items[0]
        assert result.state=="applied"
        from lzo.semantic.facts import current_assertions
        values=current_assertions(uow,result.entity_id)
        assert {a["payload"]["object"]["value"] for a in values}=={"01.00","2.50"}
        assert len({a["supports"][0]["id"] for a in values})==2
        for assertion in values:
            provenance=assertion["supports"][0]["payload"]
            assert provenance["snapshot"]["version_id"]==str(blob.version_id)
            assert provenance["raw_value"]["semantic"]["subject"]["value"]=="urn:pond"
            assert provenance["raw_value"]["semantic"]["graph"] is None
        from lzo.semantic.projection import ProjectionService,graph_iri
        from lzo.semantic.fuseki import FusekiRepository
        repo=FusekiRepository(s.config.fuseki_url)
        assert ProjectionService(uow,repo,actor=s.actor("alice")).project_revision(result.revision).state=="synced"
        uow.commit()
    from lzo.semantic.facts import object_iri
    from lzo.semantic.rdf import Triple,RdfTerm
    from rdflib.namespace import RDF
    expected={Triple(object_iri(result.entity_id),str(RDF.type),RdfTerm('iri','urn:Pond')),
        *(Triple(object_iri(result.entity_id),'urn:area',RdfTerm('literal',value,
            'http://www.w3.org/2001/XMLSchema#decimal')) for value in ('01.00','2.50'))}
    assert set(repo.read_graph(graph_iri(result.entity_id,"accepted")))==expected
    from test_semantic_export import grant_export
    grant_export(s,[result.entity_id],name="alice")
    s.login()
    exported=s.client.post("/api/v1/query/exports",headers=s.headers(),json={"resource_ids":[str(result.entity_id)],
        "format":fmt,"layout":"single-graph","graph_role":"accepted"})
    assert exported.status_code==202,exported.text
    from uuid import UUID
    export_id=UUID(exported.json()["id"])
    for _ in range(250):
        with s.transaction() as uow:
            status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==export_id)).scalar_one()
        if status in ("succeeded","failed","paused"):break
        assert runner.run_once()
    assert status=="succeeded"
    detail=s.client.get("/api/v1/jobs/"+str(export_id)).json()
    content=s.client.get(detail["result_ref"]["api_path"]+"/content")
    assert content.status_code==200
    from lzo.semantic.files import parse_text
    dataset=parse_text(content.text,fmt,allow_platform_skolem=True)
    from lzo.semantic.rdf import term_from_node
    assert {Triple(str(subject),str(predicate),term_from_node(value)) for subject,predicate,value,_ in dataset.quads()}==expected
    s.login("nobody")
    assert s.client.get("/api/v1/imports/"+str(job.id)).status_code==404
