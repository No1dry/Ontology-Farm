"""Identity projections, encrypted sessions and independent resource scopes."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0002_identity_authorization"
down_revision = "0001_core_ledger"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("authorization_state", sa.Column("singleton", sa.Integer, primary_key=True),
                    sa.Column("version", sa.Integer, nullable=False), sa.CheckConstraint("singleton = 1"))
    op.execute("INSERT INTO authorization_state VALUES (1,1)")
    op.create_table("platform_users",
        sa.Column("id", UUID, primary_key=True), sa.Column("issuer", sa.String(1024), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False), sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("email", sa.String(320)), sa.Column("enabled", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("revision", sa.Integer, nullable=False, server_default="1"), sa.UniqueConstraint("issuer", "subject"))
    op.create_table("platform_groups", sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("revision", sa.Integer, nullable=False))
    op.create_table("group_members", sa.Column("group_id", UUID, sa.ForeignKey("platform_groups.id"), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("platform_users.id"), primary_key=True))
    op.create_table("platform_roles", sa.Column("id", UUID, primary_key=True),
        sa.Column("name", sa.String(255), nullable=False), sa.Column("actions", JSONB, nullable=False),
        sa.Column("revision", sa.Integer, nullable=False))
    op.create_table("permission_grants", sa.Column("id", UUID, primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("platform_users.id")),
        sa.Column("group_id", UUID, sa.ForeignKey("platform_groups.id")),
        sa.Column("role_id", UUID, sa.ForeignKey("platform_roles.id"), nullable=False),
        sa.Column("scope_id", UUID), sa.Column("resource_id", UUID),
        sa.Column("security_class", sa.String(64), nullable=False),
        sa.Column("global_scope", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("active", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.CheckConstraint("(user_id IS NOT NULL)::int + (group_id IS NOT NULL)::int = 1"),
        sa.CheckConstraint("(scope_id IS NOT NULL)::int + (resource_id IS NOT NULL)::int + global_scope::int = 1"))
    op.create_table("resource_scopes", sa.Column("resource_id", UUID, primary_key=True),
        sa.Column("scope_id", UUID), sa.Column("owner_scope_id", UUID),
        sa.Column("related_scope_ids", JSONB, nullable=False, server_default="[]"),
        sa.Column("security_class", sa.String(64), nullable=False),
        sa.Column("resource_kind", sa.String(128), nullable=False))
    op.create_table("bff_sessions", sa.Column("id_hash", sa.String(64), primary_key=True),
        sa.Column("user_id", UUID, sa.ForeignKey("platform_users.id"), nullable=False),
        sa.Column("encrypted_tokens", sa.String, nullable=False), sa.Column("csrf_token", sa.String(128), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("oidc_login_states", sa.Column("id_hash", sa.String(64), primary_key=True),
        sa.Column("state_hash", sa.String(64), nullable=False), sa.Column("encrypted_data", sa.String, nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False))
    op.create_table("revision_metadata", sa.Column("event_id", UUID, sa.ForeignKey("revisions.event_id"), primary_key=True),
        sa.Column("task_id", UUID), sa.Column("bundle_id", UUID),
        sa.Column("evidence_refs", JSONB, nullable=False, server_default="[]"),
        sa.Column("proof_refs", JSONB, nullable=False, server_default="[]"))
    op.create_index("ix_resource_scope_security", "resource_scopes", ["scope_id", "security_class"])
    op.create_index("ix_grant_user", "permission_grants", ["user_id"])
    op.create_index("ix_grant_group", "permission_grants", ["group_id"])
    for table in ("authorization_state", "platform_users", "platform_groups", "platform_roles", "permission_grants", "resource_scopes"):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE ON {table} TO lzo_app"))
    for table in ("bff_sessions", "oidc_login_states", "group_members"):
        op.execute(sa.text(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO lzo_app"))
    op.execute("GRANT SELECT, INSERT ON revision_metadata TO lzo_app")


def downgrade():
    for table in ("revision_metadata", "oidc_login_states", "bff_sessions", "resource_scopes",
                  "permission_grants", "platform_roles", "group_members", "platform_groups", "platform_users", "authorization_state"):
        op.drop_table(table)
