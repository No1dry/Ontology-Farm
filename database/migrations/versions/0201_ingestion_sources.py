"""Single source registry and immutable registered uploads/raw source events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0201_ingestion_sources"
down_revision = "S0105_semantic_exports"
branch_labels = depends_on = None


def upgrade():
    op.create_table("sources",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("configuration", JSONB, nullable=False), sa.Column("revision", sa.Integer, nullable=False))
    op.create_table("source_uploads",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False), sa.Column("config_digest", sa.String(64), nullable=False),
        sa.Column("version_id", UUID, sa.ForeignKey("artifact_versions.id"), nullable=False),
        sa.Column("binding", JSONB, nullable=False),
        sa.UniqueConstraint("source_id", "sha256", "config_digest", name="uq_source_upload_identity"))
    op.create_table("source_records",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("upload_id", UUID, sa.ForeignKey("source_uploads.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False), sa.Column("payload", JSONB, nullable=False),
        sa.UniqueConstraint("upload_id", "ordinal", name="uq_source_record_ordinal"))
    for table in ("sources", "source_uploads", "source_records"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO lzo_app")


def downgrade():
    for table in ("source_records", "source_uploads", "sources"):
        op.drop_table(table)
