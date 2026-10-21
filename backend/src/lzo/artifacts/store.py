"""Content-addressed private file store with bounded streaming writes."""
import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
import re
import tempfile
from typing import AsyncIterable, BinaryIO, Iterable
from uuid import uuid4

from lzo.contracts import BlobRef
from lzo.errors import ApiError

_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_MEDIA = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*\Z")
_CHUNK = 1024 * 1024


@dataclass(frozen=True)
class StagedBlob:
    ref: BlobRef
    path: Path


class ArtifactStore:
    def __init__(self, root: Path, *, max_upload_bytes: int):
        self.root = Path(root)
        self.max_upload_bytes = max_upload_bytes
        if max_upload_bytes < 1:
            raise ValueError("max_upload_bytes must be positive")
        (self.root / "staging").mkdir(parents=True, exist_ok=True)
        (self.root / "objects").mkdir(parents=True, exist_ok=True)

    def _media(self, media_type: str) -> str:
        normalized = media_type.lower().strip()
        if not _MEDIA.fullmatch(normalized):
            raise ApiError("INVALID_MEDIA_TYPE", 422, "Invalid artifact media type")
        return normalized

    def _path(self, digest: str) -> Path:
        if not _DIGEST.fullmatch(digest):
            raise ApiError("INVALID_BLOB_REF", 422, "Invalid artifact reference")
        return self.root / "objects" / digest[:2] / digest

    def _finish(self, staged: Path, ref: BlobRef) -> BlobRef:
        digest, size = ref.sha256, ref.size_bytes
        destination = self._path(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.link(staged, destination)
        except FileExistsError:
            if destination.is_symlink() or destination.stat().st_size != size:
                raise ApiError("BLOB_CORRUPT", 503, "Stored artifact failed validation") from None
        finally:
            staged.unlink(missing_ok=True)
        return ref

    def _write(self, chunks: Iterable[bytes], media_type: str) -> BlobRef:
        media = self._media(media_type)
        digest = hashlib.sha256()
        size = 0
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root / "staging", delete=False) as output:
                path = Path(output.name)
                for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ApiError("INVALID_UPLOAD", 422, "Artifact stream must contain bytes")
                    for start in range(0, len(chunk), _CHUNK):
                        part = chunk[start:start + _CHUNK]
                        size += len(part)
                        if size > self.max_upload_bytes:
                            raise ApiError("UPLOAD_TOO_LARGE", 413, "Artifact exceeds upload limit")
                        output.write(part)
                        digest.update(part)
                if size == 0:
                    raise ApiError("EMPTY_UPLOAD", 422, "Artifact is empty")
                output.flush()
                os.fsync(output.fileno())
            return self._finish(path, BlobRef(digest.hexdigest(), size, media, uuid4()))
        finally:
            if path is not None:
                path.unlink(missing_ok=True)

    def put(self, stream: BinaryIO, media_type: str) -> BlobRef:
        return self._write(iter(lambda: stream.read(_CHUNK), b""), media_type)

    async def stage_async(self, chunks: AsyncIterable[bytes], media_type: str) -> StagedBlob:
        media = self._media(media_type)
        digest = hashlib.sha256()
        size = 0
        path = None
        try:
            with tempfile.NamedTemporaryFile(dir=self.root / "staging", delete=False) as output:
                path = Path(output.name)
                async for chunk in chunks:
                    if not isinstance(chunk, bytes):
                        raise ApiError("INVALID_UPLOAD", 422, "Artifact stream must contain bytes")
                    for start in range(0, len(chunk), _CHUNK):
                        part = chunk[start:start + _CHUNK]
                        size += len(part)
                        if size > self.max_upload_bytes:
                            raise ApiError("UPLOAD_TOO_LARGE", 413, "Artifact exceeds upload limit")
                        output.write(part)
                        digest.update(part)
                if size == 0:
                    raise ApiError("EMPTY_UPLOAD", 422, "Artifact is empty")
                output.flush()
                os.fsync(output.fileno())
            return StagedBlob(BlobRef(digest.hexdigest(), size, media, uuid4()), path)
        except BaseException:
            if path is not None:
                path.unlink(missing_ok=True)
            raise

    def complete(self, staged: StagedBlob) -> BlobRef:
        return self._finish(staged.path, staged.ref)

    def discard(self, staged: StagedBlob) -> None:
        staged.path.unlink(missing_ok=True)

    async def put_async(self, chunks: AsyncIterable[bytes], media_type: str) -> BlobRef:
        staged = await self.stage_async(chunks, media_type)
        try:
            return self.complete(staged)
        finally:
            self.discard(staged)

    def open(self, blob: BlobRef) -> BinaryIO:
        path = self._path(blob.sha256)
        if path.is_symlink():
            raise ApiError("BLOB_CORRUPT", 503, "Stored artifact failed validation")
        try:
            stream = path.open("rb")
        except FileNotFoundError:
            raise ApiError("BLOB_MISSING", 503, "Stored artifact is unavailable") from None
        if os.fstat(stream.fileno()).st_size != blob.size_bytes:
            stream.close()
            raise ApiError("BLOB_CORRUPT", 503, "Stored artifact failed validation")
        return stream
