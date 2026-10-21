from uuid import uuid4
import pytest


def test_import_run_batch_requires_actual_f05_execution_context():
    from lzo.ingestion.pipeline import IngestionService
    from lzo.errors import ApiError
    assert hasattr(IngestionService,"run_batch"),"Required bounded execution adapter is missing"
    with pytest.raises(ApiError) as error:
        IngestionService().run_batch(None,uuid4(),uuid4())
    assert error.value.code=="JOB_BATCH_REQUIRED"
