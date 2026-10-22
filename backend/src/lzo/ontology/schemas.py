"""Public S02 DTOs; revision preconditions are supplied by HTTP If-Match."""
from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_serializer
from lzo.contracts import BlobRef, JsonObject, RevisionRef
from lzo.semantic.rdf import RdfTerm, Triple, iri
from lzo.semantic.validation import ValidationIssue


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassDef(Model):
    iri: str
    label: str
    description: str = ""
    parents: tuple[str, ...] = ()
    deprecated: bool = False


class PropertyDef(Model):
    iri: str
    label: str
    datatype_iri: str
    unit: str | None = None
    owner_policy_iri: str
    enum_values: tuple[RdfTerm, ...] = ()
    superproperties: tuple[str, ...] = ()


class RelationDef(Model):
    iri: str
    label: str
    domain_iris: tuple[str, ...] = ()
    range_iris: tuple[str, ...] = ()
    superproperties: tuple[str, ...] = ()
    inverse_iri: str | None = None
    symmetric: bool = False
    transitive: bool = False


class SchemaDocument(Model):
    classes: tuple[ClassDef, ...] = ()
    properties: tuple[PropertyDef, ...] = ()
    relations: tuple[RelationDef, ...] = ()
    shapes: str = ""
    ui_metadata: JsonObject = Field(default_factory=dict)
    dependencies: tuple[BlobRef, ...] = ()
    reference_versions: tuple[RevisionRef, ...] = Field(default_factory=tuple)

    @model_serializer(mode='wrap')
    def preserve_existing_document_digest(self, handler):
        value = handler(self)
        if not self.reference_versions:
            value.pop('reference_versions', None)
        return value


class CurrentSchemaBaseline(Model):
    # The enclosing null means unavailable; this explicit null means no active release.
    active_release_id: UUID | None


class SchemaDraftView(Model):
    id: UUID
    revision: int
    base_release_id: UUID | None
    document: SchemaDocument
    state: str
    document_digest: str | None = None
    current_baseline: CurrentSchemaBaseline | None = None


class SchemaReleaseView(Model):
    id: UUID
    revision: int
    version: str
    state: str
    document: SchemaDocument
    artifacts: tuple[BlobRef, ...]


class ImpactReport(Model):
    digest: str
    baseline: dict[str, int]
    affected_ids: tuple[UUID, ...]
    violations: tuple[ValidationIssue, ...]
    form_diff: JsonObject
    permission_diff: JsonObject
    migration_required: bool
    # Older immutable release snapshots predate preview classification.
    risk: Literal["low", "high"] | None = None
    reasons: tuple[str, ...] = ()
    active_release_id: UUID | None = None


class SchemaDiffView(Model):
    base_release_id: UUID
    target_release_id: UUID
    changes: JsonObject
    impact: ImpactReport


class FormField(Model):
    iri: str
    label: str
    datatype_iri: str | None
    reference_classes: tuple[str, ...]
    required: bool
    min_count: int
    max_count: int | None
    unit: str | None
    enum_values: tuple[RdfTerm, ...]
    read_only: bool
    widget: str


class FormSchemaView(Model):
    class_iri: str
    release_id: UUID | None
    fields: tuple[FormField, ...]
    inherited_from: dict[str, tuple[str, ...]]


class InheritanceView(Model):
    class_iri: str
    ancestors: tuple[str, ...]
    fields: tuple[FormField, ...]
    conflicts: tuple[str, ...]


class CreateDraftRequest(Model):
    base_release_id: UUID | None = None
    document: SchemaDocument


class UpdateDraftRequest(Model):
    document: SchemaDocument


class SubmitDraftRequest(Model):
    impact_digest: str


class PublishReleaseRequest(Model):
    draft_id: UUID
    approval_id: UUID


class SemanticDraftImportBody(Model):
    blob: BlobRef
    format: Literal["turtle", "json-ld"]
    kind: Literal["schema", "shapes"]
    base_release_id: UUID | None = None
    target_draft_id: UUID | None = None


class SemanticDraftImport(SemanticDraftImportBody):
    expected_revision: int = Field(ge=0)


class SchemaAxiom(Model):
    iri: str
    triple: Triple
    release_id: UUID
