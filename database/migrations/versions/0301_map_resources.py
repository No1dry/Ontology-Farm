"""Map projection following the consolidated ingestion history."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = '0301_map_resources'
down_revision = '0204_ingestion_runs_rows'
branch_labels = depends_on = None


def upgrade():
    op.create_table('map_resources',
        sa.Column('id', UUID, sa.ForeignKey('resource_scopes.resource_id'), primary_key=True),
        sa.Column('revision', sa.Integer, nullable=False), sa.Column('payload', JSONB, nullable=False))
    op.create_table('map_artifacts',
        sa.Column('resource_id', UUID, sa.ForeignKey('map_resources.id'), primary_key=True),
        sa.Column('version_id', UUID, sa.ForeignKey('artifact_versions.id'), primary_key=True))
    op.execute('GRANT SELECT, INSERT, UPDATE ON map_resources TO lzo_app')
    op.execute('GRANT SELECT, INSERT, DELETE ON map_artifacts TO lzo_app')


def downgrade():
    if op.get_bind().execute(sa.text('SELECT EXISTS(SELECT 1 FROM map_resources)')).scalar():
        raise RuntimeError('Map history requires an explicit restore plan')
    op.drop_table('map_artifacts')
    op.drop_table('map_resources')
