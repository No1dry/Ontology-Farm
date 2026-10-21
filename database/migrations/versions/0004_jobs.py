"""Durable jobs, lease fences and append-only attempt records."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0004_jobs"
down_revision = "0003a_artifact_visibility"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("jobs",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("initiator_id", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("bundle_id", UUID),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("reference_ids", JSONB, nullable=False),
        sa.Column("cursor", JSONB, nullable=False),
        sa.Column("result_ref", JSONB),
        sa.Column("error_code", sa.String(128)),
        sa.Column("attempts", sa.Integer, nullable=False),
        sa.Column("retry_count", sa.Integer, nullable=False),
        sa.Column("max_attempts", sa.Integer, nullable=False),
        sa.Column("lease_token", UUID),
        sa.Column("worker_id", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column("heartbeat_at", sa.DateTime(timezone=True)),
        sa.Column("available_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("outbox_id", UUID, sa.ForeignKey("outbox_events.id")),
        sa.Column("outbox_position", sa.BigInteger),
        sa.CheckConstraint("status IN ('queued','running','paused','failed','succeeded','cancelled')"),
        sa.CheckConstraint("attempts >= 0 AND retry_count >= 0 AND max_attempts > 0"),
        sa.CheckConstraint("(status = 'running') = (lease_token IS NOT NULL AND lease_expires_at IS NOT NULL)"),
        sa.UniqueConstraint("kind", "outbox_id"))
    op.create_index("ix_jobs_claim", "jobs", ["status", "available_at", "created_at"])
    op.create_table("job_dependencies",
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True))
    op.create_table("job_attempts",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id"), nullable=False),
        sa.Column("attempt", sa.Integer, nullable=False),
        sa.Column("lease_token", UUID, nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("initiator_id", UUID, nullable=False),
        sa.Column("authorization_version", sa.Integer, nullable=False),
        sa.Column("phase", sa.String(32), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_job_attempts_job", "job_attempts", ["job_id", "attempt"])
    op.create_table("source_health",
        sa.Column("source_id", UUID, primary_key=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("status IN ('ready','offline','degraded')"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON jobs, source_health TO lzo_app")
    op.execute("GRANT SELECT, INSERT ON job_dependencies, job_attempts TO lzo_app")


def downgrade():
    for table in ("source_health", "job_attempts", "job_dependencies", "jobs"):
        op.drop_table(table)
