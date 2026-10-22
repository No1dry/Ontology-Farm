"""Coordinated backup receipts and isolated restore attestations."""
from alembic import op

revision = '0406_storage_backups'
down_revision = '0405_storage_migration'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE storage_backups (
        id uuid PRIMARY KEY REFERENCES resource_heads(resource_id),
        manifest_digest text NOT NULL, authority_key_path text NOT NULL,
        pg_bin text NOT NULL, source_identity jsonb NOT NULL,
        data_digest text NOT NULL, data_position bigint NOT NULL,
        watermark jsonb NOT NULL, findings jsonb NOT NULL);
        CREATE TABLE storage_restore_receipts (
        id uuid PRIMARY KEY, backup_id uuid NOT NULL,
        manifest_digest text NOT NULL, configuration_digest text NOT NULL,
        targets jsonb NOT NULL, findings jsonb NOT NULL);
        GRANT SELECT ON storage_backups,storage_restore_receipts TO lzo_app''')


def downgrade():
    raise RuntimeError('Backup/recovery evidence must be retained')
