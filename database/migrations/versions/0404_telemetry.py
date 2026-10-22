"""Batch acceptance and source-wide identities, without sample RDF revisions."""
from alembic import op
from lzo.telemetry.repository import batches, identities

revision = '0404_telemetry'
down_revision = '0403_catalog'
branch_labels = depends_on = None


def upgrade():
    batches.create(op.get_bind())
    identities.create(op.get_bind())
    op.execute('GRANT SELECT,INSERT ON telemetry_batches,telemetry_record_keys TO lzo_app')


def downgrade():
    op.drop_table('telemetry_record_keys')
    op.drop_table('telemetry_batches')
