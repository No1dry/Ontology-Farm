import json

import pytest


def test_durable_stage_exclusive_atomic_publish_and_retry(tmp_path):
    from lzo.storage.maintenance import stage_publication, publish_staged
    from lzo.storage.migration import MigrationError
    path = tmp_path / 'private' / 'activation.json'
    payload = {'secret_settings': 'private-test-value'}
    pending = stage_publication(path, payload)
    assert not path.exists()
    assert json.loads(pending.read_text()) == payload
    with pytest.raises(MigrationError, match='ACTIVATION_STAGE_EXISTS'):
        stage_publication(path, payload)
    publish_staged(path, pending)
    assert json.loads(path.read_text()) == payload
    assert not pending.exists()
    # An unrelated existing destination is never replaced.
    pending.write_text(json.dumps({'different': True}))
    with pytest.raises(MigrationError, match='ACTIVATION_PATH_EXISTS'):
        publish_staged(path, pending)
    assert json.loads(path.read_text()) == payload


def test_bad_parent_fails_during_staging(tmp_path):
    from lzo.storage.maintenance import stage_publication
    parent = tmp_path / 'file'
    parent.write_text('preserved')
    with pytest.raises(OSError):
        stage_publication(parent / 'activation.json', {})
    assert parent.read_text() == 'preserved'
