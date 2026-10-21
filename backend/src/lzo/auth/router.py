"""Same-origin BFF and typed authorization administration endpoints."""
from dataclasses import dataclass
import hashlib
import json
from uuid import UUID
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from sqlalchemy import select

from lzo.auth import models
from lzo.auth.identity_admin import AuthorizationAdmin
from lzo.auth.permissions import permission_context, require_permission
from lzo.auth.schemas import (GrantDetail, GrantWrite, GroupDetail, GroupWrite, InvitationWrite,
    IdentityOperationDetail, LogoutDetail, MembersDetail, MembersWrite, RoleDetail, RoleWrite, SessionDetail, UserDetail, UserWrite)
from lzo.auth.session import LOGIN_COOKIE, SESSION_COOKIE, SessionService
from lzo.contracts import ActorContext, Page, ResourceScope
from lzo.db import PgUnitOfWork
from lzo.errors import ApiError, parse_if_match, require_idempotency_key
from lzo.governance.idempotency import execute_idempotent

router = APIRouter(prefix="/api/v1", tags=["Identity"])


@dataclass
class AuthRequest:
    uow: PgUnitOfWork
    service: SessionService
    actor: ActorContext
    row: dict


async def authenticated(request: Request):
    with PgUnitOfWork(request.app.state.engine) as uow:
        service = SessionService(uow, request.app.state.settings)
        row = service.read(request.cookies.get(SESSION_COOKIE))
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            service.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
        with permission_context(uow):
            yield AuthRequest(uow, service, service.actor(row, request.state.request_id), row)


def secure_cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, secure=True, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    response.headers["Referrer-Policy"] = "no-referrer"


@router.get("/session/login", operation_id="sessionLogin", status_code=302, response_class=RedirectResponse)
def login(request: Request):
    with PgUnitOfWork(request.app.state.engine) as uow:
        service = SessionService(uow, request.app.state.settings)
        cookie, url = service.login()
        uow.commit()
    response = RedirectResponse(url, status_code=302)
    secure_cookie(response, LOGIN_COOKIE, cookie, 300)
    return response


def accepts_html(request: Request) -> bool:
    """Require an explicit acceptable HTML media range; wildcard alone is API."""
    for entry in request.headers.get("accept", "").split(","):
        media, *parameters = entry.strip().lower().split(";")
        if media.strip() != "text/html":
            continue
        quality = 1.0
        for parameter in parameters:
            key, separator, value = parameter.strip().partition("=")
            if key == "q":
                try:
                    quality = float(value) if separator else 0
                except ValueError:
                    quality = 0
        if 0 < quality <= 1:
            return True
    return False


