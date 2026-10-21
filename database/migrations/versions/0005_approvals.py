"""Immutable approval snapshots, decisions and visibility dependencies."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0005_approvals"
down_revision = "0004_jobs"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("authorization_footprints",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("command_digest", sa.String(64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False))
    op.create_index("ix_authorization_footprints_user", "authorization_footprints", ["user_id"])
    # Existing recipients are conservatively noninitial. This records presence,
    # never invents their historical permissions or confers any current rights.
    op.execute("""INSERT INTO authorization_footprints
        SELECT gen_random_uuid(), id, 'pre_f06_noninitial', clock_timestamp()
        FROM platform_users WHERE id IN
        (SELECT user_id FROM permission_grants WHERE user_id IS NOT NULL
         UNION SELECT user_id FROM group_members)""")
    op.execute("GRANT SELECT, INSERT ON authorization_footprints TO lzo_app")
    op.create_table("approvals",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("submitter_id", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("kind", sa.String(128), nullable=False),
        sa.Column("operation_key", sa.String(64), nullable=False),
        sa.Column("risk", sa.String(4), nullable=False),
        sa.Column("change_digest", sa.String(64), nullable=False),
        sa.Column("baseline", JSONB, nullable=False),
        sa.Column("descriptor", JSONB, nullable=False),
        sa.Column("target_ids", JSONB, nullable=False),
        sa.Column("impact", JSONB, nullable=False),
        sa.Column("reasons", JSONB, nullable=False),
        sa.Column("position", sa.BigInteger, nullable=False, unique=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("risk IN ('low','high')"))
    op.create_index("ix_approvals_operation", "approvals", ["submitter_id", "operation_key", "position"])
    op.create_table("approval_decisions",
        sa.Column("approval_id", UUID, sa.ForeignKey("approvals.id"), primary_key=True),
        sa.Column("reviewer_id", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("decision", sa.String(8), nullable=False),
        sa.Column("comment", sa.Text, nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("decision IN ('approve','reject')"))
    op.create_table("approval_dependencies",
        sa.Column("approval_id", UUID, sa.ForeignKey("approvals.id"), primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True))
    op.execute("GRANT SELECT, INSERT ON approvals, approval_decisions, approval_dependencies TO lzo_app")


def downgrade():
    for name in ("approval_dependencies", "approval_decisions", "approvals", "authorization_footprints"):
        op.drop_table(name)
