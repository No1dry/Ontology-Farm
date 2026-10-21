"""Prospective object scope is distinct from permission on the source resource."""
import pytest
from sqlalchemy import select
from test_ingestion_rule_versions import configuration_case,raw_change
from test_ingestion_pipeline import ordinary_bundle


def test_source_resource_edit_grant_does_not_authorize_new_object(configuration_case):
    from lzo.ingestion.models import source_uploads
    from lzo.ingestion.schemas import ImportPreviewCommand,ImportSelection,UploadBinding
    from lzo.ingestion.pipeline import IngestionService
    from lzo.errors import ApiError
    s=configuration_case;bundle=ordinary_bundle(s);raw_change(s)
    s.revoke(s.object_editor_grant)
    s.ids_by_name['source_only']=s.i02_source.id
    s.grant('alice','editor',resource='source_only')
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        binding=UploadBinding.model_validate(uow.session.execute(select(source_uploads.c.binding).where(
            source_uploads.c.source_id==s.i02_source.id)).scalar_one())
        with pytest.raises(ApiError) as failure:
            IngestionService(uow).preview(s.actor('alice'),ImportPreviewCommand(selection=ImportSelection(
                source_id=s.i02_source.id,upload=binding.blob,file_options=binding.options,mode='full'),bundle_id=bundle.id))
        assert failure.value.status==403
