"""Real prefix validation excludes cycles/dependents and contains capacity failures."""
import io
import pytest
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,version
from test_ingestion_review_boundaries import publish


@pytest.mark.parametrize('object_case',['recursive'],indirect=True)
def test_reference_exclusions_and_planning_capacity_rollback(configuration_case):
    from lzo.ingestion.sources import SourceService,digest
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import FieldMap,AuthorityRule,ImportSelection,ImportPreviewCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.models import source_evaluations
    from lzo.ontology.drafts import store_for
    from lzo.artifacts.service import ArtifactService
    from lzo.semantic.models import objects,supports
    from lzo.worker.models import jobs
    from lzo.errors import ApiError
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'identity_paths':('subject.value',),'name_path':'subject.value','fields':tuple(
                FieldMap(source_path='terms.'+digest(p),predicate_iri=p,term_kind=k,multiple=True)
                for p,k in (('urn:area','rdf'),('urn:next','reference')))}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            *s.i02_authority.authority_rules,AuthorityRule(predicate_iri='urn:next',owner_policy='source_backed',
                owner_source_ids=(s.i02_source.id,),digest=''))))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,authority)
    def preview(uow,text):
        store=store_for(uow);blob=store.put(io.BytesIO(text.encode()),'text/turtle')
        ArtifactService(uow,store,s.config).register_upload(s.actor('alice'),s.ids_by_name['object_a'],blob,filename='boundary.ttl',metadata={})
        return IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
            source_id=s.i02_source.id,upload=blob,semantic_format='turtle',semantic_kind='instances',mode='full'),bundle_id=bundle.id))
    cases=(
        ('<urn:cycle-a> a <urn:Pond>; <urn:next> <urn:cycle-b>. <urn:cycle-b> a <urn:Pond>; <urn:next> <urn:cycle-a>.',0,2,0),
        ('<urn:dependent> a <urn:Pond>; <urn:next> <urn:bad>. <urn:bad> a <urn:Pond>; <urn:area> "wrong".',0,1,1),
        ('<urn:external> a <urn:Pond>; <urn:next> <urn:unregistered>.',0,0,1),
    )
    for raw,ready,review,invalid in cases:
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            before=uow.session.execute(select(func.count()).select_from(jobs)).scalar_one()
            report=preview(uow,raw)
            assert (report.ready_count,report.review_count,report.invalid_count)==(ready,review,invalid)
            assert all(row.issue_refs and all(issue.locator for issue in row.issue_refs) for row in report.rows)
            for table in (objects,supports,source_evaluations):
                assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==0
            assert uow.session.execute(select(func.count()).select_from(jobs)).scalar_one()==before
            uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        report=preview(uow,'<urn:self> a <urn:Pond>; <urn:next> <urn:self>.')
        assert not any(issue.code=='IMPORT_DEPENDENCY_CYCLE' for row in report.rows for issue in row.issue_refs)
        assert report.ready_count+report.invalid_count==1,'Self reference must reach actual S03 validation'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        uow.job_settings=s.config.model_copy(update={'job_queue_limit':1})
        with pytest.raises(ApiError) as incomplete:
            preview(uow,'<urn:capacity> a <urn:Pond>.')
        assert incomplete.value.code=='IMPORT_PLANNING_INCOMPLETE' and incomplete.value.status==503
        for table in (objects,supports,source_evaluations):
            assert uow.session.execute(select(func.count()).select_from(table)).scalar_one()==0
