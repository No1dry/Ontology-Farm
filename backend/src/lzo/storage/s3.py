"""Private S3 immutable content with bounded, seekable local materialization."""
import hashlib
import tempfile
import io
from pathlib import Path

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from lzo.artifacts.store import ArtifactStore
from lzo.errors import ApiError


class _MaterializedStream(io.BufferedReader):
    """Read-only named file suitable for SQLite/GDAL, removed when closed."""
    def __init__(self, path):
        self.path = Path(path)
        super().__init__(self.path.open('rb'))

    def close(self):
        try:
            super().close()
        finally:
            self.path.unlink(missing_ok=True)


class S3BlobStore(ArtifactStore):
    def __init__(self, settings):
        from lzo.storage.blobs import upload_budget
        super().__init__(settings.blob_staging_path, max_upload_bytes=upload_budget(settings))
        self.bucket = settings.s3_bucket
        self.client = boto3.client('s3', endpoint_url=settings.s3_endpoint,
            region_name=settings.s3_region,
            aws_access_key_id=settings.s3_access_key_file.read_text().strip(),
            aws_secret_access_key=settings.s3_secret_key_file.read_text().strip(),
            config=Config(signature_version='s3v4', connect_timeout=5, read_timeout=60,
                          retries={'max_attempts': 2}, s3={'addressing_style': 'path'}))

    def key(self, blob):
        self._path(blob.sha256)  # validate untrusted digest before constructing a key
        return f'objects/{blob.sha256[:2]}/{blob.sha256}'

    def ready(self):
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except (BotoCoreError, ClientError):
            raise ApiError('BLOB_UNAVAILABLE', 503, 'Private object store unavailable') from None

    def _finish(self, staged, ref):
        try:
            digest = hashlib.sha256()
            with staged.open('rb') as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                    digest.update(chunk)
                if stream.tell() != ref.size_bytes or digest.hexdigest() != ref.sha256:
                    raise ApiError('BLOB_CORRUPT', 503, 'Staged artifact failed validation')
                stream.seek(0)
                try:
                    self.client.put_object(Bucket=self.bucket, Key=self.key(ref), Body=stream,
                        ContentLength=ref.size_bytes, ContentType=ref.media_type, IfNoneMatch='*')
                except ClientError as error:
                    if error.response['ResponseMetadata']['HTTPStatusCode'] not in (409, 412):
                        raise
            # Verify the accepted bytes, including deduplicated objects. Never repair by overwriting.
            with self.open(ref):
                pass
            return ref
        except (BotoCoreError, ClientError, OSError):
            raise ApiError('BLOB_UNAVAILABLE', 503, 'Private object store unavailable') from None
        finally:
            staged.unlink(missing_ok=True)

    def open(self, blob):
        output = tempfile.NamedTemporaryFile(dir=self.root / 'staging', delete=False)
        body = None
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self.key(blob))
            body = response['Body']
            if response['ContentLength'] != blob.size_bytes:
                raise ApiError('BLOB_CORRUPT', 503, 'Stored artifact failed validation')
            digest, size = hashlib.sha256(), 0
            for chunk in iter(lambda: body.read(1024 * 1024), b''):
                size += len(chunk)
                if size > blob.size_bytes:
                    raise ApiError('BLOB_CORRUPT', 503, 'Stored artifact failed validation')
                output.write(chunk)
                digest.update(chunk)
            if size != blob.size_bytes or digest.hexdigest() != blob.sha256:
                raise ApiError('BLOB_CORRUPT', 503, 'Stored artifact failed validation')
            output.close()
            return _MaterializedStream(output.name)
        except BaseException as error:
            output.close()
            Path(output.name).unlink(missing_ok=True)
            if isinstance(error, (BotoCoreError, ClientError, OSError)):
                raise ApiError('BLOB_UNAVAILABLE', 503, 'Private object store unavailable') from None
            raise
        finally:
            if body is not None:
                body.close()
