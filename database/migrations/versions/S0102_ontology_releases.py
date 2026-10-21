"""Governed ontology snapshots and F/S-only execution bundles."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "S0102_ontology_releases"
down_revision = "S0101_semantic_projection"
branch_labels = depends_on = None


def upgrade():
    op.create_table("ontology_drafts",
        sa.Column("id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("base_release_id", UUID),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("imports", JSONB, nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.CheckConstraint("revision > 0"),
        sa.CheckConstraint("state IN ('draft','submitted','approved','installing','published','failed')"))
    op.create_table("ontology_releases",
        sa.Column("id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True),
        sa.Column("draft_id", UUID, sa.ForeignKey("ontology_drafts.id"), nullable=False),
        sa.Column("draft_revision", sa.Integer, nullable=False),
        sa.Column("version", sa.String(128), nullable=False, unique=True),
        sa.Column("document", JSONB, nullable=False),
        sa.Column("imports", JSONB, nullable=False),
        sa.Column("artifacts", JSONB, nullable=False),
        sa.Column("approval_id", UUID, sa.ForeignKey("approvals.id"), nullable=False),
        sa.Column("bundle_id", UUID, nullable=False),
        sa.Column("impact", JSONB, nullable=False),
        sa.UniqueConstraint("draft_id", "draft_revision"))
    op.create_table("ontology_installations",
        sa.Column("release_id", UUID, sa.ForeignKey("ontology_releases.id"), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id")),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id")),
        sa.Column("error_code", sa.String(128)),
        sa.CheckConstraint("state IN ('installing','published','failed','deprecated')"))
    op.create_table("ontology_catalog",
        sa.Column("singleton", sa.Integer, primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("active_release_id", UUID, sa.ForeignKey("ontology_releases.id")),
        sa.CheckConstraint("singleton = 1 AND revision > 0"))
    op.create_table("execution_bundles",
        sa.Column("id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True),
        sa.Column("schema_release_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False),
        sa.Column("mapping_version_id", UUID, sa.ForeignKey("resource_heads.resource_id")),
        sa.Column("authority_version_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False),
        sa.Column("ruling_policy_version_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False),
        sa.Column("inference_version_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False),
        sa.Column("shapes_digest", sa.String(64), nullable=False))
    op.create_table("ontology_genesis",
        sa.Column("kind", sa.String(64), primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("resource_heads.resource_id"), nullable=False))
    op.create_table("ontology_dependencies",
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True),
        sa.Column("dependency_id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True))
    op.create_table("schema_axioms",
        sa.Column("iri", sa.String, primary_key=True),
        sa.Column("release_id", UUID, sa.ForeignKey("ontology_releases.id"), primary_key=True),
        sa.Column("triple", JSONB, nullable=False))
    op.execute("GRANT SELECT, INSERT, UPDATE ON ontology_drafts, ontology_installations, ontology_catalog TO lzo_app")
    op.execute("GRANT SELECT, INSERT ON ontology_releases, execution_bundles, ontology_genesis, ontology_dependencies, schema_axioms TO lzo_app")


def downgrade():
    for name in ("schema_axioms", "ontology_dependencies", "ontology_genesis", "execution_bundles",
                 "ontology_catalog", "ontology_installations", "ontology_releases", "ontology_drafts"):
        op.drop_table(name)
