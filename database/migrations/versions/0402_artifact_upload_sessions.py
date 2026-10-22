"""Durable, quota-reserved private artifact upload sessions."""
from alembic import op
from lzo.artifacts.models import artifact_upload_sessions

revision = '0402_artifact_upload_sessions'
down_revision = '0401_store_deliveries'
branch_labels = depends_on = None


def upgrade():
    artifact_upload_sessions.create(op.get_bind())
    op.execute('GRANT SELECT, INSERT, UPDATE ON artifact_upload_sessions TO lzo_app')


def downgrade():
    op.drop_table('artifact_upload_sessions')
