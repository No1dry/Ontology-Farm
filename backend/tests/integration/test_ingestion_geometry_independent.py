"""Confirmed independent sources can support the same selected geometry."""
from sqlalchemy import select
from uuid import uuid4
from test_ingestion_rule_versions import configuration_case,raw_change,version


def test_withdrawing_one_source_geometry_preserves_other_source_support(configuration_case):
    from lzo.contracts import ResourceScope
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import (AlignmentRule,SourceCreate,AuthorityRule,ImpactCommand,ImpactSelection,
        PublishRuleCommand,UploadBinding,ImportSelection,ImportPreviewCommand,ApplyImportCommand)
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.pipeline import IngestionService
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic.models import geometry_supports
    from lzo.semantic.source_rows import selected_geometry
    from lzo.semantic.facts import get_object_row
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s=configuration_case;original=s.i02_source
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=RuleService(uow)
        alignment=service.create_version(uow,s.actor('alice'),version('alignment',alignment_rules=(AlignmentRule(
            id=uuid4(),digest='',class_iri='urn:Pond',key_paths=('code',),normalizers=(),
            scope=ResourceScope(None,s.scopes['farm_a'],(),'data'),confirmed=True),)))
        first=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'fields':(),'geometry_path':'geometry','source_crs':'EPSG:4326',
            'confirmed_identity_rule_id':alignment.alignment_rules[0].id}))
        second_source=SourceService().create(uow,s.actor('alice'),SourceCreate(source_system='other',source_instance='two',
            collection='pond',kind='file',scope=ResourceScope(None,s.scopes['farm_a'],(),'data'),data_nature='synthetic'))
        second=SourceService(uow).save_mapping(uow,s.actor('alice'),first.model_copy(update={'source_id':second_source.id}))
        authority=service.create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            AuthorityRule(predicate_iri='urn:lzo:geometry',owner_policy='source_backed',
                owner_source_ids=(original.id,second_source.id),digest=''),)))
        bundle=service.stage_configuration(s.actor('alice'),(first.id,second.id),authority.id,s.i02_ruling.id)
        impact=service.impact(s.actor('alice'),ImpactCommand(target_bundle_id=bundle.id,selection=ImpactSelection(
            resource_ids=(),source_ids=(original.id,second_source.id),scope=ResourceScope(None,None,(),'schema')),rollback_of=None))
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'),impact.approval_id,'approve','Independent two-source configuration');uow.commit()
    with s.transaction() as uow:
        RuleService(uow).publish(uow,s.actor('alice'),PublishRuleCommand(rule_version_id=authority.id,expected_revision=1,
            impact_id=impact.id,approval_id=impact.approval_id));uow.commit()
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None:raise error
    runner=DiagnosticRunner(s.app,s.config)
    def execute(source,geometry):
        s.i02_source=source;change=raw_change(s,fields={'geometry':geometry})
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            values=uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==source.id)).scalars()
            binding=next(UploadBinding.model_validate(v) for v in values if v['blob']['version_id']==str(change.snapshot.version_id))
            service=IngestionService(uow)
            report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(source_id=source.id,
                upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
            assert report.ready_count==1
            ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Independent geometry source effect')
            job=service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
                input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
        for _ in range(180):
            with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
            if state in ('succeeded','failed','paused'):break
            assert runner.run_once()
        assert state=='succeeded'
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            return IngestionService(uow).rows(s.actor('alice'),job.id).items[0]
    geometry={'type':'Point','coordinates':[120,30]}
    one=execute(original,geometry);two=execute(second_source,geometry)
    assert one.entity_id==two.entity_id and two.revision.revision==2
    with s.transaction() as uow:
        assert len(uow.session.execute(select(geometry_supports).where(geometry_supports.c.active.is_(True))).all())==2
    removed=execute(original,None)
    assert removed.entity_id==one.entity_id and removed.revision.revision==3
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        entries=uow.session.execute(select(geometry_supports)).mappings().all()
        assert len(entries)==2 and sum(e['active'] for e in entries)==1
        assert selected_geometry(uow,one.entity_id)['source_support']['source_id']==str(second_source.id)
        assert selected_geometry(uow,one.entity_id)['normalized']==geometry
        assert get_object_row(uow,s.actor('alice'),one.entity_id)['payload']['state']=='active'
