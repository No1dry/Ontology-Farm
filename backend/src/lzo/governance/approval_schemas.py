"""Public immutable approval contracts and HTTP command descriptors."""
from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from lzo.contracts import JsonObject


class ApprovalRef(BaseModel):
    model_config = ConfigDict(frozen=True)
    id: UUID
    submitter_id: UUID
    reviewer_id: UUID | None
    risk: Literal["low", "high"]
    change_digest: str
    baseline: dict[str, int]


class ApprovalDetail(ApprovalRef):
    command: JsonObject
    policy_version: int
    system_policy_version: int | Literal["ordinary-import-v1"]
    kind: str
    target_ids: list[UUID]
    impact: JsonObject
    reasons: list[str]
    revision: int
    status: Literal["pending", "approved", "rejected"]
    comment: str | None
    submitted_at: datetime
    decided_at: datetime | None


class ApprovalWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(min_length=1, max_length=128)
    target_ids: list[UUID] = Field(min_length=1, max_length=200)
    command: JsonObject
    change_digest: str = Field(pattern="^[0-9a-f]{64}$")
    baseline: dict[str, int]


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]
    comment: str = Field(min_length=1, max_length=4000)
