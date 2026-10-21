"""One durable source registry; raw uploads/events are append-only."""
from sqlalchemy import Column, Table, ForeignKey, Integer, String, UniqueConstraint, DateTime, LargeBinary, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID, JSONB
from lzo.governance.models import metadata

sources = Table("sources", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("configuration", JSONB, nullable=False), Column("revision", Integer, nullable=False))
source_uploads = Table("source_uploads", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False),
    Column("sha256", String(64), nullable=False), Column("config_digest", String(64), nullable=False),
    Column("version_id", UUID(as_uuid=True), ForeignKey("artifact_versions.id"), nullable=False),
    Column("binding", JSONB, nullable=False),
    UniqueConstraint("source_id", "sha256", "config_digest", name="uq_source_upload_identity"))
source_records = Table("source_records", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("upload_id", UUID(as_uuid=True), ForeignKey("source_uploads.id")),
    Column("scan_id", UUID(as_uuid=True), ForeignKey("ingestion_scans.id")),
    Column("ordinal", Integer, nullable=False), Column("payload", JSONB, nullable=False),
    UniqueConstraint("upload_id", "ordinal", name="uq_source_record_ordinal"),
    UniqueConstraint("scan_id", "ordinal", name="uq_scan_record_ordinal"),
    CheckConstraint('(upload_id IS NULL) <> (scan_id IS NULL)',name='ck_record_origin'))

source_credentials = Table('source_credentials',metadata,
    Column('ref',String(64),primary_key=True),Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),nullable=False),
    Column('revision',Integer,nullable=False),Column('ciphertext',LargeBinary,nullable=False))
source_connections = Table('source_connections',metadata,
    Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),primary_key=True),
    Column('revision',Integer,nullable=False),Column('payload',JSONB,nullable=False))
source_schedules = Table('source_schedules',metadata,
    Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),primary_key=True),
    Column('revision',Integer,nullable=False),Column('initiator_id',UUID(as_uuid=True),nullable=False),
    Column('next_run_at',DateTime(timezone=True)),Column('payload',JSONB,nullable=False))
source_scans = Table('ingestion_scans',metadata,
    Column('id',UUID(as_uuid=True),ForeignKey('resource_scopes.resource_id'),primary_key=True),
    Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),nullable=False),
    Column('job_id',UUID(as_uuid=True),ForeignKey('jobs.id'),nullable=False),
    Column('payload',JSONB,nullable=False))
scan_members = Table('ingestion_scan_members',metadata,
    Column('scan_id',UUID(as_uuid=True),ForeignKey('ingestion_scans.id'),primary_key=True),
    Column('canonical_key',String,primary_key=True),Column('change_id',UUID(as_uuid=True),ForeignKey('source_records.id'),nullable=False))
source_positions = Table('source_positions',metadata,
    Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),primary_key=True),
    Column('canonical_key',String,primary_key=True),Column('change_id',UUID(as_uuid=True),ForeignKey('source_records.id'),nullable=False),
    Column('payload',JSONB,nullable=False))
source_watermarks = Table('source_watermarks',metadata,
    Column('source_id',UUID(as_uuid=True),ForeignKey('sources.id'),primary_key=True),
    Column('payload',JSONB,nullable=False))

mapping_profiles = Table("ingestion_mapping_profiles", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False),
    Column("ordinal", Integer, nullable=False), Column("payload", JSONB, nullable=False),
    UniqueConstraint("source_id", "ordinal", name="uq_mapping_source_version"))
identity_bindings = Table("ingestion_identity_bindings", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("source_id", UUID(as_uuid=True), ForeignKey("sources.id"), nullable=False),
    Column("canonical_key", String, nullable=False),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("payload", JSONB, nullable=False),
    UniqueConstraint("source_id", "canonical_key", name="uq_identity_source_key"))
alignment_keys = Table("ingestion_alignment_keys", metadata,
    Column("rule_id", UUID(as_uuid=True), primary_key=True),
    Column("key_digest", String(64), primary_key=True),
    Column("resource_id", UUID(as_uuid=True), nullable=False),
    Column("binding_id", UUID(as_uuid=True), ForeignKey("ingestion_identity_bindings.id"), nullable=False))
rule_versions = Table("ingestion_rule_versions", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("kind", String(32), nullable=False), Column("payload", JSONB, nullable=False))
rule_impacts = Table("ingestion_rule_impacts", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("payload", JSONB, nullable=False))
conflicts = Table("ingestion_conflicts", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("payload", JSONB, nullable=False))
rulings = Table("ingestion_rulings", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("conflict_id", UUID(as_uuid=True), ForeignKey("ingestion_conflicts.id"), nullable=False),
    Column("payload", JSONB, nullable=False))
merge_proposals = Table("ingestion_merge_proposals", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("payload", JSONB, nullable=False))
identity_aliases = Table("ingestion_identity_aliases", metadata,
    Column("duplicate_id", UUID(as_uuid=True), primary_key=True),
    Column("canonical_id", UUID(as_uuid=True), nullable=False),
    Column("proposal_id", UUID(as_uuid=True), ForeignKey("ingestion_merge_proposals.id"), nullable=False),
    Column("revision_ref", JSONB, nullable=False))

import_previews = Table("ingestion_previews", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("payload", JSONB, nullable=False))
preview_jobs = Table("ingestion_preview_jobs", metadata,
    Column("job_id",UUID(as_uuid=True),ForeignKey("jobs.id"),primary_key=True),
    Column("payload",JSONB,nullable=False))
preview_work_rows = Table("ingestion_preview_work_rows",metadata,
    Column("job_id",UUID(as_uuid=True),ForeignKey("ingestion_preview_jobs.job_id"),primary_key=True),
    Column("ordinal",Integer,primary_key=True),
    Column("change_id",UUID(as_uuid=True),ForeignKey("source_records.id"),nullable=False),
    Column("payload",JSONB))
import_rows = Table("ingestion_rows", metadata,
    Column("preview_id", UUID(as_uuid=True), ForeignKey("ingestion_previews.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("payload", JSONB, nullable=False))
import_authorizations = Table("ingestion_import_authorizations", metadata,
    Column("preview_id", UUID(as_uuid=True), ForeignKey("ingestion_previews.id"), primary_key=True),
    Column("approval_id", UUID(as_uuid=True), ForeignKey("approvals.id"), nullable=False, unique=True))
import_runs = Table("ingestion_runs", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("jobs.id"), primary_key=True),
    Column("preview_id", UUID(as_uuid=True), ForeignKey("ingestion_previews.id"), nullable=False, unique=True),
    Column("payload", JSONB, nullable=False))
import_run_rows = Table("ingestion_run_rows", metadata,
    Column("run_id", UUID(as_uuid=True), ForeignKey("ingestion_runs.id"), primary_key=True),
    Column("ordinal", Integer, primary_key=True),
    Column("payload", JSONB, nullable=False))
source_evaluations = Table("ingestion_source_evaluations", metadata,
    Column("id", UUID(as_uuid=True), ForeignKey("resource_scopes.resource_id"), primary_key=True),
    Column("payload", JSONB, nullable=False))
