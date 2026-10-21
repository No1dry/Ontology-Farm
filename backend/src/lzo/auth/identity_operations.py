"""Durable synchronous identity intents, with conservative email recovery.

Each method owns explicit commit boundaries. A sending intent left after process
interruption is ambiguous; retries never send that email again automatically.
The existing ledger/outbox records intent and outcomes for future worker use.
"""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import delete, insert, select, update

from lzo.auth import models
from lzo.auth.identity_admin import AuthorizationAdmin, KeycloakAdmin
from lzo.auth.permissions import current_version
from lzo.auth.schemas import IdentityOperationDetail, UserDetail
from lzo.auth.session import resolve_identity
from lzo.contracts import ActorContext
from lzo.errors import ApiError
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.revisions import append_revision


class IdentityOperations:
    def __init__(self, uow, settings):
        self.uow, self.settings = uow, settings

    def _authorize(self, actor):
        AuthorizationAdmin(self.uow)._authorize(actor)

    def _fresh(self, actor):
        fresh = ActorContext(actor.subject_id, actor.request_id, current_version(self.uow))
        self._authorize(fresh)
        return fresh

    def _row(self, operation_id, *, lock=True):
        query = select(models.identity_operations).where(models.identity_operations.c.id == operation_id)
        row = self.uow.session.execute(query.with_for_update() if lock else query).mappings().one_or_none()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "Identity operation not found")
        return dict(row)

    def _detail(self, row):
        user = None
        if row["phase"] == "completed" and row["payload"].get("result_user"):
            user = UserDetail(**row["payload"]["result_user"])
        elif row["user_id"]:
            user = UserDetail(**self.uow.session.execute(select(models.users).where(
                models.users.c.id == row["user_id"])).mappings().one())
        return IdentityOperationDetail(**{key: row[key] for key in (
            "id", "kind", "phase", "revision", "provider_subject", "error_code")}, user=user)

    def _record(self, actor, row, **changes):
        expected = row["revision"]
        row.update(changes, revision=expected + 1)
        if row["phase"] == "completed" and row["user_id"]:
            user = self.uow.session.execute(select(models.users).where(
                models.users.c.id == row["user_id"])).mappings().one()
            row["payload"] = row["payload"] | {"result_user": UserDetail(**user).model_dump(mode="json")}
        self.uow.session.execute(update(models.identity_operations).where(
            models.identity_operations.c.id == row["id"]).values(**row))
        payload = {key: str(value) if key in {"id", "actor_id", "user_id"} and value is not None else value
                   for key, value in row.items()}
        append_revision(self.uow, actor, row["id"], expected, payload, "identity." + row["phase"])

    def _prepare(self, actor, key, kind, payload, command):
        self._authorize(actor)
        encoded = json.dumps({"kind": kind, "payload": payload}, sort_keys=True, separators=(",", ":"))
        def create():
            row = dict(id=uuid4(), actor_id=actor.subject_id, kind=kind, phase="pending", revision=0,
                       payload=payload, provider_subject=None, user_id=None, error_code=None)
            self.uow.session.execute(insert(models.identity_operations).values(**row))
            command(row)
            self.uow.session.execute(insert(models.resource_scopes).values(resource_id=row["id"],
                security_class="identity", resource_kind="identity_operation"))
            self._record(actor, row, phase="provider_pending")
            return {"operation_id": str(row["id"])}
        result = execute_idempotent(self.uow, actor, key, hashlib.sha256(encoded.encode()).hexdigest(), create)
        self.uow.commit()
        from uuid import UUID
        return self._detail(self._row(UUID(result["operation_id"]), lock=False))

    def prepare_invite(self, actor, key, body):
        return self._prepare(actor, key, "invite", body.model_dump(mode="json"), lambda row: None)

    def prepare_disable(self, actor, key, user_id, expected, body):
        def local_change(row):
            current = AuthorizationAdmin(self.uow)._existing(models.users, user_id, expected)
            if current["issuer"] != self.settings.oidc_issuer:
                raise ApiError("IDENTITY_PROVIDER_MISMATCH", 422, "User belongs to another identity provider")
            row.update(user_id=user_id, provider_subject=current["subject"])
            if not body.enabled:
                self.uow.session.execute(update(models.users).where(models.users.c.id == user_id)
                    .values(enabled=False, revision=expected + 1))
                self.uow.session.execute(delete(models.sessions).where(models.sessions.c.user_id == user_id))
                updated = dict(current) | {"enabled": False, "revision": expected + 1}
                AuthorizationAdmin(self.uow)._audit(actor, user_id, expected,
                    UserDetail(**updated).model_dump(mode="json"), "user.disabled")
        return self._prepare(actor, key, "set_enabled",
            {"user_id": str(user_id), "expected_revision": expected, "enabled": body.enabled}, local_change)

    def get(self, actor, operation_id):
        self._authorize(actor)
        return self._detail(self._row(operation_id))

    def attempt(self, actor, operation_id):
        actor = self._fresh(actor)
        row = self._row(operation_id)
        if row["phase"] in {"completed", "failed", "delivery_unknown"}:
            return self._detail(row)
        if row["phase"] == "sending":
            self._record(actor, row, phase="delivery_unknown", error_code="INVITATION_DELIVERY_UNKNOWN")
            self.uow.commit()
            return self._detail(row)
        adapter = KeycloakAdmin(self.settings)
        if row["kind"] == "set_enabled":
            # The durable local disable is already committed, even if this call
            # crashes. This provider operation is idempotent and safe to retry.
            current = self.uow.session.execute(select(models.users).where(
                models.users.c.id == row["user_id"]).with_for_update()).mappings().one()
            expected_current = row["payload"]["expected_revision"] + (0 if row["payload"]["enabled"] else 1)
            if current["revision"] != expected_current:
                self._record(actor, row, phase="failed", error_code="REVISION_CONFLICT")
                self.uow.commit()
                return self._detail(row)
            try:
                adapter.set_enabled(row["provider_subject"], row["payload"]["enabled"])
            except ApiError as error:
                self._record(actor, row, phase="provider_pending", error_code=error.code)
                self.uow.commit()
                return self._detail(row)
            if row["payload"]["enabled"]:
                updated = dict(current) | {"enabled": True, "revision": current["revision"] + 1}
                self.uow.session.execute(update(models.users).where(models.users.c.id == row["user_id"])
                                         .values(enabled=True, revision=updated["revision"]))
                AuthorizationAdmin(self.uow)._audit(actor, row["user_id"], current["revision"],
                    UserDetail(**updated).model_dump(mode="json"), "user.enabled")
            self._record(actor, row, phase="completed", error_code=None)
            self.uow.commit()
            return self._detail(row)
        if not self.settings.invitation_smtp_configured:
            self._record(actor, row, phase="failed", error_code="INVITATION_MAIL_UNAVAILABLE")
            self.uow.commit()
            return self._detail(row)
        if not row["provider_subject"]:
            try:
                subject = adapter.ensure_invited_identity(row["id"], row["payload"]["email"], row["payload"]["display_name"])
            except ApiError as error:
                self._record(actor, row, phase="provider_pending", error_code=error.code)
                self.uow.commit()
                return self._detail(row)
            user_id = resolve_identity(self.uow, self.settings.oidc_issuer, subject,
                row["payload"]["display_name"], row["payload"]["email"], actor=actor)
            self._record(actor, row, phase="provider_created", provider_subject=subject, user_id=user_id)
            self.uow.commit()
        actor = self._fresh(actor)
        row = self._row(operation_id)
        if row["phase"] != "provider_created":
            return self._detail(row)
        self._record(actor, row, phase="sending")
        self.uow.commit()
        actor = self._fresh(actor)
        row = self._row(operation_id)
        if row["phase"] != "sending":
            return self._detail(row)
        try:
            adapter.send_invitation(row["provider_subject"])
        except ApiError as error:
            # A transport timeout could occur after acceptance. Never claim
            # failed delivery or silently resend an ambiguous email.
            self._record(actor, row, phase="delivery_unknown", error_code="INVITATION_DELIVERY_UNKNOWN")
            self.uow.commit()
            return self._detail(row)
        self._record(actor, row, phase="completed", error_code=None)
        self.uow.commit()
        return self._detail(row)
