"""Catalog commands preserve model, physical entity and artifact identities."""
from datetime import date, datetime
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator, model_serializer
from lzo.contracts import ResourceScope, RevisionRef
from lzo.semantic.rdf import iri


class DTO(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True)

    @model_serializer(mode='wrap')
    def compatible_payload(self, handler):
        value = handler(self)
        for key in ('hardware_version', 'equipment_category', 'author', 'organization',
                    'agricultural_scenario', 'language', 'manual_summary_preparer'):
            if value.get(key) is None:
                value.pop(key, None)
        return value


class Capability(DTO):
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(default='', max_length=4000)
    state: Literal['draft', 'verified'] = 'draft'
    evidence_version_ids: tuple[UUID, ...] = Field(default=(), max_length=64)


class ModelMetadata(DTO):
    manufacturer: str | None = Field(default=None, max_length=200)
    exact_model: str = Field(min_length=1, max_length=200)
    firmware: str | None = Field(default=None, max_length=200)
    hardware_version: str | None = Field(default=None, max_length=200)
    equipment_category: str | None = Field(default=None, max_length=200)
    applicability: str | None = Field(default=None, max_length=4000)
    capabilities: tuple[Capability, ...] = Field(default=(), max_length=100)
    protocol_profiles: list[str] | None = Field(default=None, max_length=100)
    offline_constraints: str | None = Field(default=None, max_length=4000)
    evidence_version_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    attachment_version_ids: tuple[UUID, ...] = Field(default=(), max_length=64)
    state: Literal['draft', 'verified', 'retired'] = 'draft'
    synthetic: bool = False
    summary: str = Field(default='', max_length=8000)


class DocumentMetadata(DTO):
    title: str = Field(min_length=1, max_length=300)
    document_type: Literal['manual', 'reference', 'ontology', 'standard', 'summary']
    artifact_version_id: UUID
    source_uri: str = Field(min_length=1, max_length=2000)
    source_version: str = Field(min_length=1, max_length=200)
    license: str = Field(min_length=1, max_length=2000)
    publication_date: date | None = None
    author: str | None = Field(default=None, max_length=500)
    organization: str | None = Field(default=None, max_length=500)
    agricultural_scenario: str | None = Field(default=None, max_length=2000)
    language: str | None = Field(default=None, max_length=100)
    manual_summary_preparer: str | None = Field(default=None, max_length=500)
    model_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    entity_ids: tuple[UUID, ...] = Field(default=(), max_length=100)
    entity_iris: tuple[str, ...] = Field(default=(), max_length=100)
    term_iris: tuple[str, ...] = Field(default=(), max_length=100)
    summary: str = Field(default='', max_length=8000)
    state: Literal['draft', 'verified', 'retired'] = 'draft'

    @model_validator(mode='after')
    def valid_iris(self):
        iri(self.source_uri)
        for value in (*self.term_iris, *self.entity_iris):
            iri(value)
        return self


class CreateModel(DTO):
    scope: ResourceScope
    source_identity: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: ModelMetadata


class CreateDocument(DTO):
    scope: ResourceScope
    source_identity: str | None = Field(default=None, min_length=1, max_length=500)
    metadata: DocumentMetadata


class VersionModel(DTO):
    expected_revision: int = Field(ge=1)
    metadata: ModelMetadata
    approval_id: UUID | None = None


class VersionDocument(DTO):
    expected_revision: int = Field(ge=1)
    metadata: DocumentMetadata
    approval_id: UUID | None = None


class CatalogRetry(DTO):
    expected_revision: int = Field(ge=1)
    accepted_revision: RevisionRef


class CatalogRetryReceipt(DTO):
    accepted_revision: RevisionRef
    audit: RevisionRef
    status: Literal['pending'] = 'pending'


class CatalogView(DTO):
    id: UUID
    kind: Literal['model', 'document']
    revision: RevisionRef
    metadata: ModelMetadata | DocumentMetadata
    sha256: str | None = None
    recorded_at: datetime
    projection_status: Literal['current', 'pending', 'failed', 'unavailable']


class DeviceBinding(DTO):
    entity_id: UUID
    expected_revision: int = Field(ge=0)


class DeviceModelView(DTO):
    entity_id: UUID
    model_id: UUID
    revision: RevisionRef


class CatalogPage(DTO):
    items: list[CatalogView]
    next_cursor: str | None = None
    projection_status: Literal['current', 'pending', 'failed', 'unavailable']
