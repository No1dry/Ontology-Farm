"""Dynamic objects and provenance DTOs; no caller-controlled authority flags."""
from datetime import datetime, timezone
from typing import Literal, Protocol
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from lzo.contracts import ActorContext, BlobRef, ExecutionBundle, JsonObject, RevisionRef, UnitOfWork
from lzo.semantic.rdf import RdfTerm, iri
from lzo.semantic.validation import ValidationReport

DataNature = Literal["synthetic", "test", "imported_unverified", "verified"]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceSupport(Model):
    support_id: UUID
    source_id: UUID | None = None
    record_key: str | None = None
    source_version: str | None = None
    snapshot: BlobRef | None = None
    raw_value: JsonObject
    mapping_version_id: UUID | None = None
    evidence_refs: tuple[RevisionRef, ...] = ()
    observed_at: datetime | None = None

    @field_validator("observed_at")
    @classmethod
    def utc_time(cls, value):
        if value is not None:
            if value.tzinfo is None:
                raise ValueError("Timezone is required")
            return value.astimezone(timezone.utc)
        return value


class AssertionInput(Model):
    assertion_id: UUID
    resource_id: UUID
    predicate_iri: str
    object: RdfTerm
    state: Literal["candidate", "accepted"]
    data_nature: DataNature
    valid_from: datetime
    valid_to: datetime | None = None
    support: SourceSupport

    @field_validator("predicate_iri")
    @classmethod
    def valid_iri(cls, value):
        iri(value)
        return value

    @field_validator("valid_from", "valid_to")
    @classmethod
    def utc_time(cls, value):
        return SourceSupport.utc_time(value)

    @model_validator(mode="after")
    def interval(self):
        if self.valid_to is not None and self.valid_to <= self.valid_from:
            raise ValueError("valid_to must follow valid_from")
        return self


class CreateObjectInput(Model):
    resource_id: UUID
    class_iris: tuple[str, ...] = Field(min_length=1)
    display_name: str = Field(min_length=1, max_length=512)
    owner_scope_id: UUID
    related_scope_ids: tuple[UUID, ...] = ()
    state: Literal["draft", "active"]
    data_nature: DataNature
    assertions: tuple[AssertionInput, ...] = ()
    decision_ref: RevisionRef | None = None

    @model_validator(mode="after")
    def subjects(self):
        if any(a.resource_id != self.resource_id for a in self.assertions):
            raise ValueError("Assertion subject differs from object")
        if len(set(self.class_iris)) != len(self.class_iris):
            raise ValueError("Duplicate class")
        return self


class FactPatch(Model):
    resource_id: UUID
    expected_revision: int = Field(ge=1)
    additions: tuple[AssertionInput, ...] = ()
    withdraw_support_ids: tuple[UUID, ...] = ()
    decision_ref: RevisionRef | None = None

    @model_validator(mode="after")
    def subjects(self):
        if any(a.resource_id != self.resource_id for a in self.additions):
            raise ValueError("Assertion subject differs from patch")
        return self


class WriteReceipt(Model):
    revision: RevisionRef
    assertion_ids: tuple[UUID, ...]
    projection_state: Literal["pending", "synced", "failed"]


class GeometryInput(Model):
    resource_id: UUID
    expected_revision: int = Field(ge=1)
    geojson: JsonObject
    source_crs: str = Field(min_length=1)
    source_support: SourceSupport
    decision_ref: RevisionRef | None = None


class RowGeometry(Model):
    geojson: JsonObject
    source_crs: str = Field(min_length=1)
    source_support: SourceSupport
    valid_from: datetime
    valid_to: datetime | None = None

    @field_validator("valid_from", "valid_to")
    @classmethod
    def utc_time(cls, value):
        return SourceSupport.utc_time(value)

    @model_validator(mode="after")
    def interval(self):
        if self.valid_to is not None and self.valid_to<=self.valid_from:
            raise ValueError("Invalid geometry interval")
        return self


