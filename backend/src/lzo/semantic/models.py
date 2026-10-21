"""Projection operational state and immutable classification/input evidence."""
from sqlalchemy import BigInteger, Boolean, Column, DateTime, ForeignKey, Integer, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID
from lzo.governance.models import metadata

projection_events = Table("projection_events", metadata,
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("position", BigInteger, nullable=False, unique=True),
    Column("producer_version", String(128), nullable=False),
    Column("reference_ids", JSONB, nullable=False),
    Column("dependency_refs", JSONB, nullable=False),
    Column("initiator_id", UUID(as_uuid=True), nullable=False),
    Column("state", String(16), nullable=False),
    Column("digest", String(64), nullable=False),
    Column("error_code", String(128)))

projection_heads = Table("projection_heads", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("position", BigInteger, nullable=False),
    Column("digest", String(64), nullable=False))

inference_output_retirements = Table("inference_output_retirements", metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("run_id", UUID(as_uuid=True), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("reason", String(128), nullable=False),
    Column("audit_event_id", UUID(as_uuid=True), nullable=False))

projection_classifications = Table("projection_classifications", metadata,
    Column("position", BigInteger, primary_key=True),
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), nullable=False, unique=True),
    Column("kind", String(16), nullable=False),
    Column("version", String(128), nullable=False))

inference_fence = Table("inference_fence", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("expected_position", BigInteger, nullable=False),
    Column("resolved_position", BigInteger, nullable=False))

inference_inputs = Table("inference_inputs", metadata,
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), primary_key=True),
    Column("position", BigInteger, nullable=False),
    Column("kind", String(16), nullable=False))

inference_requests = Table("inference_requests", metadata,
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), primary_key=True),
    Column("position", BigInteger, nullable=False),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("state", String(16), nullable=False),
    Column("error_code", String(128)))

# Mutable current-state indexes; each version is also in the immutable F02 ledger.
objects = Table("semantic_objects", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("iri", String, nullable=False, unique=True),
    Column("revision", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("bundle_id", UUID(as_uuid=True), nullable=False),
    Column("next_transition_at", DateTime(timezone=True)),
    Column("transition_origin_event_id", UUID(as_uuid=True)))
assertions = Table("semantic_assertions", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("revision_ref", JSONB, nullable=False),
    Column("bundle_id", UUID(as_uuid=True), nullable=False))
supports = Table("semantic_supports", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("assertion_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    Column("withdrawn_event_id", UUID(as_uuid=True)))
object_dependencies = Table("semantic_object_dependencies", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("dependency_id", UUID(as_uuid=True), primary_key=True))
type_declarations = Table("semantic_type_declarations", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("class_iris", JSONB, nullable=False),
    Column("revision_ref", JSONB, nullable=False),
    Column("dependency_refs", JSONB, nullable=False))

geometry_supports = Table("semantic_geometry_supports", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("active", Boolean, nullable=False),
    Column("revision_ref", JSONB, nullable=False),
    Column("withdrawn_event_id", UUID(as_uuid=True)))

export_bindings = Table("semantic_exports", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True), Column("initiator_id", UUID(as_uuid=True), nullable=False),
    Column("policy_version", String(64), nullable=False), Column("state", String(16), nullable=False),
    Column("request", JSONB, nullable=False), Column("bundle_id", UUID(as_uuid=True), nullable=False),
    Column("input_watermark", BigInteger, nullable=False), Column("input_digest", String(64), nullable=False))
export_dependencies = Table("semantic_export_dependencies", metadata,
    Column("export_id", UUID(as_uuid=True), primary_key=True), Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("export_root", Boolean, nullable=False), Column("candidate", Boolean, nullable=False))
export_outputs = Table("semantic_export_outputs", metadata,
    Column("export_id", UUID(as_uuid=True), primary_key=True), Column("version_id", UUID(as_uuid=True), nullable=False),
    Column("sha256", String(64), nullable=False), Column("state", String(16), nullable=False))
