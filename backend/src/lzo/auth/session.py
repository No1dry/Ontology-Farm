"""Opaque backend session persistence and stable issuer/subject identities."""
from uuid import uuid4
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import secrets

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update

from sqlalchemy.dialects.postgresql import insert

from lzo.auth.models import users, sessions, login_states, grants, roles, resource_scopes
from lzo.auth.oidc import OIDCClient
from lzo.auth.permissions import current_version, principal_predicate
from lzo.auth.schemas import SessionDetail, UserDetail, PermissionSummary
from lzo.contracts import ActorContext
from lzo.errors import ApiError

SESSION_COOKIE = "__Host-lzo_session"
LOGIN_COOKIE = "__Host-lzo_login"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def resolve_identity(uow, issuer: str, subject: str, display_name: str, email: str | None, *, actor=None):
    # Email is display information, never an account-linking key.
    user_id = uuid4()
    statement = insert(users).values(id=user_id, issuer=issuer, subject=subject,
        display_name=display_name, email=email).on_conflict_do_nothing(
        index_elements=[users.c.issuer, users.c.subject]).returning(users.c.id)
    created = uow.session.execute(statement).scalar_one_or_none()
    if created is not None:
        from lzo.governance.revisions import append_revision
        append_revision(uow, actor or ActorContext(user_id, "identity-registration", current_version(uow)),
            user_id, 0, {"id": str(user_id), "issuer": issuer, "subject": subject,
                        "display_name": display_name, "email": email, "enabled": True}, "user.registered")
        uow.session.execute(insert(resource_scopes).values(
            resource_id=user_id, security_class="identity", resource_kind="user"))
        return user_id
    return uow.session.execute(select(users.c.id).where(users.c.issuer == issuer, users.c.subject == subject)).scalar_one()


class SessionService:
    def __init__(self, uow, settings):
        self.uow = uow
        self.settings = settings
        if not settings.session_encryption_key:
            raise ApiError("SESSION_UNAVAILABLE", 503, "Session encryption is not configured")
        self.cipher = Fernet(settings.session_encryption_key.encode())
        self.oidc = OIDCClient(settings)

    def encrypt(self, value):
        return self.cipher.encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, value):
        try:
            return json.loads(self.cipher.decrypt(value.encode()))
        except (InvalidToken, ValueError):
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required") from None

    def login(self):
        browser, state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(4))
        url = self.oidc.authorization_url(state, nonce, verifier)
        self.uow.session.execute(delete(login_states).where(login_states.c.expires_at < datetime.now(timezone.utc)))
        self.uow.session.execute(insert(login_states).values(id_hash=digest(browser), state_hash=digest(state),
            encrypted_data=self.encrypt({"nonce": nonce, "verifier": verifier}),
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=5)))
        return browser, url

    def consume_login(self, browser, state):
        if not browser or not state:
            raise ApiError("OIDC_STATE_INVALID", 401, "Invalid login state")
        row = self.uow.session.execute(delete(login_states).where(
            login_states.c.id_hash == digest(browser)).returning(login_states)).mappings().one_or_none()
        # Consume even a failed attempt; callback replay can never exchange twice.
        self.uow.commit()
        if row is None or row["expires_at"] <= datetime.now(timezone.utc) or not hmac.compare_digest(row["state_hash"], digest(state)):
            raise ApiError("OIDC_STATE_INVALID", 401, "Invalid login state")
        return self.decrypt(row["encrypted_data"])

    def callback(self, browser, state, code):
        temporary = self.consume_login(browser, state)
        tokens, claims = self.oidc.exchange(code, temporary["nonce"], temporary["verifier"])
        # Match administration's state -> user -> session lock order. Hold the
        # shared state lock through validation, session insertion and commit so
        # disable either rejects this callback or subsequently deletes its session.
        current_version(self.uow, lock=True)
        user_id = resolve_identity(self.uow, claims["iss"], claims["sub"],
            claims.get("name") or claims.get("preferred_username") or claims["sub"], claims.get("email"))
        enabled = self.uow.session.execute(select(users.c.enabled).where(users.c.id == user_id)).scalar_one()
        if not enabled:
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required")
        return self.create(user_id, tokens)

    def create(self, user_id, tokens):
        cookie = secrets.token_urlsafe(48)
        self.uow.session.execute(insert(sessions).values(id_hash=digest(cookie), user_id=user_id,
            encrypted_tokens=self.encrypt(tokens), csrf_token=secrets.token_urlsafe(32),
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=self.settings.session_lifetime_seconds)))
        return cookie

    def read(self, cookie, *, lock=False):
        if not cookie:
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required")
        query = select(sessions).where(sessions.c.id_hash == digest(cookie))
        if lock:
            query = query.with_for_update()
        row = self.uow.session.execute(query).mappings().one_or_none()
        if row is None or row["expires_at"] <= datetime.now(timezone.utc):
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required")
        user = self.uow.session.execute(select(users).where(users.c.id == row["user_id"])).mappings().one()
        if not user["enabled"]:
            raise ApiError("UNAUTHENTICATED", 401, "Authentication required")
        return row

    def csrf(self, row, token, origin=None):
        if not token or not hmac.compare_digest(row["csrf_token"], token) or (
                origin is not None and origin != self.settings.public_origin):
            raise ApiError("CSRF_INVALID", 403, "CSRF validation failed")

    def actor(self, row, request_id):
        return ActorContext(row["user_id"], request_id, current_version(self.uow))

    def detail(self, row):
        actor = self.actor(row, "session")
        user = self.uow.session.execute(select(users).where(users.c.id == row["user_id"])).mappings().one()
        permissions = self.uow.session.execute(select(grants.c.role_id, roles.c.actions, grants.c.scope_id,
            grants.c.resource_id, grants.c.security_class, grants.c.global_scope).select_from(grants.join(roles)).where(
                grants.c.active.is_(True), principal_predicate(actor))).mappings().all()
        return SessionDetail(user=UserDetail(**user), permissions=[PermissionSummary(**item) for item in permissions],
                             authorization_version=actor.authorization_version, csrf_token=row["csrf_token"])

    def refresh(self, row):
        tokens = self.decrypt(row["encrypted_tokens"])
        try:
            refreshed, claims = self.oidc.refresh(tokens)
            user = self.uow.session.execute(select(users).where(users.c.id == row["user_id"])).mappings().one()
            if claims["iss"] != user["issuer"] or claims["sub"] != user["subject"]:
                raise ApiError("OIDC_TOKEN_INVALID", 401, "Identity token validation failed")
            self.uow.session.execute(update(sessions).where(sessions.c.id_hash == row["id_hash"])
                                     .values(encrypted_tokens=self.encrypt(refreshed)))
        except ApiError:
            self.uow.session.execute(delete(sessions).where(sessions.c.id_hash == row["id_hash"]))
            self.uow.commit()
            raise

    def logout(self, row):
        tokens = self.decrypt(row["encrypted_tokens"])
        self.uow.session.execute(delete(sessions).where(sessions.c.id_hash == row["id_hash"]))
        self.uow.commit()
        return self.oidc.logout(tokens)
