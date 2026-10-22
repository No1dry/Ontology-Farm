"""Restore refuses ambiguous/source destinations before opening any connection."""
import pytest


def test_restore_maps_all_connections_to_explicit_destination():
    from sqlalchemy.engine import make_url
    from lzo.storage.restore import destination_url
    source = make_url('postgresql+psycopg://domain:password@source:25432/source?options=-csearch_path%3Downed')
    admin = make_url('postgresql+psycopg://postgres:other@destination:25433/postgres')
    mapped = destination_url(source, admin, 'lzo_restore_12345678_core')
    assert (mapped.host, mapped.port, mapped.database) == ('destination', 25433, 'lzo_restore_12345678_core')
    assert (mapped.username, mapped.password, mapped.query) == (source.username, source.password, source.query)


@pytest.mark.parametrize('key', ['host', 'hostaddr', 'port', 'service', 'servicefile', 'dbname'])
def test_restore_rejects_hidden_transport_override(key):
    from sqlalchemy.engine import make_url
    from lzo.storage.restore import destination_url
    from lzo.storage.backup_format import BackupError
    source = make_url('postgresql+psycopg://domain:password@source:25432/source').update_query_dict({key: 'elsewhere'})
    with pytest.raises(BackupError, match='RESTORE_TRANSPORT_OVERRIDE'):
        destination_url(source, make_url('postgresql+psycopg://admin@destination:25433/postgres'), 'new')


@pytest.mark.parametrize('key', ['PGHOSTADDR', 'PGSERVICE', 'PGSERVICEFILE'])
def test_restore_refuses_ambient_transport_override_before_package_io(tmp_path, monkeypatch, key):
    from lzo.storage.restore import RestoreService
    from lzo.storage.backup_format import BackupError
    monkeypatch.setenv(key, 'elsewhere')
    destination = tmp_path / 'new'
    with pytest.raises(BackupError, match='RESTORE_TRANSPORT_OVERRIDE'):
        RestoreService(None, tmp_path, tmp_path / 'key').restore(tmp_path / 'missing', 'lzo_restore_12345678', destination)
    assert not destination.exists()


@pytest.mark.parametrize('namespace', ['lzo', 'lzo_gis', '../production', 'lzo_restore_', 'LZO_restore_test'])
def test_restore_rejects_source_or_ambiguous_namespace_before_io(tmp_path, namespace):
    from lzo.storage.restore import RestoreService
    from lzo.storage.backup_format import BackupError
    restore = RestoreService(None, tmp_path / 'pg', tmp_path / 'authority')
    with pytest.raises(BackupError, match='INVALID_RESTORE_NAMESPACE'):
        restore.restore(tmp_path / 'missing-manifest', namespace, tmp_path / 'destination')