class SourceRowInput(Model):
    """One finite object create OR exact-head patch with source geometry."""
    create: CreateObjectInput | None = None
    patch: FactPatch | None = None
    geometry_action: Literal["keep", "replace", "withdraw"] = "keep"
    geometry: RowGeometry | None = None
    withdraw_geometry_support_ids: tuple[UUID, ...] = ()
    decision_ref: RevisionRef | None = None

    @model_validator(mode="after")
    def finite_effect(self):
        if (self.create is None)==(self.patch is None):raise ValueError("One create or patch is required")
        if (self.geometry_action=="replace")!=(self.geometry is not None):raise ValueError("Explicit replacement geometry required")
        if self.geometry_action=="keep" and self.withdraw_geometry_support_ids:raise ValueError("Keep cannot withdraw geometry")
        if (self.create or self.patch).decision_ref is not None:raise ValueError("Decision belongs to the composite row")
        return self


class ObjectView(Model):
    id: UUID
    iri: str
    revision: int
    class_iris: tuple[str, ...]
    display_name: str
    state: str
    data_nature: DataNature
    owner_scope_id: UUID
    related_scope_ids: tuple[UUID, ...]
    properties: dict[str, tuple[RdfTerm, ...]]
    projection_state: str
    bundle_id: UUID
    validation_state: Literal["conforms", "needs_review", "pending"]
    validation_report: ValidationReport | None
    candidate_validation_report: ValidationReport | None
    evaluation_time: datetime
    next_transition_at: datetime | None


class AssertionView(Model):
    id: UUID
    resource_id: UUID
    predicate_iri: str
    object: RdfTerm
    state: str
    data_nature: DataNature
    valid_from: datetime
    valid_to: datetime | None
    supports: tuple[SourceSupport, ...]
    decision_ref: RevisionRef | None
    revision: RevisionRef
    bundle_id: UUID


class LinkView(Model):
    id: UUID
    source_id: UUID
    predicate_iri: str
    target_id: UUID
    assertion: AssertionView


class GeoFeatureCollection(Model):
    type: Literal["FeatureCollection"] = "FeatureCollection"
    features: list[JsonObject]
    projection_watermark: int
    next_cursor: str | None


class CreateObjectRequest(Model):
    create: CreateObjectInput
    bundle_id: UUID


class UpdateObjectRequest(Model):
    patch: FactPatch
    bundle_id: UUID


class CreateLinkRequest(Model):
    source_id: UUID
    target_id: UUID
    predicate_iri: str
    assertion_id: UUID
    state: Literal["candidate", "accepted"]
    data_nature: DataNature
    valid_from: datetime
    valid_to: datetime | None = None
    support: SourceSupport
    decision_ref: RevisionRef | None = None
    bundle_id: UUID


class WithdrawLinkRequest(Model):
    support_ids: tuple[UUID, ...]
    decision_ref: RevisionRef | None = None
    bundle_id: UUID


class DeactivateObjectRequest(Model):
    reason: str = Field(min_length=1, max_length=4000)
    approval_id: UUID | None = None


class GeometryWriteRequest(Model):
    geometry: GeometryInput
    bundle_id: UUID


class ValidateFactsRequest(Model):
    bundle_id: UUID
    facts: tuple[AssertionInput, ...] | None = None
    create: CreateObjectInput | None = None

    @model_validator(mode="after")
    def exclusive(self):
        if (self.facts is None) == (self.create is None):
            raise ValueError("Exactly one of facts and create is required")
        return self


class DecisionEvidence(Model):
    """Trusted verifier result; exact immutable evidence, never an acceptance boolean."""
    references: tuple[RevisionRef, ...]
    type_references: tuple[RevisionRef, ...] | None = None


class AcceptedDecisionPort(Protocol):
    """I02 deployment adapter; absent implementation denies external acceptance.

    Verify exact effect, subject/revision, immutable bundle/evidence and current
    authority, independently reviewed/rule decision, and current actor rights.
    The effect includes additions, withdrawals or original geometry, never only
    a caller-provided digest. Implementations do not commit.
    """
    def verify(self, uow: UnitOfWork, actor: ActorContext, decision_ref: RevisionRef,
               effect: JsonObject, bundle: ExecutionBundle) -> DecisionEvidence: ...


class AuthorityPolicyPort(Protocol):
    """Version-aware I02 authority resolution takes precedence over genesis."""
    def resolve(self, uow: UnitOfWork, actor: ActorContext, predicate_iri: str,
                bundle: ExecutionBundle) -> str: ...
