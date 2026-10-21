"""Immutable selected export footprint and one actual completed artifact."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "S0105_semantic_exports"
down_revision = "S0104_inference_proofs"
branch_labels = depends_on = None


def upgrade():
    op.create_table("semantic_exports",
        sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("initiator_id", UUID, nullable=False),
        sa.Column("policy_version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("request", JSONB, nullable=False),
        sa.Column("bundle_id", UUID, nullable=False),
        sa.Column("input_watermark", sa.BigInteger, nullable=False),
        sa.Column("input_digest", sa.String(64), nullable=False),
        sa.CheckConstraint("state='prepared'"))
    op.create_table("semantic_export_dependencies",
        sa.Column("export_id", UUID, sa.ForeignKey("semantic_exports.id", deferrable=True, initially="DEFERRED"), primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
        sa.Column("export_root", sa.Boolean, nullable=False),
        sa.Column("candidate", sa.Boolean, nullable=False),
        sa.CheckConstraint("export_id <> resource_id"))
    op.create_table("semantic_export_outputs",
        sa.Column("export_id", UUID, sa.ForeignKey("semantic_exports.id"), primary_key=True),
        sa.Column("version_id", UUID, sa.ForeignKey("artifact_versions.id"), nullable=False, unique=True),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.CheckConstraint("state='completed'"))
    # Dependency rows are inserted before their deferred owner binding. Its
    # insertion seals the footprint; no UPDATE/DELETE rights are granted.
    op.execute("""CREATE FUNCTION seal_semantic_export() RETURNS trigger LANGUAGE plpgsql AS $$
      BEGIN
        IF TG_TABLE_NAME='semantic_export_dependencies' THEN
          IF EXISTS(SELECT 1 FROM semantic_exports WHERE id=NEW.export_id) OR
             EXISTS(SELECT 1 FROM resource_scopes WHERE resource_id=NEW.resource_id
                    AND resource_kind IN ('semantic.export','job')) OR
             EXISTS(SELECT 1 FROM artifact_versions a JOIN resource_scopes s ON s.resource_id=a.resource_id
                    WHERE a.id=NEW.resource_id AND s.resource_kind='semantic.export') THEN
            RAISE EXCEPTION 'Invalid or sealed export footprint';
          END IF;
        ELSIF TG_TABLE_NAME='semantic_exports' THEN
          IF NOT EXISTS(SELECT 1 FROM semantic_export_dependencies WHERE export_id=NEW.id AND export_root) OR
             NOT EXISTS(SELECT 1 FROM resource_scopes WHERE resource_id=NEW.id AND resource_kind='semantic.export') THEN
            RAISE EXCEPTION 'Export needs an actual protected root';
          END IF;
        ELSE
          IF NOT EXISTS(SELECT 1 FROM artifact_versions WHERE id=NEW.version_id
                        AND resource_id=NEW.export_id AND sha256=NEW.sha256 AND version_number=1
                        AND previous_version_id IS NULL) THEN
            RAISE EXCEPTION 'Export output is not the actual initial artifact';
          END IF;
        END IF;
        RETURN NEW;
      END $$""")
    for table in ("semantic_exports", "semantic_export_dependencies", "semantic_export_outputs"):
        op.execute(f"CREATE TRIGGER seal_export BEFORE INSERT ON {table} FOR EACH ROW EXECUTE FUNCTION seal_semantic_export()")
        op.execute(f"GRANT SELECT, INSERT ON {table} TO lzo_app")


def downgrade():
    for table in ("semantic_export_outputs", "semantic_export_dependencies", "semantic_exports"):
        op.drop_table(table)
    op.execute("DROP FUNCTION seal_semantic_export()")
