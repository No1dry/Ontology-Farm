"""Read-time visibility dependencies for references in immutable events."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID

revision = "0003a_artifact_visibility"
down_revision = "0003_artifacts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("revision_visibility_dependencies",
        sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("resource_scopes.resource_id"), primary_key=True))
    op.create_index("ix_revision_visibility_resource", "revision_visibility_dependencies", ["resource_id"])
    op.execute("""INSERT INTO revision_visibility_dependencies (event_id, resource_id)
        SELECT event_id, (payload->>'target_resource_id')::uuid
        FROM revisions WHERE event_type = 'artifact.linked'""")
    op.execute("GRANT SELECT, INSERT ON revision_visibility_dependencies TO lzo_app")


def downgrade():
    op.drop_table("revision_visibility_dependencies")
