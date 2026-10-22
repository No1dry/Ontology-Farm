"""Same durable upload contract on local and actual private S3, never mocked."""
import asyncio
import hashlib
import io

import pytest
from sqlalchemy import select, update

from lzo.errors import ApiError


@pytest.fixture(params=['local', 's3'])
def uploads(artifacts, request, tmp_path):
    from lzo.storage.blobs import get_blob_store
    from lzo.artifacts.uploads import UploadService
    from dotenv import dotenv_values
    s = artifacts
    changes = {'blob_provider': request.param, 'file_root': tmp_path,
               'artifact_upload_part_bytes': 4, 'artifact_user_quota_bytes': 20,
               'artifact_scope_quota_bytes': 20}
    if request.param == 's3':
        env = dotenv_values('.runtime/object-store/services.env')
        assert env, 'Real native S3 must be provisioned first'
        from pathlib import Path
        changes.update(s3_endpoint=env['LZO_S3_ENDPOINT'], s3_bucket=env['LZO_S3_BUCKET'],
            s3_access_key_file=Path(env['LZO_S3_ACCESS_KEY_FILE']),
            s3_secret_key_file=Path(env['LZO_S3_SECRET_KEY_FILE']), blob_staging_path=tmp_path/'staging')
    s.limits = s.limits.model_copy(update=changes)
    s.store = get_blob_store(s.limits)
    def service(uow):
        return UploadService(uow, s.store, s.limits)
    s.upload_service = service
    s.resource_id = s.ids_by_name['object_a']
    def create(content=b'abcdef', **overrides):
        args = dict(resource_id=s.resource_id, filename='clip.bin', media_type='application/octet-stream',
                    size_bytes=len(content), sha256=hashlib.sha256(content).hexdigest(),
                    metadata={}, idempotency_key='session-one')
        args.update(overrides)
        with s.transaction() as uow:
            result = service(uow).create(s.actor('alice'), **args)
            uow.commit()
            return result['id']
    s.create_session = create
    def part(identifier, number, content):
        async def chunks():
            yield content
        staged = asyncio.run(s.store.stage_async(chunks(), 'application/octet-stream'))
        try:
            with s.transaction() as uow:
                result = service(uow).part(s.actor('alice'), identifier, number, staged)
                uow.commit()
                return result
        finally:
            s.store.discard(staged)
    s.part = part
    return s


def test_resume_conflicting_part_and_duplicate_finalize(uploads):
    s = uploads
    identifier = s.create_session()
    assert s.create_session() == identifier
    s.part(identifier, 1, b'abcd')
    s.part(identifier, 1, b'abcd')
    with pytest.raises(ApiError, match='UPLOAD_PART_CONFLICT'):
        s.part(identifier, 1, b'xxxx')
    # New service/transaction simulates client and process losing all transient state.
    with s.transaction() as uow:
        assert s.upload_service(uow).status(s.actor('alice'), identifier)['parts'] == [1]
        with pytest.raises(ApiError, match='UPLOAD_INCOMPLETE'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)
    s.part(identifier, 2, b'ef')
    with s.transaction() as uow:
        detail = s.upload_service(uow).complete(s.actor('alice'), identifier)
        uow.commit()
    with s.transaction() as uow:
        again = s.upload_service(uow).complete(s.actor('alice'), identifier)
        assert again.version_id == detail.version_id
        uow.commit()
    with s.store.open(detail) as stream:
        assert stream.read() == b'abcdef'


def test_wrong_digest_cannot_publish(uploads):
    s = uploads
    identifier = s.create_session(sha256='0'*64)
    s.part(identifier, 1, b'abcd')
    s.part(identifier, 2, b'ef')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='UPLOAD_DIGEST_MISMATCH'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)


def test_quota_reserved_and_cancelled_session_never_publishes(uploads):
    s = uploads
    identifier = s.create_session(b'x'*15)
    with pytest.raises(ApiError, match='ARTIFACT_QUOTA_EXCEEDED'):
        s.create_session(b'x'*6, idempotency_key='other')
    with s.transaction() as uow:
        s.upload_service(uow).cancel(s.actor('alice'), identifier)
        uow.commit()
    s.create_session(b'x'*6, idempotency_key='other')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='UPLOAD_NOT_ACTIVE'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)


def test_expired_and_stale_sessions_cannot_publish(uploads):
    from lzo.artifacts.models import artifact_upload_sessions
    from datetime import datetime, timezone, timedelta
    s = uploads
    identifier = s.create_session()
    with s.transaction() as uow:
        uow.session.execute(update(artifact_upload_sessions).where(artifact_upload_sessions.c.id==identifier)
            .values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='UPLOAD_EXPIRED'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)


def test_stale_revision_cannot_publish(uploads):
    s = uploads
    identifier = s.create_session(expected_resource_revision=0)
    s.part(identifier, 1, b'abcd')
    s.part(identifier, 2, b'ef')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='REVISION_CONFLICT'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)


