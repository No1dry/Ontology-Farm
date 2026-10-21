from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle


def test_source_type_only_evidence_has_independent_currentness(configuration_case):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.schemas import UploadBinding,ImportPreviewCommand,ImportSelection,ApplyImportCommand
    from lzo.ingestion.models import source_uploads
    from lzo.semantic.facts import confirmed_types
    from lzo.ingestion.rulings import RulingService
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        s.i02_mapping=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={"fields":()}))
        uow.commit()
    bundle=ordinary_bundle(s);raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        service=IngestionService(uow)
        report=service.preview(s.actor("alice"),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
            upload=binding.blob,file_options=binding.options,mode="full"),bundle_id=bundle.id))
        assert report.ready_count==1
        job=service.apply(uow,s.actor("alice"),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None:raise error
    runner=DiagnosticRunner(s.app,s.config)
    for _ in range(200):
        with s.transaction() as uow:status=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if status in ("succeeded","failed","paused"):break
        assert runner.run_once()
    assert status=="succeeded"
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        target=IngestionService(uow).rows(s.actor("alice"),job.id).items[0].entity_id
        assert confirmed_types(uow,s.actor("alice"),target)[1]=="active"
        assert not RulingService(uow).has_invalidated_supports(uow)
    raw_change(s,area="4",note="unmapped metadata changed")
    with s.transaction() as uow:
        assert confirmed_types(uow,s.actor("alice"),target)[1]=="active"
        assert not RulingService(uow).has_invalidated_supports(uow)
    # Independent manual items do not acquire type authority, and must not be
    # hidden when an unrelated source declaration becomes ineligible.
    from lzo.semantic.facts import apply_fact_patch,object_view,get_object_row
    from lzo.semantic.schemas import FactPatch,AssertionInput,GeometryInput
    from lzo.semantic.geometry import write_geometry
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        note=AssertionInput.model_validate(s.assertion(target,value='Independent note'))
        apply_fact_patch(uow,s.actor('alice'),FactPatch(resource_id=target,expected_revision=1,
            additions=(note,),withdraw_support_ids=()),bundle)
        revision=write_geometry(uow,s.actor('alice'),GeometryInput(resource_id=target,expected_revision=2,
            geojson={'type':'Point','coordinates':[120,30]},source_crs='EPSG:4326',
            source_support=note.support,decision_ref=None),bundle)
        assert ProjectionService(uow,FusekiRepository(s.config.fuseki_url),actor=s.actor('alice')).project_revision(revision).state=='synced'
        uow.commit()
    raw_change(s,area="4",temporal={"start":"2020-01-01T00:00:00Z","end":"2021-01-01T00:00:00Z","seen":"2020-01-01T00:00:00Z"})
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert confirmed_types(uow,s.actor("alice"),target)[1]=="inactive", "Source type evidence ignored changed source validity"
        assert RulingService(uow).has_invalidated_supports(uow), "Type-only invalidation must fence inference"
        view=object_view(uow,s.actor('alice'),get_object_row(uow,s.actor('alice'),target))
        assert view.state=='active' and view.class_iris==(),'Current classes must not repeat an ineligible source declaration'
        assert view.properties['urn:note'][0].value=='Independent note'
        from lzo.semantic.authorized_dataset import build_authorized_dataset
        from lzo.auth.permissions import permission_context
        from rdflib import URIRef,Literal
        from rdflib.namespace import RDF
        from lzo.semantic.facts import object_iri
        with permission_context(uow):snapshot=build_authorized_dataset(s.actor('alice'))
        graph=snapshot.dataset.default_graph;subject=URIRef(object_iri(target))
        assert list(map(str,graph.objects(subject,URIRef('urn:note'))))==['Independent note']
        assert list(graph.objects(subject,URIRef('urn:lzo:geometry')))
        assert not list(graph.objects(subject,RDF.type))
    # A historical class cannot authorize a fresh relation range dependency.
    from uuid import uuid4
    import pytest
    from lzo.errors import ApiError
    from lzo.semantic.schemas import CreateObjectInput
    from lzo.semantic.facts import register_object
    sensor=uuid4()
    create=CreateObjectInput(resource_id=sensor,class_iris=('urn:Sensor',),display_name='New sensor',
        owner_scope_id=s.scopes['farm_a'],related_scope_ids=(),state='active',data_nature='synthetic',decision_ref=None,
        assertions=(AssertionInput.model_validate(s.assertion(sensor)),
            AssertionInput.model_validate(s.assertion(sensor,predicate='urn:monitors',kind='iri',value=object_iri(target)))))
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        with pytest.raises(ApiError) as rejected:
            with uow.session.begin_nested():register_object(uow,s.actor('alice'),create,bundle)
        assert rejected.value.code=='LINK_TARGET_INACTIVE' and rejected.value.status==409
