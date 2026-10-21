"""Durable identity side-effect intents; additive after deployed F0002."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002a_identity_operations"
down_revision = "0002_identity_authorization"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("identity_operations", sa.Column("id", UUID, primary_key=True),
        sa.Column("actor_id", UUID, nullable=False), sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("phase", sa.String(32), nullable=False), sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("payload", JSONB, nullable=False), sa.Column("provider_subject", sa.String(255)),
        sa.Column("user_id", UUID, sa.ForeignKey("platform_users.id")), sa.Column("error_code", sa.String(128)))
    op.execute("GRANT SELECT, INSERT, UPDATE ON identity_operations TO lzo_app")


def downgrade():
    op.drop_table("identity_operations")
