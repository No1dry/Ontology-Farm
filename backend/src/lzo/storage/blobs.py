"""Explicit private blob providers; no remote-to-local fallback."""
import re
from pathlib import Path
from typing import Protocol, BinaryIO, AsyncIterable

from lzo.artifacts.store import ArtifactStore, StagedBlob
from lzo.contracts import BlobRef
from lzo.errors import ApiError


def upload_budget(settings):
    return max([settings.artifact_max_upload_bytes, *getattr(settings, 'artifact_resource_budgets', {}).values()])


class BlobStore(Protocol):
    @property
    def staging_directory(self) -> Path: ...
    def normalize_media_type(self, media_type: str) -> str: ...
    def put(self, stream: BinaryIO, media_type: str) -> BlobRef: ...
    async def stage_async(self, chunks: AsyncIterable[bytes], media_type: str) -> StagedBlob: ...
    def complete(self, staged: StagedBlob) -> BlobRef: ...
    def discard(self, staged: StagedBlob) -> None: ...
    def open(self, blob: BlobRef) -> BinaryIO: ...
    def open_range(self, blob: BlobRef, start: int, end: int) -> BinaryIO: ...


class LocalBlobStore(ArtifactStore):
    def __init__(self, settings):
        super().__init__(settings.file_root / 'artifacts', max_upload_bytes=upload_budget(settings))
        self.legacy = ArtifactStore(settings.file_root, max_upload_bytes=settings.artifact_max_upload_bytes)

    def open(self, blob):
        try:
            return super().open(blob)
        except ApiError as error:
            if error.code != 'BLOB_MISSING':
                raise
            return self.legacy.open(blob)


def get_blob_store(settings) -> BlobStore:
    if settings.blob_provider == 'local':
        return LocalBlobStore(settings)
    if settings.blob_provider == 's3':
        from lzo.storage.s3 import S3BlobStore
        return S3BlobStore(settings)
    raise ValueError('Explicit local or s3 blob provider required')


def parse_range(header: str | None, size: int):
    if header is None:
        return None
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header)
    if match:
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        elif last and int(last) > 0:
            start, end = max(0, size - int(last)), size - 1
        else:
            start, end = size, -1
        if 0 <= start <= end < size:
            return start, end
    raise ApiError('INVALID_RANGE', 416, 'Requested range is not satisfiable', {'size_bytes': size})
