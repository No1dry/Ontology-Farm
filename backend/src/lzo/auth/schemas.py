"""Public identity DTOs deliberately exclude all provider tokens."""
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class UserDetail(StrictModel):
    id: UUID
    issuer: str
    subject: str
    display_name: str
    email: str | None
    enabled: bool
    revision: int


class UserWrite(StrictModel):
    enabled: bool


class InvitationWrite(StrictModel):
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    display_name: str = Field(min_length=1, max_length=255)


class GroupWrite(StrictModel):
    name: str = Field(min_length=1, max_length=255)


class GroupDetail(GroupWrite):
    id: UUID
    revision: int


class MembersWrite(StrictModel):
    user_ids: list[UUID]
    approval_id: UUID | None = None


class MembersDetail(StrictModel):
    id: UUID
    revision: int
    user_ids: list[UUID]


class RoleWrite(GroupWrite):
    actions: list[str] = Field(min_length=1, max_length=100)
    approval_id: UUID | None = None

    @model_validator(mode="after")
    def valid_actions(self):
        import re
        if any(not re.fullmatch(r"[a-z][a-z0-9_.:-]{0,127}", value) for value in self.actions):
            raise ValueError("Invalid action name")
        self.actions = sorted(set(self.actions))
        return self


class RoleDetail(RoleWrite):
    id: UUID
    revision: int


class GrantWrite(StrictModel):
    approval_id: UUID | None = None
    user_id: UUID | None = None
    group_id: UUID | None = None
    role_id: UUID
    scope_id: UUID | None = None
    resource_id: UUID | None = None
    security_class: str = Field(pattern=r"^[a-z][a-z0-9_.-]{0,63}$")
    global_scope: bool = False

    @model_validator(mode="after")
    def complete_binding(self):
        if (self.user_id is not None) + (self.group_id is not None) != 1:
            raise ValueError("Exactly one principal is required")
        if (self.scope_id is not None) + (self.resource_id is not None) + self.global_scope != 1:
            raise ValueError("Exactly one scope or resource is required")
        return self


class GrantDetail(GrantWrite):
    id: UUID
    revision: int
    active: bool


class PermissionSummary(StrictModel):
    role_id: UUID
    actions: list[str]
    scope_id: UUID | None
    resource_id: UUID | None
    security_class: str
    global_scope: bool


class SessionDetail(StrictModel):
    user: UserDetail
    permissions: list[PermissionSummary]
    authorization_version: int
    csrf_token: str


class LogoutDetail(StrictModel):
    logged_out: bool
    provider_logged_out: bool


class IdentityOperationDetail(StrictModel):
    id: UUID
    kind: str
    phase: str
    revision: int
    provider_subject: str | None
    user: UserDetail | None
    error_code: str | None
