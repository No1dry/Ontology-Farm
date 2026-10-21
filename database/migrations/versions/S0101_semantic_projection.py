"""Exact RDF projection receipts, explicit classifications and inference fence."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "S0101_semantic_projection"
down_revision = "0005a_approval_footprint_seed"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("projection_events",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("position", sa.BigInteger, sa.ForeignKey("outbox_events.position"), nullable=False, unique=True),
        sa.Column("producer_version", sa.String(128), nullable=False),
        sa.Column("reference_ids", JSONB, nullable=False),
        sa.Column("dependency_refs", JSONB, nullable=False),
        sa.Column("initiator_id", UUID, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.Column("error_code", sa.String(128)),
        sa.CheckConstraint("state IN ('pending','synced','superseded','failed')"),
        sa.CheckConstraint("revision > 0 AND position > 0"),
        sa.UniqueConstraint("resource_id", "revision"))
    op.create_table("projection_heads",
        sa.Column("resource_id", UUID, primary_key=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("position", sa.BigInteger, nullable=False),
        sa.Column("digest", sa.String(64), nullable=False),
        sa.CheckConstraint("revision > 0 AND position > 0"))
    op.create_table("projection_classifications",
        sa.Column("position", sa.BigInteger, sa.ForeignKey("outbox_events.position"), primary_key=True),
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), nullable=False, unique=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("version", sa.String(128), nullable=False),
        sa.CheckConstraint("kind IN ('semantic','nonsemantic')"))
    op.create_table("inference_fence",
        sa.Column("singleton", sa.Integer, primary_key=True),
        sa.Column("expected_position", sa.BigInteger, nullable=False),
        sa.Column("resolved_position", sa.BigInteger, nullable=False),
        sa.CheckConstraint("singleton = 1"),
        sa.CheckConstraint("expected_position >= resolved_position AND resolved_position >= 0"))
    op.execute("INSERT INTO inference_fence VALUES (1,0,0)")
    op.create_table("inference_inputs",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("position", sa.BigInteger, nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.CheckConstraint("kind IN ('facts','schema','rules')"))
    op.create_table("inference_requests",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("position", sa.BigInteger, nullable=False),
        sa.Column("resource_id", UUID, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.CheckConstraint("state IN ('pending','resolved','superseded')"))
    op.execute("GRANT SELECT, INSERT, UPDATE ON projection_events, projection_heads, inference_fence, inference_requests TO lzo_app")
    op.execute("GRANT SELECT, INSERT ON projection_classifications, inference_inputs TO lzo_app")


def downgrade():
    for name in ("inference_requests", "inference_inputs", "inference_fence",
                 "projection_classifications", "projection_heads", "projection_events"):
        op.drop_table(name)
