"""Mutable identity projections; every business change also enters the ledger."""
from sqlalchemy import Boolean, CheckConstraint, Column, DateTime, ForeignKey, Integer, MetaData, String, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()
authorization_state = Table("authorization_state", metadata,
    Column("singleton", Integer, primary_key=True),
    Column("version", Integer, nullable=False), CheckConstraint("singleton = 1"))
users = Table("platform_users", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("issuer", String(1024), nullable=False), Column("subject", String(255), nullable=False),
    Column("display_name", String(255), nullable=False), Column("email", String(320)),
    Column("enabled", Boolean, nullable=False, server_default="true"),
    Column("revision", Integer, nullable=False, server_default="1"),
    UniqueConstraint("issuer", "subject"))
groups = Table("platform_groups", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("revision", Integer, nullable=False))
group_members = Table("group_members", metadata,
    Column("group_id", UUID(as_uuid=True), ForeignKey("platform_groups.id"), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("platform_users.id"), primary_key=True))
roles = Table("platform_roles", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("name", String(255), nullable=False),
    Column("actions", JSONB, nullable=False),
    Column("revision", Integer, nullable=False))
grants = Table("permission_grants", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("platform_users.id")),
    Column("group_id", UUID(as_uuid=True), ForeignKey("platform_groups.id")),
    Column("role_id", UUID(as_uuid=True), ForeignKey("platform_roles.id"), nullable=False),
    Column("scope_id", UUID(as_uuid=True)), Column("resource_id", UUID(as_uuid=True)),
    Column("security_class", String(64), nullable=False),
    Column("global_scope", Boolean, nullable=False, server_default="false"),
    Column("active", Boolean, nullable=False, server_default="true"),
    Column("revision", Integer, nullable=False),
    CheckConstraint("(user_id IS NOT NULL)::int + (group_id IS NOT NULL)::int = 1"),
    CheckConstraint("(scope_id IS NOT NULL)::int + (resource_id IS NOT NULL)::int + global_scope::int = 1"))
resource_scopes = Table("resource_scopes", metadata,
    Column("resource_id", UUID(as_uuid=True), primary_key=True),
    Column("scope_id", UUID(as_uuid=True)),
    Column("owner_scope_id", UUID(as_uuid=True)),
    Column("related_scope_ids", JSONB, nullable=False, server_default="[]"),
    Column("security_class", String(64), nullable=False),
    Column("resource_kind", String(128), nullable=False))
sessions = Table("bff_sessions", metadata,
    Column("id_hash", String(64), primary_key=True),
    Column("user_id", UUID(as_uuid=True), ForeignKey("platform_users.id"), nullable=False),
    Column("encrypted_tokens", String, nullable=False),
    Column("csrf_token", String(128), nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False))
login_states = Table("oidc_login_states", metadata,
    Column("id_hash", String(64), primary_key=True),
    Column("state_hash", String(64), nullable=False),
    Column("encrypted_data", String, nullable=False),
    Column("expires_at", DateTime(timezone=True), nullable=False))
identity_operations = Table("identity_operations", metadata,
    Column("id", UUID(as_uuid=True), primary_key=True),
    Column("actor_id", UUID(as_uuid=True), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("phase", String(32), nullable=False),
    Column("revision", Integer, nullable=False),
    Column("payload", JSONB, nullable=False),
    Column("provider_subject", String(255)),
    Column("user_id", UUID(as_uuid=True), ForeignKey("platform_users.id")),
    Column("error_code", String(128)))
