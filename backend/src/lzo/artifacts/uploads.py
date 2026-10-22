"""Durable resumable uploads using existing current permissions and governance.

Parts are immutable blobs, not process-local files. Unreferenced bytes are retained
for reconciliation; this module never deletes content shared with accepted versions.
All state transitions and quota changes commit in the caller's core transaction.
"""
import hashlib
import math
import re
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import insert, select, update

from lzo.artifacts.models import artifact_quotas, artifact_upload_sessions as sessions
from lzo.artifacts.service import ArtifactService, _scope
from lzo.artifacts.store import StagedBlob
from lzo.contracts import BlobRef
from lzo.errors import ApiError, Conflict, require_idempotency_key
from lzo.governance.revisions import lock_ledger


class UploadService:
    def __init__(self, uow, store, settings):
        self.uow, self.store, self.settings = uow, store, settings
        self.artifacts = ArtifactService(uow, store, settings)

    def _get(self, actor, identifier):
        lock_ledger(self.uow)
        row = self.uow.session.execute(select(sessions).where(sessions.c.id == identifier)
            .with_for_update()).mappings().one_or_none()
        if row is None or row['actor_id'] != actor.subject_id:
            raise ApiError('NOT_FOUND', 404, 'Upload session not found')
        self.artifacts.authorize_upload(actor, row['resource_id'])
        return row

    def _active(self, row):
        if row['state'] != 'active':
            raise Conflict('UPLOAD_NOT_ACTIVE', 'Upload session is no longer active')
        if row['expires_at'] <= datetime.now(timezone.utc):
            raise Conflict('UPLOAD_EXPIRED', 'Upload session expired')

    def status(self, actor, identifier):
        row = self._get(actor, identifier)
        return dict(id=row['id'], state='expired' if row['state']=='active' and
            row['expires_at'] <= datetime.now(timezone.utc) else row['state'],
            expires_at=row['expires_at'], parts=sorted(int(n) for n in row['parts']),
            part_bytes=row['request']['part_bytes'], size_bytes=row['request']['size_bytes'],
            version_id=row['version_id'])

    def create(self, actor, *, resource_id, filename, media_type, size_bytes, sha256,
               idempotency_key, metadata=None, previous_version_id=None, expected_resource_revision=None):
        require_idempotency_key(idempotency_key)
        self.artifacts.authorize_upload(actor, resource_id)
        scope = _scope(self.uow, resource_id)
        budget = self.settings.artifact_resource_budgets.get(scope['resource_kind'],
            self.settings.artifact_max_upload_bytes)
        if not 0 < size_bytes <= budget:
            raise ApiError('UPLOAD_TOO_LARGE', 413, 'Artifact exceeds resource upload budget')
        if not re.fullmatch('[0-9a-f]{64}', sha256):
            raise ApiError('INVALID_DIGEST', 422, 'Expected SHA-256 is required')
        media_type = self.store._media(media_type)
        request = dict(resource_id=str(resource_id), filename=filename, media_type=media_type,
            size_bytes=size_bytes, sha256=sha256, metadata=metadata or {},
            previous_version_id=str(previous_version_id) if previous_version_id else None,
            expected_resource_revision=expected_resource_revision)
        lock_ledger(self.uow)
        existing = self.uow.session.execute(select(sessions).where(sessions.c.actor_id==actor.subject_id,
            sessions.c.idempotency_key==idempotency_key)).mappings().one_or_none()
        if existing:
            client_request = {key: value for key, value in existing['request'].items() if key != 'part_bytes'}
            if client_request != request:
                raise Conflict('IDEMPOTENCY_CONFLICT', 'Upload key reused with a different request')
            return self.status(actor, existing['id'])
        request['part_bytes'] = self.settings.artifact_upload_part_bytes
        if math.ceil(size_bytes/request['part_bytes']) > 10000:
            raise ApiError('UPLOAD_TOO_LARGE', 413, 'Upload exceeds part count budget')
        quota_id = scope['scope_id'] or resource_id
        self.artifacts._charge('user', actor.subject_id, size_bytes, self.settings.artifact_user_quota_bytes)
        self.artifacts._charge('scope', quota_id, size_bytes, self.settings.artifact_scope_quota_bytes)
        identifier = uuid4()
        self.uow.session.execute(insert(sessions).values(id=identifier, actor_id=actor.subject_id,
            resource_id=resource_id, scope_quota_id=quota_id, idempotency_key=idempotency_key,
            request=request, parts={}, state='active',
            expires_at=datetime.now(timezone.utc)+timedelta(seconds=self.settings.artifact_upload_session_seconds)))
        return self.status(actor, identifier)

    def part(self, actor, identifier, number, staged):
        row = self._get(actor, identifier)
        self._active(row)
        request = row['request']
        count = math.ceil(request['size_bytes']/request['part_bytes'])
        length = request['part_bytes'] if number < count else request['size_bytes']-(count-1)*request['part_bytes']
        if not 1 <= number <= count or staged.ref.size_bytes != length:
            raise ApiError('INVALID_UPLOAD_PART', 422, 'Part number or length is invalid')
        parts = dict(row['parts'])
        old = parts.get(str(number))
        if old:
            if (old['sha256'], old['size_bytes']) != (staged.ref.sha256, staged.ref.size_bytes):
                raise Conflict('UPLOAD_PART_CONFLICT', 'Part number already contains different bytes')
        else:
            ref = self.store.complete(staged)
            parts[str(number)] = dict(sha256=ref.sha256, size_bytes=ref.size_bytes,
                media_type=ref.media_type, version_id=str(ref.version_id))
            self.uow.session.execute(update(sessions).where(sessions.c.id==identifier).values(parts=parts))
        return self.status(actor, identifier)

    def _release(self, row):
        size = row['request']['size_bytes']
        for kind, owner in (('user', row['actor_id']), ('scope', row['scope_quota_id'])):
            self.uow.session.execute(update(artifact_quotas).where(artifact_quotas.c.kind==kind,
                artifact_quotas.c.owner_id==owner).values(used_bytes=artifact_quotas.c.used_bytes-size))

    def cancel(self, actor, identifier):
        row = self._get(actor, identifier)
        if row['state'] == 'complete':
            raise Conflict('UPLOAD_NOT_ACTIVE', 'Completed upload cannot be cancelled')
        if row['state'] == 'active':
            self._release(row)
            self.uow.session.execute(update(sessions).where(sessions.c.id==identifier).values(state='cancelled'))
        return self.status(actor, identifier)

    def expire(self):
        """Maintenance-only entry point; release reservations, retain forensic parts."""
        lock_ledger(self.uow)
        rows = self.uow.session.execute(select(sessions).where(sessions.c.state=='active',
            sessions.c.expires_at <= datetime.now(timezone.utc)).with_for_update()).mappings().all()
        for row in rows:
            self._release(row)
            self.uow.session.execute(update(sessions).where(sessions.c.id==row['id']).values(state='expired'))
        return len(rows)

    def complete(self, actor, identifier):
        row = self._get(actor, identifier)
        if row['state'] == 'complete':
            return self.artifacts.get(actor, row['version_id'])
        self._active(row)
        request = row['request']
        count = math.ceil(request['size_bytes']/request['part_bytes'])
        if set(row['parts']) != {str(i) for i in range(1, count+1)}:
            raise Conflict('UPLOAD_INCOMPLETE', 'Upload has missing parts')
        digest, size = hashlib.sha256(), 0
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.store.root/'staging', delete=False) as output:
                path = Path(output.name)
                for number in range(1, count+1):
                    part = dict(row['parts'][str(number)])
                    part['version_id'] = UUID(part['version_id'])
                    with self.store.open(BlobRef(**part)) as stream:
                        for chunk in iter(lambda: stream.read(1024*1024), b''):
                            size += len(chunk)
                            if size > request['size_bytes']:
                                raise Conflict('UPLOAD_DIGEST_MISMATCH', 'Final content differs from declaration')
                            digest.update(chunk)
                            output.write(chunk)
            if size != request['size_bytes'] or digest.hexdigest() != request['sha256']:
                raise Conflict('UPLOAD_DIGEST_MISMATCH', 'Final content differs from declaration')
            # A stable final version survives rollback/retry without changing accepted identities.
            ref = BlobRef(request['sha256'], size, request['media_type'], identifier)
            self._release(row)
            result = self.artifacts.register_upload(actor, row['resource_id'], StagedBlob(ref, path),
                filename=request['filename'], metadata=request['metadata'],
                previous_version_id=UUID(request['previous_version_id']) if request['previous_version_id'] else None,
                expected_resource_revision=request['expected_resource_revision'])
            self.uow.session.execute(update(sessions).where(sessions.c.id==identifier)
                .values(state='complete', version_id=result.version_id))
            return result
        finally:
            if path:
                path.unlink(missing_ok=True)
