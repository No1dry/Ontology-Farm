"""Reserved reconstruction contract; no first-release route or adapter."""
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from lzo.contracts import ActorContext, BlobRef


@dataclass(frozen=True)
class HistoricalStateRequest:
    resource_ids: tuple[UUID, ...]
    recorded_at: datetime
    effective_at: datetime | None


@dataclass(frozen=True)
class HistorySnapshotRef:
    id: UUID
    ledger_watermark: int
    artifact: BlobRef


class HistoryReconstructionPort(Protocol):
    def reconstruct(self, actor: ActorContext, request: HistoricalStateRequest) -> HistorySnapshotRef: ...
