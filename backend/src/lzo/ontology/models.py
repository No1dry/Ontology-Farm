"""Mutable workflow projections beside immutable release and bundle snapshots."""
from sqlalchemy import Column, Integer, String, Table
from sqlalchemy.dialects.postgresql import JSONB, UUID
from lzo.governance.models import metadata

drafts = Table("ontology_drafts", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("revision", Integer, nullable=False),
    Column("base_release_id", UUID(as_uuid=True)),
    Column("document", JSONB, nullable=False),
    Column("imports", JSONB, nullable=False),
    Column("state", String(32), nullable=False))
releases = Table("ontology_releases", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("draft_id", UUID(as_uuid=True), nullable=False),
    Column("draft_revision", Integer, nullable=False),
    Column("version", String(128), nullable=False),
    Column("document", JSONB, nullable=False),
    Column("imports", JSONB, nullable=False),
    Column("artifacts", JSONB, nullable=False),
    Column("approval_id", UUID(as_uuid=True), nullable=False),
    Column("bundle_id", UUID(as_uuid=True), nullable=False),
    Column("impact", JSONB, nullable=False))
installations = Table("ontology_installations", metadata,
    Column("release_id", UUID(as_uuid=True), primary_key=True),
    Column("state", String(32), nullable=False),
    Column("event_id", UUID(as_uuid=True)),
    Column("job_id", UUID(as_uuid=True)),
    Column("error_code", String(128)))
catalog = Table("ontology_catalog", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("active_release_id", UUID(as_uuid=True)),
    Column("active_bundle_id", UUID(as_uuid=True)))
bundles = Table("execution_bundles", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("schema_release_id", UUID(as_uuid=True), nullable=False),
    Column("mapping_version_id", UUID(as_uuid=True)),
    Column("authority_version_id", UUID(as_uuid=True), nullable=False),
    Column("ruling_policy_version_id", UUID(as_uuid=True), nullable=False),
    Column("inference_version_id", UUID(as_uuid=True), nullable=False),
    Column("shapes_digest", String(64), nullable=False))
genesis = Table("ontology_genesis", metadata,
    Column("kind", String(64), primary_key=True),
    Column("version_id", UUID(as_uuid=True), nullable=False))
dependencies = Table("ontology_dependencies", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("dependency_id", UUID(as_uuid=True), primary_key=True))
axioms = Table("schema_axioms", metadata,
    Column("iri", String, primary_key=True),
    Column("release_id", UUID(as_uuid=True), primary_key=True),
    Column("triple", JSONB, nullable=False))
