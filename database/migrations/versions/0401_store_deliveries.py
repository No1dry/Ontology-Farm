"""Durable professional-store delivery ledger."""
from alembic import op
from lzo.synchronization.models import CORE_DDL
revision = "0401_store_deliveries"
down_revision = "0301_map_resources"
branch_labels = depends_on = None

def upgrade():
    op.execute(CORE_DDL)
    op.execute("GRANT SELECT ON alembic_version TO lzo_app")

def downgrade():
    op.drop_table("store_deliveries")

