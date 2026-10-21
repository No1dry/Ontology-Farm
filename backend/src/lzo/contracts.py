"""Stable cross-module service contracts."""
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Generic, Literal, Protocol, TypeVar
from uuid import UUID

from sqlalchemy.orm import Session

JsonObject = dict[str, Any]
JobState = Literal["queued", "running", "paused", "failed", "succeeded", "cancelled"]
T = TypeVar("T")


@dataclass(frozen=True)
class ActorContext:
    subject_id: UUID
    request_id: str
    authorization_version: int


@dataclass(frozen=True)
class ResourceScope:
    resource_id: UUID | None
    owner_scope_id: UUID | None
    related_scope_ids: tuple[UUID, ...]
    security_class: str


@dataclass(frozen=True)
class RevisionRef:
    resource_id: UUID
    revision: int
    event_id: UUID


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    size_bytes: int
    media_type: str
    version_id: UUID


@dataclass(frozen=True)
class ExecutionBundle:
    id: UUID
    schema_release_id: UUID
    mapping_version_id: UUID | None
    authority_version_id: UUID
    ruling_policy_version_id: UUID
    inference_version_id: UUID
    shapes_digest: str


@dataclass(frozen=True)
class JobRef:
    id: UUID
    status: JobState


@dataclass(frozen=True)
class Page(Generic[T]):
    items: list[T]
    next_cursor: str | None


class UnitOfWork(Protocol):
    session: Session

    def __enter__(self) -> "UnitOfWork": ...
    def __exit__(self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any) -> None: ...
    def commit(self) -> None: ...
