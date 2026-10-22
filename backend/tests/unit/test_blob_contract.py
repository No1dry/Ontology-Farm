import io
from types import SimpleNamespace

import pytest

from lzo.errors import ApiError
from lzo.artifacts.store import ArtifactStore


def test_local_detects_same_length_corruption(tmp_path):
    store = ArtifactStore(tmp_path, max_upload_bytes=100)
    ref = store.put(io.BytesIO(b'first'), 'text/plain')
    store._path(ref.sha256).write_bytes(b'other')
    with pytest.raises(ApiError, match='BLOB_CORRUPT'):
        store.open(ref)


def test_local_refuses_corrupt_deduplicated_object(tmp_path):
    store = ArtifactStore(tmp_path, max_upload_bytes=100)
    ref = store.put(io.BytesIO(b'first'), 'text/plain')
    store._path(ref.sha256).write_bytes(b'other')
    with pytest.raises(ApiError, match='BLOB_CORRUPT'):
        store.put(io.BytesIO(b'first'), 'text/plain')


def test_complete_verifies_staged_bytes(tmp_path):
    import asyncio
    store = ArtifactStore(tmp_path, max_upload_bytes=100)
    async def chunks():
        yield b'first'
    staged = asyncio.run(store.stage_async(chunks(), 'text/plain'))
    staged.path.write_bytes(b'other')
    with pytest.raises(ApiError, match='BLOB_CORRUPT'):
        store.complete(staged)


def test_factory_preserves_legacy_locations(tmp_path):
    from lzo.storage.blobs import get_blob_store
    settings = SimpleNamespace(blob_provider='local', file_root=tmp_path, artifact_max_upload_bytes=100)
    old = ArtifactStore(tmp_path, max_upload_bytes=100)
    ref = old.put(io.BytesIO(b'legacy'), 'text/plain')
    with get_blob_store(settings).open(ref) as stream:
        assert stream.read() == b'legacy'
    new = get_blob_store(settings).put(io.BytesIO(b'new'), 'text/plain')
    assert (tmp_path / 'artifacts' / 'objects' / new.sha256[:2] / new.sha256).exists()


def test_s3_migration_namespace_keeps_original_keys_and_isolates_new_keys(tmp_path):
    from lzo.storage.s3 import S3BlobStore
    from lzo.contracts import BlobRef
    from uuid import uuid4
    access, secret = tmp_path / 'access', tmp_path / 'secret'
    access.write_text('synthetic-access')
    secret.write_text('synthetic-secret')
    values = dict(blob_staging_path=tmp_path, artifact_max_upload_bytes=100,
        s3_bucket='synthetic', s3_endpoint='http://127.0.0.1:1', s3_region='us-east-1',
        s3_access_key_file=access, s3_secret_key_file=secret)
    blob = BlobRef('a' * 64, 10, 'text/plain', uuid4())
    original = S3BlobStore(SimpleNamespace(**values, s3_key_prefix=''))
    migrated = S3BlobStore(SimpleNamespace(**values, s3_key_prefix='migrations/owned'))
    assert original.key(blob) == 'objects/aa/' + 'a' * 64
    assert migrated.key(blob) == 'migrations/owned/objects/aa/' + 'a' * 64
    for prefix in ('../source', '/source', 'source//nested', 'source/../nested'):
        with pytest.raises(ValueError):
            S3BlobStore(SimpleNamespace(**values, s3_key_prefix=prefix))


@pytest.mark.parametrize('header,expected', [('bytes=1-3',(1,3)), ('bytes=3-',(3,5)), ('bytes=-2',(4,5)), (None,None)])
def test_ranges(header, expected):
    from lzo.storage.blobs import parse_range
    assert parse_range(header, 6) == expected


@pytest.mark.parametrize('header', ['bytes=6-', 'bytes=3-1', 'bytes=0-1,3-4', 'items=0-1','bytes=-0'])
def test_invalid_ranges(header):
    from lzo.storage.blobs import parse_range
    with pytest.raises(ApiError) as error:
        parse_range(header, 6)
    assert error.value.status == 416
