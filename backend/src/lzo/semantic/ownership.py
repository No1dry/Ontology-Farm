"""Future ownership transfer boundary.

Implementations must preserve UUID, IRI, source bindings and immutable history.
Every retained, revoked or added grant must appear explicitly in grant_delta.
No transfer endpoint is exposed until its governed implementation exists.
"""
from typing import Literal, Protocol
from uuid import UUID
from lzo.contracts import ActorContext, UnitOfWork
from lzo.semantic.schemas import Model, WriteReceipt


class TransferGrantChange(Model):
    action: Literal["retain", "revoke", "add"]
    grant_id: UUID | None
    principal_id: UUID
    operation: str
    scope_id: UUID


class TransferProposal(Model):
    resource_id: UUID
    expected_revision: int
    old_owner_scope_id: UUID
    new_owner_scope_id: UUID
    grant_delta: tuple[TransferGrantChange, ...]
    impact_digest: str
    baseline: dict[str, int]


class OwnershipTransferPort(Protocol):
    def preview(self, actor: ActorContext, resource_id: UUID, new_owner_scope_id: UUID,
                expected_revision: int) -> TransferProposal: ...
    def execute(self, uow: UnitOfWork, actor: ActorContext, proposal: TransferProposal,
                approval_id: UUID) -> WriteReceipt: ...
