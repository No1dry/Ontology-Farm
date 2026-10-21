"""Typed artifact HTTP and service contracts."""
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ArtifactLinkWrite(BaseModel):
    target_resource_id: UUID
    relation: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z][A-Za-z0-9._-]*$")


class ArtifactReviewWrite(BaseModel):
    decision: Literal["accepted", "rejected", "needs_changes"]
    comment: str = Field(max_length=2000)


class ArtifactLinkDetail(BaseModel):
    id: UUID
    target_resource_id: UUID
    relation: str
    actor_id: UUID
    event_id: UUID
    recorded_at: datetime


class ArtifactReviewDetail(BaseModel):
    id: UUID
    decision: str
    comment: str
    actor_id: UUID
    event_id: UUID
    recorded_at: datetime


class ArtifactDetail(BaseModel):
    version_id: UUID
    resource_id: UUID
    previous_version_id: UUID | None
    version_number: int
    revision: int
    sha256: str
    size_bytes: int
    media_type: str
    filename: str
    metadata: dict[str, Any]
    uploaded_by: UUID
    recorded_at: datetime
    links: list[ArtifactLinkDetail] = Field(default_factory=list)
    reviews: list[ArtifactReviewDetail] = Field(default_factory=list)


class ArtifactMutationDetail(BaseModel):
    id: UUID
    version_id: UUID
    revision: int
    event_id: UUID


class ApiErrorDetail(BaseModel):
    code: str
    message: str
    request_id: str
    details: Any | None
