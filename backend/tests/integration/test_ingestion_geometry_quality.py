"""Complete S03 withdrawal quality and geometry-addition enforcement."""
import pytest
from sqlalchemy import select,func
from test_schema_releases import schema_case
from test_ingestion_rule_versions import configuration_case,raw_change,version
from test_ingestion_review_boundaries import publish
from test_ingestion_geometry_review import apply_initial


@pytest.fixture
def object_case(schema_case):
    from lzo.worker.runner import Runner
    from lzo.ontology.models import bundles
    s=schema_case;s.grant('alice','editor',scope='farm_a')
    draft=s.create({'classes':[{'iri':'urn:Pond','label':'Pond'}],
        'shapes':'''@prefix sh:<http://www.w3.org/ns/shacl#>.
          <urn:GeometryRequired> a sh:NodeShape; sh:targetClass <urn:Pond>;
            sh:property [sh:path <urn:lzo:geometry>; sh:minCount 1; sh:pattern ":1$"].'''})
    assert s.publish(draft,s.approve(draft)).status_code==202
    assert Runner(s.app,s.config).run_once()
    with s.transaction() as uow:s.bundle_id=uow.session.execute(select(bundles.c.id)).scalar_one()
    s.login();return s


def preview_change(s,change,bundle):
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.schemas import UploadBinding,ImportSelection,ImportPreviewCommand
    from lzo.ingestion.pipeline import IngestionService
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        values=uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==s.i02_source.id)).scalars()
        binding=next(UploadBinding.model_validate(v) for v in values if v['blob']['version_id']==str(change.snapshot.version_id))
        report=IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
            source_id=s.i02_source.id,upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
        uow.commit();return report


@pytest.fixture
def geometry_quality_case(configuration_case):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import AuthorityRule
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'fields':(),'geometry_path':'geometry','source_crs':'EPSG:4326'}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            AuthorityRule(predicate_iri='urn:lzo:geometry',owner_policy='source_backed',owner_source_ids=(s.i02_source.id,),digest=''),)))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
    publish(s,bundle,authority)
    initial=preview_change(s,raw_change(s,fields={'geometry':{'type':'Point','coordinates':[120,30]}}),bundle)
    assert initial.ready_count==1 and initial.invalid_count==0
    apply_initial(s,initial,bundle)
    return s,bundle,mapping,initial.rows[0].entity_id


@pytest.mark.parametrize('quality',['nonconforming','incomplete'])
def test_last_required_geometry_withdrawal_commits_with_honest_quality(geometry_quality_case,quality,monkeypatch):
    from lzo.semantic import files,semantica_adapter
    from lzo.semantic.models import geometry_supports,projection_events
    from lzo.semantic.facts import get_object_row,object_view
    from lzo.semantic.source_rows import selected_geometry
    from lzo.ingestion.pipeline import IngestionService
    from lzo.governance.models import revisions,outbox_events
    s,bundle,_,identifier=geometry_quality_case
    with s.transaction() as uow:support_id=uow.session.execute(select(geometry_supports.c.id)).scalar_one()
    if quality=='incomplete':
        execute=semantica_adapter.bounded_json
        def incomplete(module,payload,**kwargs):
            # Real finite executor input-budget classification, not a fake report.
            with monkeypatch.context() as scoped:
                scoped.setattr(files,'MAX_BYTES',1)
                return execute(module,payload,**kwargs)
        monkeypatch.setattr(semantica_adapter,'bounded_json',incomplete)
    report=preview_change(s,raw_change(s,fields={'geometry':None}),bundle)
    assert report.ready_count==1 and report.invalid_count==report.review_count==0, 'Explicit exact support withdrawal must remain executable'
    assert {issue.code for issue in report.rows[0].issue_refs}==({'SHACL_VIOLATION'} if quality=='nonconforming' else {'VALIDATION_PENDING'})
    apply_initial(s,report,bundle)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        support=uow.session.execute(select(geometry_supports).where(geometry_supports.c.id==support_id)).mappings().one()
        assert not support['active'] and support['withdrawn_event_id']
        row=get_object_row(uow,s.actor('alice'),identifier)
        assert row['revision']==2 and row['payload']['state']=='active'
        assert selected_geometry(uow,identifier) is None
        event=uow.session.execute(select(revisions).where(revisions.c.event_id==support['withdrawn_event_id'])).mappings().one()
        assert event['revision']==2 and event['payload']['geometry'] is None
        effect=event['payload']['effect']['row']
        assert effect['withdraw_geometry_support_ids']==[str(support_id)] and effect['patch']['additions']==[] and effect['geometry'] is None
        assert uow.session.execute(select(func.count()).select_from(outbox_events).where(outbox_events.c.event_id==event['event_id'])).scalar_one()==1
        assert uow.session.execute(select(projection_events.c.revision).where(projection_events.c.event_id==event['event_id'])).scalar_one()==2
        validation=event['payload']['validation']
        view=object_view(uow,s.actor('alice'),row)
        if quality=='nonconforming':
            assert validation is not None and not validation['conforms']
            assert any(v['path_iri']=='urn:lzo:geometry' and v['constraint_iri'].endswith('MinCountConstraintComponent') for v in validation['violations'])
            assert view.validation_state=='needs_review' and view.validation_report is not None and not view.validation_report.conforms
        else:
            assert validation is None and view.validation_state=='pending' and view.validation_report is None


def test_reviewed_invalid_geometry_replacement_cannot_use_withdrawal_tolerance(geometry_quality_case):
    from lzo.ingestion.rulings import RulingService
    from lzo.ingestion.schemas import RulingCommand
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic.source_rows import apply_source_row,validate_source_row
    from lzo.semantic.schemas import SourceRowInput
    from lzo.semantic.facts import get_object_row
    from lzo.semantic.models import geometry_supports
    from lzo.errors import ApiError
    s,bundle,_,identifier=geometry_quality_case
    report=preview_change(s,raw_change(s,fields={'geometry':{'type':'Point','coordinates':[121,31]}}),bundle)
    assert report.review_count==1 and report.ready_count==0
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=RulingService(uow);conflict=service.get(s.actor('alice'),report.rows[0].conflict_ids[0])
        approval=service.preview(s.actor('alice'),conflict.id,conflict.candidate_ids,(),'Reviewed replacement')
        ApprovalService(uow).decide(s.actor('admin'),approval.id,'approve','Independent exact replacement')
        decision=service.resolve(uow,s.actor('alice'),conflict.id,RulingCommand(expected_revision=conflict.revision,
            chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale='Reviewed replacement',approval_id=approval.id))
        row=SourceRowInput.model_validate(service.accepted_effect(s.actor('alice'),decision.id)['row'])
        assert row.geometry is not None and row.patch.additions==()
        validation=validate_source_row(uow,s.actor('alice'),row,bundle)
        assert validation is not None and not validation.conforms
        with pytest.raises(ApiError) as error:apply_source_row(uow,s.actor('alice'),row,bundle)
        assert error.value.code=='FACT_VALIDATION_FAILED'
    with s.transaction() as uow:
        assert get_object_row(uow,s.actor('alice'),identifier)['revision']==1
        assert uow.session.execute(select(func.count()).select_from(geometry_supports)).scalar_one()==1
