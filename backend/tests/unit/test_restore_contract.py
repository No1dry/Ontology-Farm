"""Restore refuses ambiguous/source destinations before opening any connection."""
import pytest


@pytest.mark.parametrize('namespace', ['lzo', 'lzo_gis', '../production', 'lzo_restore_', 'LZO_restore_test'])
def test_restore_rejects_source_or_ambiguous_namespace_before_io(tmp_path, namespace):
    from lzo.storage.restore import RestoreService
    from lzo.storage.backup_format import BackupError
    restore = RestoreService(None, tmp_path / 'pg', tmp_path / 'authority')
    with pytest.raises(BackupError, match='INVALID_RESTORE_NAMESPACE'):
        restore.restore(tmp_path / 'missing-manifest', namespace, tmp_path / 'destination')
