"""Immutable mapping/rule versions and serializable source/business identities."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0202_ingestion_mapping_rulings"
down_revision = "0201_ingestion_sources"
branch_labels = depends_on = None


def upgrade():
    op.create_table("ingestion_mapping_profiles",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("ordinal", sa.Integer, nullable=False), sa.Column("payload", JSONB, nullable=False),
        sa.UniqueConstraint("source_id", "ordinal", name="uq_mapping_source_version"))
    op.create_table("ingestion_identity_bindings",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("source_id", UUID, sa.ForeignKey("sources.id"), nullable=False),
        sa.Column("canonical_key", sa.String, nullable=False),
        sa.Column("resource_id", UUID, nullable=False), sa.Column("payload", JSONB, nullable=False),
        sa.UniqueConstraint("source_id", "canonical_key", name="uq_identity_source_key"))
    op.create_table("ingestion_alignment_keys",
        sa.Column("rule_id", UUID, primary_key=True), sa.Column("key_digest", sa.String(64), primary_key=True),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("binding_id", UUID, sa.ForeignKey("ingestion_identity_bindings.id"), nullable=False))
    op.create_table("ingestion_rule_versions",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("kind", sa.String(32), nullable=False), sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_rule_impacts",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_conflicts",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_rulings",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("conflict_id", UUID, sa.ForeignKey("ingestion_conflicts.id"), nullable=False),
        sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_merge_proposals",sa.Column("id",UUID,sa.ForeignKey("resource_scopes.resource_id"),primary_key=True),
        sa.Column("payload",JSONB,nullable=False))
    op.create_table("ingestion_identity_aliases",sa.Column("duplicate_id",UUID,primary_key=True),
        sa.Column("canonical_id",UUID,nullable=False),sa.Column("proposal_id",UUID,sa.ForeignKey("ingestion_merge_proposals.id"),nullable=False),
        sa.Column("revision_ref",JSONB,nullable=False))
    for table in ("ingestion_mapping_profiles", "ingestion_identity_bindings", "ingestion_alignment_keys", "ingestion_rule_versions", "ingestion_rule_impacts", "ingestion_conflicts", "ingestion_rulings", "ingestion_merge_proposals", "ingestion_identity_aliases"):
        op.execute(f"GRANT SELECT, INSERT ON {table} TO lzo_app")
    op.execute("GRANT UPDATE ON sources TO lzo_app")


def downgrade():
    for table in ("ingestion_identity_aliases", "ingestion_merge_proposals", "ingestion_rulings", "ingestion_conflicts", "ingestion_rule_impacts", "ingestion_rule_versions", "ingestion_alignment_keys", "ingestion_identity_bindings", "ingestion_mapping_profiles"):
        op.drop_table(table)
    op.execute("REVOKE UPDATE ON sources FROM lzo_app")
