"""Immutable preview/evaluation evidence and recoverable import progress."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB

revision = "0204_ingestion_runs_rows"
down_revision = "0203_ingestion_scans_schedules"
branch_labels = depends_on = None


def upgrade():
    op.add_column("semantic_geometry",sa.Column("active",sa.Boolean,nullable=False,server_default=sa.true()))
    op.create_table("semantic_geometry_supports",sa.Column("id",UUID,primary_key=True),
        sa.Column("resource_id",UUID,nullable=False),sa.Column("payload",JSONB,nullable=False),
        sa.Column("active",sa.Boolean,nullable=False),sa.Column("revision_ref",JSONB,nullable=False),
        sa.Column("withdrawn_event_id",UUID))
    op.execute("GRANT SELECT, INSERT, UPDATE ON semantic_geometry_supports TO lzo_app")
    for name in ("ingestion_previews", "ingestion_source_evaluations"):
        op.create_table(name, sa.Column("id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True),
            sa.Column("payload", JSONB, nullable=False))
        op.execute(f"GRANT SELECT, INSERT ON {name} TO lzo_app")
    op.create_table("ingestion_preview_jobs",sa.Column("job_id",UUID,sa.ForeignKey("jobs.id"),primary_key=True),
        sa.Column("payload",JSONB,nullable=False))
    op.create_table("ingestion_preview_work_rows",sa.Column("job_id",UUID,sa.ForeignKey("ingestion_preview_jobs.job_id"),primary_key=True),
        sa.Column("ordinal",sa.Integer,primary_key=True),sa.Column("change_id",UUID,sa.ForeignKey("source_records.id"),nullable=False),
        sa.Column("payload",JSONB))
    op.execute("GRANT SELECT, INSERT ON ingestion_preview_jobs TO lzo_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ingestion_preview_work_rows TO lzo_app")
    op.create_table("ingestion_rows", sa.Column("preview_id", UUID, sa.ForeignKey("ingestion_previews.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True), sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_import_authorizations",
        sa.Column("preview_id", UUID, sa.ForeignKey("ingestion_previews.id"), primary_key=True),
        sa.Column("approval_id", UUID, sa.ForeignKey("approvals.id"), nullable=False, unique=True))
    op.execute("GRANT SELECT, INSERT ON ingestion_import_authorizations TO lzo_app")
    op.create_table("ingestion_runs", sa.Column("id", UUID, sa.ForeignKey("jobs.id"), primary_key=True),
        sa.Column("preview_id", UUID, sa.ForeignKey("ingestion_previews.id"), nullable=False, unique=True),
        sa.Column("payload", JSONB, nullable=False))
    op.create_table("ingestion_run_rows", sa.Column("run_id", UUID, sa.ForeignKey("ingestion_runs.id"), primary_key=True),
        sa.Column("ordinal", sa.Integer, primary_key=True), sa.Column("payload", JSONB, nullable=False))
    op.execute("GRANT SELECT, INSERT ON ingestion_rows, ingestion_run_rows TO lzo_app")
    op.execute("GRANT SELECT, INSERT, UPDATE ON ingestion_runs TO lzo_app")


def downgrade():
    history=("ingestion_previews","ingestion_source_evaluations","ingestion_preview_jobs",
        "ingestion_preview_work_rows","ingestion_rows","ingestion_import_authorizations",
        "ingestion_runs","ingestion_run_rows","semantic_geometry_supports")
    if any(op.get_bind().execute(sa.text(f"SELECT EXISTS(SELECT 1 FROM {name})")).scalar() for name in history):
        raise RuntimeError("Import history requires an explicit restore plan")
    for name in ("ingestion_preview_work_rows","ingestion_preview_jobs","ingestion_run_rows", "ingestion_runs", "ingestion_import_authorizations", "ingestion_rows", "ingestion_source_evaluations", "ingestion_previews"):
        op.drop_table(name)
    op.drop_table("semantic_geometry_supports")
    op.drop_column("semantic_geometry","active")
