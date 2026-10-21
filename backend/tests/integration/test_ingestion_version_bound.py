"""Identical registered bytes require a new preview after reviewed publication."""
from uuid import uuid4
import pytest
from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case,raw_change,activate
from test_ingestion_review_boundaries import publish


def test_same_bytes_new_mapping_requires_new_preview(configuration_case):
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.schemas import UploadBinding,ImportSelection,ImportPreviewCommand,ApplyImportCommand
    from lzo.ingestion.pipeline import IngestionService
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.rules import RuleService
    from lzo.governance.approvals import ApprovalService
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.errors import ApiError
    from lzo.semantic.facts import current_assertions
    s=configuration_case;first_bundle=activate(s);raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        selection=ImportSelection(source_id=s.i02_source.id,upload=binding.blob,file_options=binding.options,mode='full')
        first=IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=selection,bundle_id=first_bundle.id))
        ApprovalService(uow).decide(s.actor('admin'),first.approval_id,'approve','First mapping');uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        mapping=SourceService(uow).save_mapping(uow,s.actor('alice'),s.i02_mapping.model_copy(update={'id':uuid4(),
            'fields':tuple(field.model_copy(update={'source_unit':None,'target_unit':None}) for field in s.i02_mapping.fields)}))
        second_bundle=RuleService(uow).stage_configuration(s.actor('alice'),(mapping.id,),s.i02_authority.id,s.i02_ruling.id);uow.commit()
    publish(s,second_bundle,s.i02_authority)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        with pytest.raises(ApiError) as stale:
            IngestionService(uow).apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=first.id,expected_revision=1,
                input_digest=first.input_digest,approval_id=first.approval_id))
        assert stale.value.status==409
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=IngestionService(uow)
        second=service.preview(s.actor('alice'),ImportPreviewCommand(selection=selection,bundle_id=second_bundle.id))
        assert second.input_digest==first.input_digest and second.id!=first.id and second.change_digest!=first.change_digest
        ApprovalService(uow).decide(s.actor('admin'),second.approval_id,'approve','Second mapping')
        job=service.apply(uow,s.actor('alice'),ApplyImportCommand(preview_id=second.id,expected_revision=1,
            input_digest=second.input_digest,approval_id=second.approval_id));uow.commit()
    runner=Runner(s.app,s.config)
    for _ in range(200):
        with s.transaction() as uow:state=uow.session.execute(select(jobs.c.status).where(jobs.c.id==job.id)).scalar_one()
        if state in ('succeeded','failed','paused'):break
        assert runner.run_once()
    assert state=='succeeded'
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        result=IngestionService(uow).rows(s.actor('alice'),job.id).items[0]
        assert result.state=='applied'
        assert {a['payload']['object']['value'] for a in current_assertions(uow,result.entity_id)}=={'2.5'}
