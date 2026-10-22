"""Durable checked legacy-store migration plans and per-version checkpoints."""
from alembic import op

revision = '0405_storage_migration'
down_revision = '0404_telemetry'
branch_labels = depends_on = None


def upgrade():
    op.execute('''CREATE TABLE storage_migration_plans (
        id uuid PRIMARY KEY REFERENCES resource_heads(resource_id),
        source_identity jsonb NOT NULL, target_identity jsonb NOT NULL,
        configuration_digest text NOT NULL, initial_watermark bigint NOT NULL,
        epoch bigint NOT NULL DEFAULT 0, state text NOT NULL DEFAULT 'backfill',
        reconciliation jsonb, backup jsonb, activation jsonb);
        CREATE TABLE storage_migration_items (
        plan_id uuid NOT NULL REFERENCES storage_migration_plans(id),
        kind text NOT NULL, version_key text NOT NULL, digest text NOT NULL,
        PRIMARY KEY(plan_id,kind,version_key));
        GRANT SELECT,INSERT,UPDATE ON storage_migration_plans TO lzo_app;
        GRANT SELECT,INSERT ON storage_migration_items TO lzo_app''')


def downgrade():
    raise RuntimeError('Storage migration history is retained; destructive downgrade is unsupported')