def test_current_revocation_blocks_resume_and_publish(uploads):
    from lzo.auth.models import grants
    from lzo.auth.schemas import GrantDetail
    s = uploads
    identifier = s.create_session()
    s.part(identifier, 1, b'abcd')
    with s.transaction() as uow:
        rows = uow.session.execute(select(grants).where(grants.c.user_id==s.users['alice'],
            grants.c.active.is_(True))).mappings().all()
    for row in rows:
        s.revoke(GrantDetail(**row))
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            s.upload_service(uow).complete(s.actor('alice'), identifier)
    with pytest.raises(ApiError):
        s.part(identifier, 2, b'ef')


def test_session_key_is_bound_to_expected_content(uploads):
    s = uploads
    s.create_session()
    with pytest.raises(ApiError, match='IDEMPOTENCY_CONFLICT'):
        s.create_session(b'xxxxxx')


def test_finalize_rollback_repairs_with_same_version_and_single_charge(uploads):
    from lzo.artifacts.models import artifact_versions, artifact_quotas
    s = uploads
    identifier = s.create_session()
    s.part(identifier, 1, b'abcd')
    s.part(identifier, 2, b'ef')
    # Content is accepted by S3/local but the core transaction loses its acknowledgement.
    with s.transaction() as uow:
        first = s.upload_service(uow).complete(s.actor('alice'), identifier)
        assert first.version_id == identifier
        # Deliberately no commit: simulate a crash between object and metadata commit.
    with s.transaction() as uow:
        assert s.upload_service(uow).status(s.actor('alice'), identifier)['state'] == 'active'
        result = s.upload_service(uow).complete(s.actor('alice'), identifier)
        uow.commit()
    assert result.version_id == first.version_id
    with s.transaction() as uow:
        assert len(uow.session.execute(select(artifact_versions)).all()) == 1
        assert set(uow.session.execute(select(artifact_quotas.c.used_bytes)).scalars()) == {6}


def test_s3_auth_denial_and_seekable_range(uploads):
    s = uploads
    ref = s.store.put(io.BytesIO(b'abcdef'), 'application/octet-stream')
    other = s.store.put(io.BytesIO(b'abcdef'), 'application/octet-stream')
    assert ref.sha256 == other.sha256 and ref.version_id != other.version_id
    with s.store.open_range(ref, 1, 3) as stream:
        assert stream.read() == b'bcd'
        stream.seek(0)
        assert stream.read() == b'bcd'
    if s.limits.blob_provider == 's3':
        import boto3
        from botocore.exceptions import ClientError
        from botocore.config import Config
        client = boto3.client('s3', endpoint_url=s.limits.s3_endpoint,
            aws_access_key_id='invalid', aws_secret_access_key='invalid',
            config=Config(signature_version='s3v4', retries={'max_attempts':0}))
        with pytest.raises(ClientError):
            client.get_object(Bucket=s.limits.s3_bucket, Key=s.store.key(ref))


def test_corruption_is_detected_and_never_overwritten(uploads):
    from uuid import uuid4
    s = uploads
    raw = uuid4().hex.encode()
    ref = s.store.put(io.BytesIO(raw), 'text/plain')
    if s.limits.blob_provider == 's3':
        s.store.client.put_object(Bucket=s.store.bucket,Key=s.store.key(ref),Body=b'x'*len(raw))
    else:
        s.store._path(ref.sha256).write_bytes(b'x'*len(raw))
    with pytest.raises(ApiError, match='BLOB_CORRUPT'):
        s.store.open(ref)
    with pytest.raises(ApiError, match='BLOB_CORRUPT'):
        s.store.put(io.BytesIO(raw), 'text/plain')


def test_expiry_maintenance_releases_reservation_without_publication(uploads):
    from datetime import datetime, timezone, timedelta
    from lzo.artifacts.models import artifact_upload_sessions
    s = uploads
    identifier = s.create_session(b'x'*15)
    with s.transaction() as uow:
        uow.session.execute(update(artifact_upload_sessions).where(artifact_upload_sessions.c.id==identifier)
            .values(expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        assert s.upload_service(uow).expire() == 1
        uow.commit()
    s.create_session(b'x'*15,idempotency_key='new')
    with s.transaction() as uow:
        with pytest.raises(ApiError,match='UPLOAD_NOT_ACTIVE'):
            s.upload_service(uow).complete(s.actor('alice'), identifier)


def test_resource_purpose_budget_overrides_generic_limit(uploads):
    from lzo.artifacts.service import _scope
    s = uploads
    with s.transaction() as uow:
        kind = _scope(uow, s.resource_id)['resource_kind']
    s.limits = s.limits.model_copy(update={'artifact_max_upload_bytes':4,
        'artifact_resource_budgets':{kind:6}})
    identifier = s.create_session()
    s.part(identifier, 1, b'abcd')
    s.part(identifier, 2, b'ef')
    with s.transaction() as uow:
        assert s.upload_service(uow).complete(s.actor('alice'), identifier).size_bytes == 6
        uow.commit()
    with pytest.raises(ApiError,match='UPLOAD_TOO_LARGE'):
        s.create_session(b'x'*7,idempotency_key='over-budget')
