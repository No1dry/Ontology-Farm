"""Create immutable revision ledger with a transactional position counter.

Revision ID: 0001_core_ledger
Revises:
"""
from alembic import op
from sqlalchemy import text

revision = "0001_core_ledger"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    connection.execute(text("""CREATE TABLE platform_watermarks (
        singleton integer PRIMARY KEY CHECK (singleton = 1),
        event_position bigint NOT NULL,
        input_watermark bigint NOT NULL,
        projection_watermark bigint NOT NULL,
        inference_watermark bigint NOT NULL,
        CHECK (event_position >= 0 AND input_watermark >= 0 AND
               projection_watermark >= 0 AND inference_watermark >= 0))"""))
    connection.execute(text("""CREATE TABLE resource_heads (
        resource_id uuid PRIMARY KEY,
        revision integer NOT NULL CHECK (revision >= 0),
        digest varchar(64), event_id uuid)"""))
    connection.execute(text("""CREATE TABLE revisions (
        event_id uuid PRIMARY KEY,
        resource_id uuid NOT NULL REFERENCES resource_heads(resource_id),
        revision integer NOT NULL,
        previous_revision integer NOT NULL,
        previous_digest varchar(64), content_digest varchar(64) NOT NULL,
        payload jsonb NOT NULL, event_type varchar(128) NOT NULL,
        actor_id uuid NOT NULL, request_id varchar(256) NOT NULL,
        effective_at timestamptz NOT NULL, recorded_at timestamptz NOT NULL,
        UNIQUE (resource_id, revision))"""))
    connection.execute(text("""CREATE TABLE audit_events (
        id uuid PRIMARY KEY, event_id uuid NOT NULL REFERENCES revisions(event_id),
        event_type varchar(128) NOT NULL, actor_id uuid NOT NULL,
        request_id varchar(256) NOT NULL, recorded_at timestamptz NOT NULL)"""))
    connection.execute(text("""CREATE TABLE outbox_events (
        id uuid PRIMARY KEY, event_id uuid NOT NULL REFERENCES revisions(event_id),
        position bigint NOT NULL UNIQUE, event_type varchar(128) NOT NULL,
        payload jsonb NOT NULL, recorded_at timestamptz NOT NULL)"""))
    connection.execute(text("""CREATE TABLE idempotency_results (
        actor_id uuid NOT NULL, key varchar(256) NOT NULL,
        body_digest varchar(64) NOT NULL, result jsonb,
        recorded_at timestamptz NOT NULL, PRIMARY KEY (actor_id, key))"""))
    connection.execute(text("""INSERT INTO platform_watermarks
        (singleton, event_position, input_watermark, projection_watermark, inference_watermark)
        VALUES (1, 0, 0, 0, 0) ON CONFLICT (singleton) DO NOTHING"""))
    connection.execute(text("GRANT SELECT, UPDATE ON platform_watermarks TO lzo_app"))
    connection.execute(text("GRANT SELECT, INSERT, UPDATE ON resource_heads TO lzo_app"))
    for table in ("revisions", "audit_events", "outbox_events"):
        connection.execute(text(f"GRANT SELECT, INSERT ON {table} TO lzo_app"))
    connection.execute(text("GRANT SELECT, INSERT, UPDATE ON idempotency_results TO lzo_app"))


def downgrade():
    for table in ("idempotency_results", "outbox_events", "audit_events",
                  "revisions", "resource_heads", "platform_watermarks"):
        op.execute(text(f"DROP TABLE {table}"))
