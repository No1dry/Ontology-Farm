"""Immutable artifact versions, append-only evidence events and quota counters."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0003_artifacts"
down_revision = "0002a_identity_operations"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifact_versions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), nullable=False),
        sa.Column("previous_version_id", UUID, sa.ForeignKey("artifact_versions.id"), unique=True),
        sa.Column("version_number", sa.Integer, nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("size_bytes", sa.BigInteger, nullable=False),
        sa.Column("media_type", sa.String(255), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("metadata", JSONB, nullable=False),
        sa.Column("uploaded_by", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes > 0"), sa.CheckConstraint("version_number > 0"))
    op.create_table("artifact_links",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("target_resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), nullable=False),
        sa.Column("relation", sa.String(128), nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("artifact_reviews",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("decision", sa.String(32), nullable=False),
        sa.Column("comment", sa.String(2000), nullable=False),
        sa.Column("actor_id", UUID, nullable=False),
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("artifact_quotas",
        sa.Column("kind", sa.String(16), primary_key=True),
        sa.Column("owner_id", UUID, primary_key=True),
        sa.Column("used_bytes", sa.BigInteger, nullable=False),
        sa.CheckConstraint("used_bytes >= 0"))
    op.create_index("ix_artifact_resource", "artifact_versions", ["resource_id", "recorded_at"])
    op.create_index("ix_artifact_link_version", "artifact_links", ["version_id"])
    op.create_index("ix_artifact_review_version", "artifact_reviews", ["version_id"])
    for table in ("artifact_versions", "artifact_links", "artifact_reviews"):
        op.execute(sa.text(f"GRANT SELECT, INSERT ON {table} TO lzo_app"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON artifact_quotas TO lzo_app")


def downgrade():
    for table in ("artifact_quotas", "artifact_reviews", "artifact_links", "artifact_versions"):
        op.drop_table(table)
