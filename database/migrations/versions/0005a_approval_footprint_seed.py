"""Conservatively mark every pre-F06 identity with unknown grant history."""
from alembic import op

revision = "0005a_approval_footprint_seed"
down_revision = "0005_approvals"
branch_labels = None
depends_on = None


def upgrade():
    # Group removals may leave no current recipient binding. Presence is a risk
    # marker only, never invented historical rights or a current authorization.
    op.execute("""INSERT INTO authorization_footprints
        SELECT gen_random_uuid(), u.id, 'pre_f06_unknown_history', clock_timestamp()
        FROM platform_users u
        WHERE NOT EXISTS (SELECT 1 FROM authorization_footprints f WHERE f.user_id=u.id)""")


def downgrade():
    # Immutable evidence must survive a software downgrade.
    pass
