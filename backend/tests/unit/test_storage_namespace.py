"""A recovery/migration blob destination must be new and bound to its owner."""
from types import SimpleNamespace
from uuid import uuid4

import pytest


def test_local_namespace_claim_is_exclusive_and_verifies_owner(tmp_path):
    from lzo.storage.migration import claim_blob_namespace, verify_blob_namespace, MigrationError
    settings = SimpleNamespace(blob_provider='local', file_root=tmp_path / 'new')
    identifier = uuid4()
    claim_blob_namespace(settings, 'migration', identifier)
    verify_blob_namespace(settings, 'migration', identifier)
    with pytest.raises(MigrationError, match='TARGET_BLOB_NAMESPACE_NOT_NEW'):
        claim_blob_namespace(settings, 'migration', uuid4())
    with pytest.raises(MigrationError, match='TARGET_BLOB_NAMESPACE_CHANGED'):
        verify_blob_namespace(settings, 'migration', uuid4())


def test_preexisting_local_destination_is_preserved_and_refused(tmp_path):
    from lzo.storage.migration import claim_blob_namespace, MigrationError
    root = tmp_path / 'existing'
    root.mkdir()
    original = root / 'original.txt'
    original.write_text('preserve source material')
    with pytest.raises(MigrationError, match='TARGET_BLOB_NAMESPACE_NOT_NEW'):
        claim_blob_namespace(SimpleNamespace(blob_provider='local', file_root=root), 'migration', uuid4())
    assert original.read_text() == 'preserve source material'