@router.get("/session/callback", operation_id="sessionCallback", status_code=303, response_class=RedirectResponse)
def callback(request: Request, state: str | None = None, code: str | None = None):
    html = accepts_html(request)
    try:
        with PgUnitOfWork(request.app.state.engine) as uow:
            service = SessionService(uow, request.app.state.settings)
            cookie = service.callback(request.cookies.get(LOGIN_COOKIE), state, code)
            uow.commit()
    except ApiError as error:
        if not html or error.code not in {"OIDC_STATE_INVALID", "OIDC_TOKEN_INVALID", "OIDC_UNAVAILABLE", "UNAUTHENTICATED"}:
            raise
        response = RedirectResponse("/auth/error?" + urlencode({"code": error.code, "request_id": request.state.request_id}), status_code=303)
        response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response
    response = RedirectResponse("/auth/complete" if html else "/api/v1/session", status_code=303)
    response.delete_cookie(LOGIN_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    secure_cookie(response, SESSION_COOKIE, cookie, request.app.state.settings.session_lifetime_seconds)
    return response


@router.get("/session", response_model=SessionDetail, operation_id="getSession")
def session(response: Response, auth: AuthRequest = Depends(authenticated)):
    response.headers["Cache-Control"] = "no-store"
    return auth.service.detail(auth.row)


@router.post("/session/refresh", response_model=SessionDetail, operation_id="refreshSession")
def refresh(request: Request, response: Response):
    with PgUnitOfWork(request.app.state.engine) as uow:
        service = SessionService(uow, request.app.state.settings)
        # Row lock spans provider refresh and token replacement across processes.
        row = service.read(request.cookies.get(SESSION_COOKIE), lock=True)
        service.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
        service.refresh(row)
        result = service.detail(row)
        uow.commit()
    response.headers["Cache-Control"] = "no-store"
    return result


@router.post("/session/logout", response_model=LogoutDetail, operation_id="logoutSession")
def logout(request: Request, response: Response):
    with PgUnitOfWork(request.app.state.engine) as uow:
        service = SessionService(uow, request.app.state.settings)
        row = service.read(request.cookies.get(SESSION_COOKIE), lock=True)
        service.csrf(row, request.headers.get("X-CSRF-Token"), request.headers.get("Origin"))
        provider = service.logout(row)
    response.delete_cookie(SESSION_COOKIE, secure=True, httponly=True, samesite="lax", path="/")
    response.headers["Cache-Control"] = "no-store"
    return LogoutDetail(logged_out=True, provider_logged_out=provider)


def admin(auth):
    require_permission(auth.actor, "identity.manage", ResourceScope(None, None, (), "identity"), uow=auth.uow)


def list_page(auth, table, dto, cursor, predicate=None):
    admin(auth)
    query = select(table)
    if predicate is not None:
        query = query.where(predicate)
    if cursor:
        try:
            after = UUID(cursor)
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
        query = query.where(table.c.id > after)
    size = min(auth.service.settings.list_page_size, auth.service.settings.list_max_page_size)
    rows = auth.uow.session.execute(query.order_by(table.c.id).limit(size + 1)).mappings().all()
    return Page([dto(**row) for row in rows[:size]], str(rows[size - 1]["id"]) if len(rows) > size else None)


def mutation(request, auth, body, command, *, existing=False):
    # Serialize authorization mutations before checking/replaying an idempotency
    # result. A revoked administrator cannot use replay to recover private data.
    auth.uow.session.execute(select(models.authorization_state).with_for_update()).one()
    admin(auth)
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    expected = parse_if_match(request.headers.get("If-Match")) if existing else None
    encoded = json.dumps({"method": request.method, "path": request.url.path, "body": body,
                          "revision": expected}, sort_keys=True, separators=(",", ":"))
    result = execute_idempotent(auth.uow, auth.actor, key, hashlib.sha256(encoded.encode()).hexdigest(),
        lambda: command(expected).model_dump(mode="json"))
    auth.uow.commit()
    return result


@router.get("/users", response_model=Page[UserDetail], operation_id="listUsers")
def list_users(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return list_page(auth, models.users, UserDetail, cursor)


def operation_result(operations, auth, intent):
    result = operations.attempt(auth.actor, intent.id)
    if result.phase != "completed":
        code = result.error_code or "IDENTITY_OPERATION_PENDING"
        status = 409 if code == "REVISION_CONFLICT" or result.phase in {"delivery_unknown", "sending"} else 503
        raise ApiError(code, status, "Identity operation is incomplete; inspect identity operations")
    return result


@router.post("/users/invitations", response_model=IdentityOperationDetail, status_code=201, operation_id="inviteUser")
def invite_user(request: Request, body: InvitationWrite, auth: AuthRequest = Depends(authenticated)):
    from lzo.auth.identity_operations import IdentityOperations
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    operations = IdentityOperations(auth.uow, request.app.state.settings)
    intent = operations.prepare_invite(auth.actor, key, body)
    return operation_result(operations, auth, intent)


@router.patch("/users/{user_id}", response_model=IdentityOperationDetail, operation_id="updateUser")
def update_user(user_id: UUID, body: UserWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    from lzo.auth.identity_operations import IdentityOperations
    key = require_idempotency_key(request.headers.get("Idempotency-Key"))
    expected = parse_if_match(request.headers.get("If-Match"))
    operations = IdentityOperations(auth.uow, request.app.state.settings)
    intent = operations.prepare_disable(auth.actor, key, user_id, expected, body)
    return operation_result(operations, auth, intent)


@router.get("/identity-operations/{operation_id}", response_model=IdentityOperationDetail, operation_id="getIdentityOperation")
def identity_operation(operation_id: UUID, auth: AuthRequest = Depends(authenticated)):
    from lzo.auth.identity_operations import IdentityOperations
    return IdentityOperations(auth.uow, auth.service.settings).get(auth.actor, operation_id)


@router.get("/identity-operations", response_model=Page[IdentityOperationDetail], operation_id="listIdentityOperations")
def identity_operations(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    from lzo.auth.identity_operations import IdentityOperations
    operations = IdentityOperations(auth.uow, auth.service.settings)
    admin(auth)
    query = select(models.identity_operations).order_by(models.identity_operations.c.id)
    if cursor:
        try:
            query = query.where(models.identity_operations.c.id > UUID(cursor))
        except ValueError:
            raise ApiError("INVALID_CURSOR", 422, "Invalid list cursor") from None
    size = min(auth.service.settings.list_page_size, auth.service.settings.list_max_page_size)
    rows = auth.uow.session.execute(query.limit(size + 1)).mappings().all()
    return Page([operations._detail(row) for row in rows[:size]], str(rows[size - 1]["id"]) if len(rows) > size else None)


@router.get("/groups", response_model=Page[GroupDetail], operation_id="listGroups")
def list_groups(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return list_page(auth, models.groups, GroupDetail, cursor)


@router.post("/groups", response_model=GroupDetail, status_code=201, operation_id="createGroup")
def create_group(body: GroupWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"),
                    lambda _: AuthorizationAdmin(auth.uow).create_group(auth.actor, body))


@router.patch("/groups/{group_id}", response_model=GroupDetail, operation_id="updateGroup")
def update_group(group_id: UUID, body: GroupWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"),
                    lambda expected: AuthorizationAdmin(auth.uow).update_group(auth.actor, group_id, expected, body), existing=True)


@router.get("/groups/{group_id}/members", response_model=Page[UserDetail], operation_id="listGroupMembers")
def members(group_id: UUID, cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    admin(auth)
    row = auth.uow.session.execute(select(models.groups).where(models.groups.c.id == group_id)).mappings().one_or_none()
    if row is None:
        raise ApiError("NOT_FOUND", 404, "Resource not found")
    return list_page(auth, models.users, UserDetail, cursor, models.users.c.id.in_(
        select(models.group_members.c.user_id).where(models.group_members.c.group_id == group_id)))


@router.put("/groups/{group_id}/members", response_model=MembersDetail, operation_id="setGroupMembers")
def set_members(group_id: UUID, body: MembersWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"), lambda expected: AuthorizationAdmin(auth.uow).set_members(
        auth.actor, group_id, expected, body.user_ids, approval_id=body.approval_id), existing=True)


@router.get("/roles", response_model=Page[RoleDetail], operation_id="listRoles")
def list_roles(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return list_page(auth, models.roles, RoleDetail, cursor)


@router.post("/roles", response_model=RoleDetail, status_code=201, operation_id="createRole")
def create_role(body: RoleWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"), lambda _: AuthorizationAdmin(auth.uow).create_role(auth.actor, body))


@router.patch("/roles/{role_id}", response_model=RoleDetail, operation_id="updateRole")
def update_role(role_id: UUID, body: RoleWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"), lambda expected: AuthorizationAdmin(auth.uow).update_role(
        auth.actor, role_id, expected, body), existing=True)


@router.get("/grants", response_model=Page[GrantDetail], operation_id="listGrants")
def list_grants(cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return list_page(auth, models.grants, GrantDetail, cursor)


@router.post("/grants", response_model=GrantDetail, status_code=201, operation_id="createGrant")
def create_grant(body: GrantWrite, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, body.model_dump(mode="json"), lambda _: AuthorizationAdmin(auth.uow).create_grant(auth.actor, body))


@router.delete("/grants/{grant_id}", response_model=GrantDetail, operation_id="revokeGrant")
def revoke_grant(grant_id: UUID, request: Request, auth: AuthRequest = Depends(authenticated)):
    return mutation(request, auth, {}, lambda expected: AuthorizationAdmin(auth.uow).revoke_grant(auth.actor, grant_id, expected), existing=True)
