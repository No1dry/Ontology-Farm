"""Immutable artifact metadata and quota projections."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, BigInteger, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

from lzo.auth.models import metadata

artifact_versions = Table("artifact_versions", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), nullable=False),
    Column("previous_version_id", UUID(as_uuid=True), ForeignKey("artifact_versions.id"), unique=True),
    Column("version_number", Integer, nullable=False),
    Column("sha256", String(64), nullable=False), Column("size_bytes", BigInteger, nullable=False),
    Column("media_type", String(255), nullable=False), Column("filename", String(255), nullable=False),
    Column("metadata", JSONB, nullable=False),
    Column("uploaded_by", UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False))

artifact_links = Table("artifact_links", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("version_id", UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False),
    Column("target_resource_id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), nullable=False),
    Column("relation", String(128), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False))

artifact_reviews = Table("artifact_reviews", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("version_id", UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False),
    Column("decision", String(32), nullable=False), Column("comment", String(2000), nullable=False),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("event_id", UUID(as_uuid=True), ForeignKey("revisions.event_id"), nullable=False),
    Column("recorded_at", DateTime(timezone=True), nullable=False))

artifact_quotas = Table("artifact_quotas", metadata,
    Column("kind", String(16), primary_key=True), Column("owner_id", UUID(as_uuid=True), primary_key=True),
    Column("used_bytes", BigInteger, nullable=False))
