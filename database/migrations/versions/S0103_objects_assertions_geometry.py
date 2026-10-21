"""Dynamic globally identified objects, assertion supports and PostGIS geometry."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "S0103_objects_facts_geometry"
down_revision = "S0102_ontology_releases"
branch_labels = depends_on = None


def upgrade():
    op.create_table("semantic_objects",
        sa.Column("id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True),
        sa.Column("iri", sa.String, nullable=False, unique=True),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("bundle_id", UUID, sa.ForeignKey("execution_bundles.id"), nullable=False),
        sa.Column("next_transition_at", sa.DateTime(timezone=True)),
        sa.Column("transition_origin_event_id", UUID))
    op.create_table("semantic_assertions",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("resource_id", UUID, sa.ForeignKey("semantic_objects.id"), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("revision_ref", JSONB, nullable=False),
        sa.Column("bundle_id", UUID, sa.ForeignKey("execution_bundles.id"), nullable=False))
    op.create_index("semantic_assertions_subject", "semantic_assertions", ["resource_id"])
    op.create_table("semantic_supports",
        sa.Column("id", UUID, primary_key=True),
        sa.Column("assertion_id", UUID, sa.ForeignKey("semantic_assertions.id"), nullable=False),
        sa.Column("payload", JSONB, nullable=False),
        sa.Column("active", sa.Boolean, nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("withdrawn_event_id", UUID, sa.ForeignKey("revisions.event_id")))
    op.create_index("semantic_supports_assertion", "semantic_supports", ["assertion_id"])
    op.create_table("semantic_object_dependencies",
        sa.Column("resource_id", UUID, sa.ForeignKey("semantic_objects.id"), primary_key=True),
        sa.Column("dependency_id", UUID, sa.ForeignKey("resource_heads.resource_id"), primary_key=True))
    op.create_table("semantic_type_declarations",
        sa.Column("resource_id", UUID, sa.ForeignKey("semantic_objects.id"), primary_key=True),
        sa.Column("class_iris", JSONB, nullable=False),
        sa.Column("revision_ref", JSONB, nullable=False),
        sa.Column("dependency_refs", JSONB, nullable=False))
    # PostGIS is provisioned by F01; qualify it because isolated test schemas
    # deliberately omit public from search_path.
    op.execute("""CREATE TABLE semantic_geometry (
        resource_id uuid PRIMARY KEY REFERENCES semantic_objects(id),
        revision integer NOT NULL, event_id uuid NOT NULL REFERENCES revisions(event_id),
        geom public.geometry(Geometry,4326) NOT NULL, payload jsonb NOT NULL,
        CHECK (NOT public.ST_IsEmpty(geom) AND public.ST_IsValid(geom)))""")
    op.execute("CREATE INDEX semantic_geometry_spatial ON semantic_geometry USING gist(geom)")
    op.execute("GRANT SELECT, INSERT, UPDATE ON semantic_objects, semantic_supports, semantic_geometry TO lzo_app")
    op.execute("GRANT SELECT, INSERT ON semantic_assertions, semantic_object_dependencies, semantic_type_declarations TO lzo_app")


def downgrade():
    for table in ("semantic_geometry", "semantic_type_declarations", "semantic_object_dependencies", "semantic_supports",
                  "semantic_assertions", "semantic_objects"):
        op.drop_table(table)
