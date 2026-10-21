"""Geometry conflict choices carry the complete finite S03 row effect."""
from sqlalchemy import select
import pytest
from test_ingestion_rule_versions import configuration_case,raw_change,version
from test_ingestion_review_boundaries import publish


@pytest.mark.parametrize('reviewed_predecessor',[False,True])
def test_geometry_review_has_explicit_candidate_and_applies_one_revision(configuration_case,reviewed_predecessor):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import (AuthorityRule,ImportPreviewCommand,ImportSelection,ApplyImportCommand,
        UploadBinding,RulingCommand)
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.rulings import RulingService
    from lzo.governance.approvals import ApprovalService
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.semantic.source_rows import apply_source_row,selected_geometry
    from lzo.semantic.schemas import SourceRowInput
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'fields':(),'geometry_path':'geometry','source_crs':'EPSG:4326'}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            *s.i02_authority.authority_rules,AuthorityRule(predicate_iri='urn:lzo:geometry',owner_policy='source_backed',
                owner_source_ids=(s.i02_source.id,),comparable_time_path='seen' if reviewed_predecessor else None,digest=''))))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,authority)
    def preview():
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            bindings=uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==s.i02_source.id)).scalars().all()
            binding=next(UploadBinding.model_validate(v) for v in bindings if v['blob']['version_id']==str(change.snapshot.version_id))
            report=IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
                source_id=s.i02_source.id,upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id));uow.commit()
            return report
    change=raw_change(s,fields={'geometry':{'type':'Point','coordinates':[120,30]},'seen':'2026-01-01T00:00:00Z'})
    report=preview()
    if reviewed_predecessor:
        from lzo.ingestion.identity import IdentityService
        from lzo.ingestion.mapping import MappingService
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            identity=IdentityService(uow).resolve(uow,s.actor('alice'),change,mapping,True)
            mapped=MappingService(uow).plan(s.actor('alice'),change,mapping,bundle,identity)
            service=RulingService(uow);conflict=service.prepare(uow,s.actor('alice'),mapped,bundle)
            approval=service.preview(s.actor('alice'),conflict.id,conflict.candidate_ids,(),'Reviewed geometry creation')
            ApprovalService(uow).decide(s.actor('admin'),approval.id,'approve','Independent geometry decision')
            ruling=service.resolve(uow,s.actor('alice'),conflict.id,RulingCommand(expected_revision=conflict.revision,
                chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale='Reviewed geometry creation',approval_id=approval.id))
            effect=service.accepted_effect(s.actor('alice'),ruling.id)
            assert apply_source_row(uow,s.actor('alice'),SourceRowInput.model_validate(effect['row']),bundle).revision.revision==1
            uow.commit()
    else:
        apply_initial(s,report,bundle)
    change=raw_change(s,fields={'geometry':{'type':'Point','coordinates':[121,31]},'seen':'2026-01-02T00:00:00Z'})
    review=preview()
    assert review.review_count==1 and review.ready_count==0 and review.rows[0].conflict_ids, 'Geometry-only material replacement requires a row decision'
    assert review.rows[0].issue_refs[0].code=='SOURCE_REVIEW_REQUIRED'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        from lzo.semantic.facts import get_object_row
        from lzo.semantic.models import geometry_supports
        assert get_object_row(uow,s.actor('alice'),review.rows[0].entity_id)['revision']==1
        assert len(uow.session.execute(select(geometry_supports)).all())==1
        assert selected_geometry(uow,review.rows[0].entity_id) is None, 'History classification must not restore stale current geometry'
        service=RulingService(uow);conflict=service.get(s.actor('alice'),review.rows[0].conflict_ids[0])
        geometry=[c for c in conflict.candidate_values if c.predicate_iri=='urn:lzo:geometry']
        assert len(geometry)==1,'Geometry must be an explicit independently reviewable candidate'
        approval=service.preview(s.actor('alice'),conflict.id,conflict.candidate_ids,(),'Accept exact row')
        ApprovalService(uow).decide(s.actor('admin'),approval.id,'approve','Independent geometry choice')
        ruling=service.resolve(uow,s.actor('alice'),conflict.id,RulingCommand(expected_revision=conflict.revision,
            chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale='Accept exact row',approval_id=approval.id))
        effect=service.accepted_effect(s.actor('alice'),ruling.id)
        assert effect['operation']=='source.row'
        receipt=apply_source_row(uow,s.actor('alice'),SourceRowInput.model_validate(effect['row']),bundle)
        assert receipt.revision.revision==2
        assert selected_geometry(uow,conflict.entity_id)['normalized']=={'type':'Point','coordinates':[121,31]}
        uow.commit()


def apply_initial(s,report,bundle):
    from lzo.governance.approvals import ApprovalService
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.schemas import ApplyImportCommand
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Initial row')
        job=IngestionService(uow).apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=report.id,expected_revision=1,
            input_digest=report.input_digest,approval_id=report.approval_id));uow.commit()
    runner=Runner(s.app,s.config)
    for _ in range(150):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded'
