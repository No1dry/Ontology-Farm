"""An exact source geometry withdrawal preserves independently authored geometry."""
from sqlalchemy import select,func
from test_ingestion_rule_versions import configuration_case,raw_change,version
from test_ingestion_review_boundaries import publish


def test_source_geometry_withdrawal_preserves_manual_geometry_and_identity(configuration_case,split_delivery_worker,gis_tick):
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.ingestion.schemas import AuthorityRule,UploadBinding,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.pipeline import IngestionService
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic.models import geometry_supports
    from lzo.semantic.schemas import GeometryInput,SourceSupport
    from lzo.semantic.geometry import write_geometry
    from lzo.semantic.source_rows import selected_geometry
    from lzo.semantic.facts import get_object_row
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from uuid import uuid4
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={
            'fields':(),'geometry_path':'geometry','source_crs':'EPSG:4326'}))
        authority=RuleService(uow).create_version(uow,s.actor('alice'),version('authority',authority_rules=(
            AuthorityRule(predicate_iri='urn:lzo:geometry',owner_policy='source_backed',
                owner_source_ids=(s.i02_source.id,),digest=''),)))
        bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),authority.id,s.i02_ruling.id);uow.commit()
    # Manual geometry is authored while the genesis policy permits it, before
    # publication of the source-owned field. Do not bypass current authority.
    from lzo.ingestion.identity import IdentityService
    from lzo.ontology.releases import current_execution_bundle
    change=raw_change(s,fields={'geometry':{'type':'Point','coordinates':[120,30]}})
    with s.transaction() as uow:
        identity=IdentityService(uow).resolve(uow,s.actor('alice'),change,mapping,True)
        uow.commit()
    response=s.create_object(resource_id=identity.resource_id)
    assert response.status_code==201,response.text
    manual=SourceSupport(support_id=uuid4(),source_id=None,record_key=None,source_version=None,snapshot=None,
        raw_value={'manual':True},mapping_version_id=None,evidence_refs=(),observed_at=None)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        write_geometry(uow,s.actor('alice'),GeometryInput(resource_id=identity.resource_id,expected_revision=1,
            geojson={'type':'Point','coordinates':[120,30]},source_crs='EPSG:4326',source_support=manual,decision_ref=None),
            current_execution_bundle(uow,s.actor('alice')))
        uow.commit()
    publish(s,bundle,authority)
    class DiagnosticRunner(Runner):
        def finish(self,ctx,error=None):
            super().finish(ctx,error)
            if error is not None:raise error
    runner=DiagnosticRunner(s.app,s.config)
    def execute(change, *, invalid=False):
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            values=uow.session.execute(select(source_uploads.c.binding).where(source_uploads.c.source_id==s.i02_source.id)).scalars()
            binding=next(UploadBinding.model_validate(v) for v in values if v['blob']['version_id']==str(change.snapshot.version_id))
            service=IngestionService(uow)
            report=service.preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(source_id=s.i02_source.id,
                upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
            if invalid:
                assert report.invalid_count==1 and report.ready_count==0, 'Missing geometry path must not become a withdrawal'
                uow.commit()
                return report
            assert report.ready_count==1
            ApprovalService(uow).decide(s.actor('admin'),report.approval_id,'approve','Independent exact geometry effect')
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
    first=execute(change)
    assert first.entity_id==identity.resource_id and first.revision.revision==3
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert selected_geometry(uow,first.entity_id)['source_support']['support_id']==str(manual.support_id), 'Equal source evidence must preserve independent manual selection'
        stored=uow.session.execute(select(geometry_supports)).mappings().one()
        assert stored['active'] and stored['revision_ref']['revision']==3
        assert stored['payload']['decision_ref'] and stored['payload']['source_support']['source_id']==str(s.i02_source.id)
    execute(raw_change(s,fields={}),invalid=True)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        assert get_object_row(uow,s.actor('alice'),first.entity_id)['revision']==3
        assert uow.session.execute(select(func.count()).select_from(geometry_supports).where(geometry_supports.c.active.is_(True))).scalar_one()==1
    removed=execute(raw_change(s,fields={'geometry':None}))
    assert removed.entity_id==first.entity_id and removed.revision.revision==4
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        row=uow.session.execute(select(geometry_supports)).mappings().one()
        assert not row['active'] and row['withdrawn_event_id']==removed.revision.event_id
        assert get_object_row(uow,s.actor('alice'),first.entity_id)['payload']['state']=='active'
        selected=selected_geometry(uow,first.entity_id)
        assert selected['source_support']['support_id']==str(manual.support_id)
        assert selected['normalized']=={'type':'Point','coordinates':[120,30]}
    if s.config.storage_mode=='split':
        gis_tick(s)
        from lzo.semantic.geometry import list_geometry
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            result=list_geometry(uow,s.actor('alice'),identifier=first.entity_id)
            assert result.gis_status=='current' and result.features[0]['id']==str(first.entity_id)
            from sqlalchemy import text
            assert uow.session.execute(text('SELECT count(*) FROM semantic_geometry')).scalar_one()==0
