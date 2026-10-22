"""Immutable, canonically hashed cross-store messages and observable receipts."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Literal
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from lzo.governance.risk import digest_change
from lzo.semantic.schemas import DataNature

class SyncEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    event_id: UUID = Field(default_factory=uuid4)
    entity_id: UUID
    aggregate_revision: int = Field(ge=1)
    target: Literal["gis", "timeseries", "catalog"]
    operation: str = Field(min_length=1)
    payload_json: str = Field(alias="payload", repr=False)
    schema_version: int = Field(default=1, ge=1)
    source_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    data_nature: DataNature
    recorded_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    observed_at: datetime | None = None
    received_at: datetime | None = None
    clock_basis: str | None = None
    source_cursor: str | None = None
    mapping_version: str | None = None
    rule_version: str | None = None

    @model_validator(mode="before")
    @classmethod
    def canonical_payload(cls, values):
        values = dict(values)
        if "payload" in values:
            values["payload"] = json.dumps(values["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        return values

    @model_validator(mode="after")
    def aware_times(self):
        for value in (self.recorded_at, self.observed_at, self.received_at):
            if value is not None and value.utcoffset() is None: raise ValueError("Envelope timestamps require timezone")
        return self

    @property
    def payload(self):
        return json.loads(self.payload_json)

    @property
    def payload_hash(self):
        return digest_change(self.payload)

    def wire(self):
        value = self.model_dump(mode="json", exclude={"payload_json"})
        value["payload"] = self.payload
        return value

    @property
    def envelope_hash(self):
        return digest_change(self.wire())

class DomainReceipt(BaseModel):
    model_config = ConfigDict(frozen=True)
    event_id: UUID
    target: str
    payload_hash: str
    aggregate_revision: int
    status: Literal["pending", "retry", "failed", "applied", "superseded"]
    attempts: int = 0
    error: str | None = None

class DeliveryBatchResult(BaseModel):
    delivered: int = 0
    failed: int = 0
