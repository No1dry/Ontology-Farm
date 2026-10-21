"""Shared current bundle selection and immutable inference generations."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "S0104_inference_proofs"
down_revision = "S0103_objects_facts_geometry"
branch_labels = depends_on = None


def upgrade():
    op.add_column("ontology_catalog", sa.Column("active_bundle_id", UUID,
                  sa.ForeignKey("execution_bundles.id")))
    op.execute("""UPDATE ontology_catalog c SET active_bundle_id=r.bundle_id
                  FROM ontology_releases r WHERE c.active_release_id=r.id""")
    for name in ("inference_runs", "inference_components", "inference_manifests"):
        op.create_table(name, sa.Column("id", UUID, primary_key=True),
            sa.Column("payload", JSONB, nullable=False))
    op.create_table("inference_current", sa.Column("singleton", sa.Integer, primary_key=True),
        sa.Column("assembly_id", UUID), sa.Column("complete_manifest_id", UUID))
    op.execute("INSERT INTO inference_current(singleton) VALUES(1)")
    op.create_table("inference_coverage",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("run_id", UUID, nullable=False))
    op.create_table("inference_schedules",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("bundle_id", UUID, sa.ForeignKey("execution_bundles.id"), primary_key=True),
        sa.Column("job_id", UUID, sa.ForeignKey("jobs.id"), nullable=False))
    op.add_column("inference_requests", sa.Column("error_code", sa.String(128)))
    op.create_table("inference_terminals",
        sa.Column("run_id", UUID, sa.ForeignKey("inference_runs.id"), primary_key=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("audit_event_id", UUID, sa.ForeignKey("revisions.event_id"), nullable=False),
        sa.CheckConstraint("state IN ('published','superseded','retired')"))
    op.create_table("inference_output_retirements",
        sa.Column("event_id", UUID, sa.ForeignKey("projection_events.event_id"), primary_key=True),
        sa.Column("run_id", UUID, sa.ForeignKey("inference_runs.id"), nullable=False),
        sa.Column("actor_id", UUID, nullable=False), sa.Column("reason", sa.String(128), nullable=False),
        sa.Column("audit_event_id", UUID, sa.ForeignKey("revisions.event_id"), nullable=False))
    op.execute("""GRANT SELECT, INSERT ON inference_runs, inference_components,
                  inference_manifests, inference_coverage, inference_schedules,
                  inference_terminals, inference_output_retirements TO lzo_app""")
    op.execute("GRANT SELECT, UPDATE ON inference_current TO lzo_app")
    op.execute("GRANT UPDATE ON inference_requests TO lzo_app")


def downgrade():
    op.drop_table("inference_output_retirements")
    op.drop_table("inference_terminals")
    op.drop_column("inference_requests", "error_code")
    for table in ("inference_schedules", "inference_coverage", "inference_current",
                  "inference_manifests", "inference_components", "inference_runs"):
        op.drop_table(table)
    op.drop_column("ontology_catalog", "active_bundle_id")
