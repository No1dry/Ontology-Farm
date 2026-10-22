"""Core catalog identities; no specialized permission copy."""
from alembic import op
from lzo.catalog.repository import entries, device_models

revision = '0403_catalog'
down_revision = '0402_artifact_upload_sessions'
branch_labels = depends_on = None


def upgrade():
    entries.create(op.get_bind())
    device_models.create(op.get_bind())
    op.execute('GRANT SELECT,INSERT ON catalog_entries TO lzo_app')
    op.execute('GRANT SELECT,INSERT,UPDATE ON catalog_device_models TO lzo_app')


def downgrade():
    op.drop_table('catalog_device_models')
    op.drop_table('catalog_entries')
