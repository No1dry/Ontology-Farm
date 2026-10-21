"""Ledger tables shared by service queries and schema migration."""
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

platform_watermarks = Table(
    "platform_watermarks", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("event_position", BigInteger, nullable=False),
    Column("input_watermark", BigInteger, nullable=False),
    Column("projection_watermark", BigInteger, nullable=False),
    Column("inference_watermark", BigInteger, nullable=False),
    CheckConstraint("singleton = 1"),
    CheckConstraint("event_position >= 0 AND input_watermark >= 0 AND projection_watermark >= 0 AND inference_watermark >= 0"),
)

resource_heads = Table(
    "resource_heads", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("digest", String(64), nullable=True),
    Column("event_id", UUID(as_uuid=True), nullable=True),
    CheckConstraint("revision >= 0"),
)

revisions = Table(
    "revisions", metadata,
    Column("event_id", UUID(as_uuid=True), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), ForeignKey("resource_heads.resource_id"), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("previous_revision", Integer, nullable=False),
    Column("previous_digest", String(64)),
    Column("content_digest", String(64), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("request_id", String(256), nullable=False),
    Column("effective_at", DateTime(timezone=True), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("resource_id", "revision"),
)

audit_events = Table(
    "audit_events", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), nullable=False),
    Column("event_type", String(128), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("request_id", String(256), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

outbox_events = Table(
    "outbox_events", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), nullable=False),
    Column("position", BigInteger, nullable=False, unique=True),
    Column("event_type", String(128), nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)

idempotency_results = Table(
    "idempotency_results", metadata,
    Column("actor_id", UUID(as_uuid=True), primary_key=True),
    Column("key", String(256), primary_key=True),
    Column("body_digest", String(64), nullable=False),
    Column("result", JSONB),
    Column("recorded_at", DateTime(timezone=True), nullable=False),
)
