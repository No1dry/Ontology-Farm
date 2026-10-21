"""Read-only source configuration, immutable credentials and scan proof."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB
revision='0203_ingestion_scans_schedules'
down_revision='0202_ingestion_mapping_rulings'
branch_labels=depends_on=None


def upgrade():
    op.create_table('source_credentials',sa.Column('ref',sa.String(64),primary_key=True),
        sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),nullable=False),sa.Column('revision',sa.Integer,nullable=False),sa.Column('ciphertext',sa.LargeBinary,nullable=False))
    op.create_table('source_connections',sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),primary_key=True),
        sa.Column('revision',sa.Integer,nullable=False),sa.Column('payload',JSONB,nullable=False))
    op.create_table('source_schedules',sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),primary_key=True),
        sa.Column('revision',sa.Integer,nullable=False),sa.Column('initiator_id',UUID,nullable=False),
        sa.Column('next_run_at',sa.DateTime(timezone=True)),sa.Column('payload',JSONB,nullable=False))
    op.create_table('ingestion_scans',sa.Column('id',UUID,sa.ForeignKey('resource_scopes.resource_id'),primary_key=True),
        sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),nullable=False),
        sa.Column('job_id',UUID,sa.ForeignKey('jobs.id'),nullable=False),sa.Column('payload',JSONB,nullable=False))
    op.alter_column('source_records','upload_id',nullable=True)
    op.add_column('source_records',sa.Column('scan_id',UUID,sa.ForeignKey('ingestion_scans.id')))
    op.create_check_constraint('ck_record_origin','source_records','(upload_id IS NULL) <> (scan_id IS NULL)')
    op.create_unique_constraint('uq_scan_record_ordinal','source_records',['scan_id','ordinal'])
    op.create_table('ingestion_scan_members',sa.Column('scan_id',UUID,sa.ForeignKey('ingestion_scans.id'),primary_key=True),
        sa.Column('canonical_key',sa.String,primary_key=True),sa.Column('change_id',UUID,sa.ForeignKey('source_records.id'),nullable=False))
    op.create_table('source_positions',sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),primary_key=True),
        sa.Column('canonical_key',sa.String,primary_key=True),sa.Column('change_id',UUID,sa.ForeignKey('source_records.id'),nullable=False),sa.Column('payload',JSONB,nullable=False))
    op.create_table('source_watermarks',sa.Column('source_id',UUID,sa.ForeignKey('sources.id'),primary_key=True),sa.Column('payload',JSONB,nullable=False))
    for table in ('source_credentials','ingestion_scan_members'):
        op.execute(f'GRANT SELECT, INSERT ON {table} TO lzo_app')
    for table in ('source_connections','source_schedules','ingestion_scans','source_positions','source_watermarks'):
        op.execute(f'GRANT SELECT, INSERT, UPDATE ON {table} TO lzo_app')


def downgrade():
    # Never discard persisted database events to make a downgrade succeed.
    bind=op.get_bind()
    if bind.execute(sa.text('SELECT EXISTS(SELECT 1 FROM source_records WHERE scan_id IS NOT NULL)')).scalar():
        raise RuntimeError('Database source history requires an explicit restore plan')
    for t in ('source_watermarks','source_positions','ingestion_scan_members'):op.drop_table(t)
    op.drop_constraint('uq_scan_record_ordinal','source_records',type_='unique')
    op.drop_constraint('ck_record_origin','source_records',type_='check')
    op.drop_column('source_records','scan_id')
    op.alter_column('source_records','upload_id',nullable=False)
    for t in ('ingestion_scans','source_schedules','source_connections','source_credentials'):op.drop_table(t)
