"""Source contracts. Uploaded bytes and source positions are not ledger positions."""
from datetime import datetime
from typing import Literal
from uuid import UUID,uuid4
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, model_serializer, model_validator, SecretStr
from lzo.contracts import BlobRef, JsonObject, ResourceScope, RevisionRef, ExecutionBundle, JobRef
from lzo.semantic.rdf import RdfTerm
from lzo.semantic.schemas import AssertionInput

DataNature = Literal["synthetic", "test", "imported_unverified", "verified"]


class DTO(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SourceKey(DTO):
    source_id: UUID
    source_system: str
    source_instance: str
    collection: str
    canonical_key: str


class SourcePosition(DTO):
    stream_id: str
    epoch: UUID
    ordinal: int
    source_version: str | None
    cursor: JsonObject


class SourceChange(DTO):
    change_id: UUID
    key: SourceKey
    operation: Literal["upsert", "delete"]
    position: SourcePosition
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    observed_at: AwareDatetime | None
    received_at: AwareDatetime
    snapshot: BlobRef
    content_sha256: str
    idempotency_key: str
    raw_fields: JsonObject
    geometry: JsonObject | None
    data_nature: DataNature
    locator: str


class TemporalFields(DTO):
    """Explicit raw record paths. Null values are open bounds/unknown observation;
    missing configured fields are errors. CSV empty cells must use an explicit
    timestamp; an omitted path, rather than an empty string, declares an open bound.
    """
    valid_from_path: str | None = Field(default=None,min_length=1,max_length=512)
    valid_to_path: str | None = Field(default=None,min_length=1,max_length=512)
    observed_at_path: str | None = Field(default=None,min_length=1,max_length=512)


class FileOptions(DTO):
    format: Literal["csv", "xlsx", "json", "geojson"]
    encoding: str = Field(min_length=1, max_length=64)
    delimiter: str = Field(min_length=1, max_length=1)
    sheet: str | None = Field(max_length=128)
    header_row: int = Field(ge=1, le=100000)
    records_path: str = Field(max_length=512)
    key_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    source_crs: str | None = Field(max_length=256)
    source_timezone: str | None = Field(max_length=128)
    data_nature: DataNature
    temporal_fields: TemporalFields | None = None

    @model_serializer(mode="wrap")
    def legacy_canonical(self, handler):
        # Existing upload bindings/config digests are immutable file-v1 bytes.
        value=handler(self)
        if self.temporal_fields is None: value.pop("temporal_fields",None)
        return value


class ParseIssue(DTO):
    locator: str
    field_path: str | None
    code: str
    message: str


class ParsedFile(DTO):
    changes: tuple[SourceChange, ...]
    issues: tuple[ParseIssue, ...]
    complete: bool


class SourceCreate(DTO):
    source_system: str = Field(min_length=1, max_length=128)
    source_instance: str = Field(min_length=1, max_length=128)
    collection: str = Field(min_length=1, max_length=128)
    kind: Literal["file", "postgresql", "mysql"]
    scope: ResourceScope
    data_nature: DataNature


class SourceView(DTO):
    id: UUID = Field(json_schema_extra={"readOnly": True})
    revision: int = Field(json_schema_extra={"readOnly": True})
    configuration: SourceCreate
    mapping_version_id: UUID | None = Field(default=None, json_schema_extra={"readOnly": True})
    connection_revision: int | None = Field(default=None, json_schema_extra={"readOnly": True})
    schedule_revision: int | None = Field(default=None, json_schema_extra={"readOnly": True})
    last_success_at: datetime | None = Field(default=None, json_schema_extra={"readOnly": True})


class SemanticFileOptions(DTO):
    format: Literal["turtle", "json-ld"]
    data_nature: DataNature
    temporal_fields: TemporalFields | None = None
    source_timezone: str | None = None


class UploadBinding(DTO):
    """Internal, server-resolved immutable registration; never an HTTP command."""
    parser_contract: Literal["file-v1", "file-v2", "semantic-v1"] = "file-v1"
    id: UUID
    source_key: SourceKey
    source_revision: int
    blob: BlobRef
    options: FileOptions | SemanticFileOptions
    epoch: UUID
    received_at: AwareDatetime


class EvidenceCreate(DTO):
    source_id: UUID | None
    scope: ResourceScope
    target_refs: tuple[RevisionRef, ...] = Field(max_length=64)
    title: str = Field(min_length=1, max_length=255)
    locator: str | None = Field(max_length=1024)
    data_nature: DataNature
    replaces_id: UUID | None


class EvidenceView(DTO):
    id: UUID = Field(json_schema_extra={"readOnly": True})
    revision: RevisionRef = Field(json_schema_extra={"readOnly": True})
    blob: BlobRef = Field(json_schema_extra={"readOnly": True})
    source_id: UUID | None
    scope: ResourceScope
    target_refs: tuple[RevisionRef, ...]
    title: str
    locator: str | None
    data_nature: DataNature = Field(json_schema_extra={"readOnly": True})
    review_state: Literal["candidate", "accepted", "rejected"] = Field(json_schema_extra={"readOnly": True})
    replaces_id: UUID | None
    created_at: AwareDatetime = Field(json_schema_extra={"readOnly": True})


class RuleExpr(DTO):
    op: Literal["path", "literal", "coalesce", "cast", "unit", "date", "normalize", "eq", "and", "or"]
    args: tuple["RuleExpr", ...] = Field(default=(), max_length=32)
    path: str | None = Field(default=None, max_length=512)
    value: JsonObject | None = None


class SourceCredentialInput(DTO):
    username: str = Field(min_length=1,max_length=255)
    password: SecretStr = Field(min_length=1,max_length=4096,json_schema_extra={"writeOnly":True})
    expected_revision: int = Field(ge=1)


class CredentialRef(DTO):
    ref: str = Field(json_schema_extra={"readOnly":True})
    source_id: UUID = Field(json_schema_extra={"readOnly":True})
    revision: int = Field(json_schema_extra={"readOnly":True})


class ConnectionConfig(DTO):
    source_id: UUID
    expected_revision: int = Field(ge=1)
    host: str = Field(min_length=1,max_length=253,pattern=r'^[a-zA-Z0-9.:_-]+$')
    port: int = Field(ge=1,le=65535)
    database: str = Field(min_length=1,max_length=128)
    schema_name: str | None = Field(default=None,max_length=128)
    table_name: str = Field(min_length=1,max_length=128)
    credential_ref: str
    tls_mode: Literal['verify','internal_trust'] = 'verify'
    key_columns: tuple[str,...] = Field(min_length=1,max_length=16)
    updated_at_column: str | None = None
    deleted_column: str | None = None
    filter_expression: RuleExpr | None = None
    temporal_fields: TemporalFields | None = None
    source_timezone: str | None = None
    overlap_seconds: int = Field(default=60,ge=0,le=86400)


class ConnectionCheck(DTO):
    source_id: UUID = Field(json_schema_extra={"readOnly":True})
    reachable: bool = Field(json_schema_extra={"readOnly":True})
    read_only: bool = Field(json_schema_extra={"readOnly":True})
    incremental_mode: Literal['timestamp_key','snapshot_diff'] = Field(json_schema_extra={"readOnly":True})
    issues: tuple[ParseIssue,...] = Field(json_schema_extra={"readOnly":True})


class ReadRequest(DTO):
    source_id: UUID
    mode: Literal['full','incremental']
    cursor: JsonObject | None = None
    page_size: int = Field(default=200,ge=1,le=1000)
    scope_digest: str = Field(min_length=64,max_length=64)
    upper_fence: JsonObject | None = None


class SourcePage(DTO):
    changes: tuple[SourceChange,...]
    next_cursor: JsonObject | None
    seen_keys: tuple[str,...]
    source_snapshot_token: str
    end_of_scan: bool
    watermark: JsonObject | None = None


class ScanResult(DTO):
    id: UUID
    source_id: UUID
    scope_digest: str
    snapshot_token: str
    state: Literal['running','complete','partial','failed']
    read_count: int
    cursor: JsonObject | None
    last_success_watermark: JsonObject | None
    errors: tuple[ParseIssue,...]


class ScheduleCommand(DTO):
    source_id: UUID
    expected_revision: int = Field(ge=1)
    enabled: bool
    interval_seconds: int = Field(ge=60,le=31536000)


class ScheduleView(DTO):
    source_id: UUID = Field(json_schema_extra={"readOnly":True})
    revision: int = Field(json_schema_extra={"readOnly":True})
    enabled: bool
    interval_seconds: int
    next_run_at: AwareDatetime | None = Field(json_schema_extra={"readOnly":True})


class AlignmentRule(DTO):
    id: UUID
    digest: str
    class_iri: str
    key_paths: tuple[str, ...] = Field(min_length=1, max_length=32)
    normalizers: tuple[RuleExpr, ...] = ()
    scope: ResourceScope
    confirmed: bool


class AuthorityRule(DTO):
    predicate_iri: str
    owner_policy: Literal["ontology_owned", "source_backed", "derived", "read_only"]
    owner_source_ids: tuple[UUID, ...] = ()
    source_priority: tuple[UUID, ...] = ()
    comparable_time_path: str | None = None
    important: bool = True
    digest: str


class RulingPolicyRule(DTO):
    predicate_iris: tuple[str, ...]
    expression: RuleExpr
    always_review: bool
    digest: str


class FieldMap(DTO):
    source_path: str = Field(max_length=512)
    predicate_iri: str
    datatype_iri: str | None = None
    source_unit: str | None = None
    target_unit: str | None = None
    expression: RuleExpr | None = None
    term_kind: Literal["scalar", "rdf", "reference"] = "scalar"
    multiple: bool = False

    @model_serializer(mode="wrap")
    def canonical(self, handler):
        value=handler(self)
        if self.term_kind=="scalar":value.pop("term_kind",None)
        if not self.multiple:value.pop("multiple",None)
        return value


class MappingProfile(DTO):
    id: UUID = Field(default_factory=uuid4,json_schema_extra={"readOnly":True})
    source_id: UUID
    revision: int = Field(default=1,ge=1, json_schema_extra={"readOnly":True})
    class_iri: str
    identity_paths: tuple[str, ...] = Field(max_length=32)
    confirmed_identity_rule_id: UUID | None
    name_path: str = Field(max_length=512)
    fields: tuple[FieldMap, ...] = Field(max_length=256)
    geometry_path: str | None
    source_crs: str | None
    source_timezone: str | None
    digest: str = Field(default="",json_schema_extra={"readOnly":True})


class IdentityMatch(DTO):
    state: Literal["existing", "new", "candidate"]
    resource_id: UUID | None
    matched_rule_id: UUID | None
    binding_revision: int | None
    explanation: str


class DependencyBinding(DTO):
    entity_id: UUID | None
    source_keys: tuple[SourceKey, ...]
    predicate_iris: tuple[str, ...]
    support_ids: tuple[UUID, ...]
    evidence_digests: tuple[str, ...]
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    authority_rule_digest: str
    ruling_rule_digest: str
    schema_dependency_digest: str
    digest: str


class CandidateValue(DTO):
    id: UUID
    entity_id: UUID | None
    predicate_iri: str | None
    raw_value: JsonObject
    normalized_value: RdfTerm | None
    source_key: SourceKey
    snapshot: BlobRef
    locator: str
    evidence_refs: tuple[RevisionRef, ...]
    bundle_id: UUID
    source_version: str | None
    observed_at: AwareDatetime | None
    valid_from: AwareDatetime | None
    valid_to: AwareDatetime | None
    data_nature: DataNature


class ConflictView(DTO):
    id: UUID
    revision: int
    scope: ResourceScope
    entity_id: UUID | None
    predicate_iri: str | None
    candidate_ids: tuple[UUID, ...]
    candidate_values: tuple[CandidateValue, ...]
    dependencies: DependencyBinding
    state: Literal["open", "resolved", "needs_review", "withdrawn"]
    reason: str


class RefreshConflictCommand(DTO):
    expected_revision: int = Field(ge=1)
    ruling_ref: RevisionRef


class ReviewRulingCommand(DTO):
    expected_revision: int = Field(ge=1)
    ruling_ref: RevisionRef


class RulingCommand(DTO):
    expected_revision: int = Field(ge=1)
    chosen_candidate_ids: tuple[UUID, ...]
    rejected_candidate_ids: tuple[UUID, ...]
    rationale: str = Field(min_length=1, max_length=4000)
    approval_id: UUID


class RulingView(DTO):
    id: UUID
    revision: RevisionRef
    conflict_id: UUID
    dependencies: DependencyBinding
    chosen_candidate_ids: tuple[UUID, ...]
    rejected_candidate_ids: tuple[UUID, ...]
    state: Literal["effective", "needs_review", "superseded"]
    rationale: str
    reviewer_id: UUID
    decided_at: AwareDatetime
    replaces_id: UUID | None


class MergePreviewCommand(DTO):
    canonical_id: UUID
    duplicate_ids: tuple[UUID, ...] = Field(min_length=1, max_length=32)


class MergeCommand(DTO):
    proposal_id: UUID
    approval_id: UUID


class MergeProposal(DTO):
    id: UUID
    revision: int
    canonical_id: UUID
    duplicate_ids: tuple[UUID, ...]
    scopes: tuple[ResourceScope, ...]
    baseline: dict[str, int]
    conflict_ids: tuple[UUID, ...]
    evidence_refs: tuple[RevisionRef, ...]
    change_digest: str
    approval_id: UUID
    preserved_candidate_assertion_ids: tuple[UUID,...] = Field(default=(),json_schema_extra={"readOnly":True})
    blocking_reasons: tuple[str,...] = Field(default=(),json_schema_extra={"readOnly":True})
    different_manager_ids: tuple[UUID,...] = Field(default=(),json_schema_extra={"readOnly":True})


class MappedRecord(DTO):
    change: SourceChange
    identity: IdentityMatch
    class_iri: str
    display_name: str
    scope: ResourceScope
    facts: tuple[AssertionInput, ...]
    geometry: JsonObject | None
    conflicts: tuple[ConflictView, ...]
    decision_ref: RevisionRef | None


class ImportAdmissionPolicy(DTO):
    contract: Literal["ordinary-import-v1"] = "ordinary-import-v1"
    effect_classes: tuple[Literal["create","facts","replace","withdraw","geometry","type","relation"], ...] = Field(min_length=1,max_length=7)
    predicate_iris: tuple[str, ...] = Field(max_length=256)
    class_iris: tuple[str, ...] = Field(min_length=1,max_length=256)
    owner_scope_ids: tuple[UUID, ...] = Field(min_length=1,max_length=256)
    max_resources: int = Field(ge=1,le=1000000)
    max_withdrawals: int = Field(ge=0,le=10000000)
    max_rows: int = Field(ge=1,le=1000000)

    @model_validator(mode="after")
    def explicit_coverage(self):
        from lzo.semantic.rdf import iri
        for name in ("effect_classes","predicate_iris","class_iris","owner_scope_ids"):
            values=getattr(self,name)
            if len(set(values))!=len(values):raise ValueError("Duplicate admission coverage")
        for value in (*self.predicate_iris,*self.class_iris):iri(value)
        return self


class RuleVersion(DTO):
    id: UUID = Field(default_factory=uuid4,json_schema_extra={"readOnly":True})
    revision: int = Field(default=1,json_schema_extra={"readOnly":True})
    kind: Literal["mapping", "alignment", "authority", "ruling"]
    scope: ResourceScope
    parent_id: UUID | None
    rules: tuple[RuleExpr, ...] = Field(max_length=256)
    mapping_profile: MappingProfile | None
    alignment_rules: tuple[AlignmentRule, ...] = Field(max_length=256)
    authority_rules: tuple[AuthorityRule, ...] = Field(max_length=256)
    ruling_policies: tuple[RulingPolicyRule, ...] = Field(max_length=256)
    status: Literal["draft", "approved", "published", "deprecated"] = Field(default="draft",json_schema_extra={"readOnly":True})
    digest: str = Field(default="",json_schema_extra={"readOnly":True})
    import_admission: ImportAdmissionPolicy | None = None

    @model_validator(mode="after")
    def admission_kind(self):
        if self.import_admission is not None and self.kind!="ruling":raise ValueError("Admission policy requires a ruling version")
        return self

    @model_serializer(mode="wrap")
    def canonical(self,handler):
        value=handler(self)
        if self.import_admission is None:value.pop("import_admission",None)
        return value


class ImpactSelection(DTO):
    resource_ids: tuple[UUID, ...]
    source_ids: tuple[UUID, ...]
    scope: ResourceScope


class ImpactCommand(DTO):
    target_bundle_id: UUID
    selection: ImpactSelection
    rollback_of: UUID | None


class FactDifference(DTO):
    resource_id: UUID
    predicate_iri: str
    before_assertion_ids: tuple[UUID, ...]
    after_assertion_ids: tuple[UUID, ...]
    expected_revision: int
    requires_review: bool


class RuleImpact(DTO):
    id: UUID
    revision: int
    target_bundle: ExecutionBundle
    selection: ImpactSelection
    input_snapshots: tuple[BlobRef, ...]
    input_watermark: int
    baseline: dict[str, int]
    differences: tuple[FactDifference, ...]
    preserved_ruling_ids: tuple[UUID, ...]
    reevaluate_ruling_ids: tuple[UUID, ...]
    new_conflict_ids: tuple[UUID, ...]
    change_digest: str
    approval_id: UUID
    rollback_of: UUID | None


class PublishRuleCommand(DTO):
    rule_version_id: UUID
    expected_revision: int
    impact_id: UUID
    approval_id: UUID


class ConfigurationStageCommand(DTO):
    profile_ids: tuple[UUID, ...] = Field(min_length=1, max_length=256)
    authority_version_id: UUID
    ruling_policy_version_id: UUID


class ImportSelection(DTO):
    source_id: UUID
    upload: BlobRef | None = None
    file_options: FileOptions | SemanticFileOptions | None = None
    mode: Literal["full", "incremental"]
    scan_id: UUID | None = None
    semantic_format: Literal["turtle", "json-ld"] | None = None
    semantic_kind: Literal["schema", "shapes", "instances"] | None = None
    base_release_id: UUID | None = None
    target_draft_id: UUID | None = None
    target_draft_revision: int | None = Field(default=None, ge=0)


class ImportPreviewCommand(DTO):
    selection: ImportSelection
    bundle_id: UUID


class RowResult(DTO):
    locator: str = Field(json_schema_extra={"readOnly": True})
    source_key: SourceKey = Field(json_schema_extra={"readOnly": True})
    state: Literal["ready", "needs_review", "invalid", "applied", "failed", "unchanged"] = Field(json_schema_extra={"readOnly": True})
    entity_id: UUID | None = Field(default=None, json_schema_extra={"readOnly": True})
    revision: RevisionRef | None = Field(default=None, json_schema_extra={"readOnly": True})
    issue_refs: tuple[ParseIssue, ...] = Field(default=(), json_schema_extra={"readOnly": True})
    conflict_ids: tuple[UUID, ...] = Field(default=(), json_schema_extra={"readOnly": True})
    assertion_ids: tuple[UUID, ...] = Field(default=(), json_schema_extra={"readOnly": True})


class PreviewReport(DTO):
    id: UUID = Field(json_schema_extra={"readOnly": True})
    revision: int = Field(json_schema_extra={"readOnly": True})
    selection: ImportSelection = Field(json_schema_extra={"readOnly": True})
    bundle: ExecutionBundle = Field(json_schema_extra={"readOnly": True})
    input_digest: str = Field(json_schema_extra={"readOnly": True})
    baseline: dict[str, int] = Field(json_schema_extra={"readOnly": True})
    rows: tuple[RowResult, ...] = Field(json_schema_extra={"readOnly": True})
    ready_count: int = Field(json_schema_extra={"readOnly": True})
    review_count: int = Field(json_schema_extra={"readOnly": True})
    invalid_count: int = Field(json_schema_extra={"readOnly": True})
    complete: bool = Field(json_schema_extra={"readOnly": True})
    change_digest: str = Field(json_schema_extra={"readOnly": True})
    approval_id: UUID | None = Field(json_schema_extra={"readOnly": True})


class ApplyImportCommand(DTO):
    preview_id: UUID
    expected_revision: int = Field(ge=1)
    input_digest: str
    approval_id: UUID | None = None


class ImportRunView(DTO):
    id: UUID = Field(json_schema_extra={"readOnly": True})
    job: JobRef = Field(json_schema_extra={"readOnly": True})
    preview_id: UUID = Field(json_schema_extra={"readOnly": True})
    bundle_id: UUID = Field(json_schema_extra={"readOnly": True})
    read_count: int = Field(json_schema_extra={"readOnly": True})
    applied_count: int = Field(json_schema_extra={"readOnly": True})
    review_count: int = Field(json_schema_extra={"readOnly": True})
    failed_count: int = Field(json_schema_extra={"readOnly": True})
    schema_draft_id: UUID | None = Field(json_schema_extra={"readOnly": True})
    projection_watermark: int | None = Field(json_schema_extra={"readOnly": True})
    inference_watermark: int | None = Field(json_schema_extra={"readOnly": True})
    projection_state: Literal["pending", "synced", "failed"] = Field(json_schema_extra={"readOnly": True})
    complete: bool = Field(json_schema_extra={"readOnly": True})
