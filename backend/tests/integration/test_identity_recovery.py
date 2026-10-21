"""Real provider effects with injected process interruption at commit boundaries."""
from uuid import uuid4
import pytest
from sqlalchemy import select


def test_disable_intent_revokes_local_session_before_provider_attempt(scope_scenario, settings, monkeypatch):
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.session import SessionService
    from lzo.auth.schemas import UserWrite
    from lzo.auth.models import sessions, users
    from lzo.auth.identity_admin import KeycloakAdmin
    from lzo.errors import ApiError
    s = scope_scenario
    with s.transaction() as uow:
        service = SessionService(uow, settings)
        cookie = service.create(s.users["alice"], {"synthetic": True})
        # A platform user whose issuer is the configured provider; the injected
        # crash is before the provider is contacted, so no external account needed.
        from sqlalchemy import update
        uow.session.execute(update(users).where(users.c.id == s.users["alice"]).values(issuer=settings.oidc_issuer))
        uow.commit()
        operation = IdentityOperations(uow, settings).prepare_disable(
            s.admin_actor(), "disable-" + uuid4().hex, s.users["alice"], 1, UserWrite(enabled=False))
    with s.transaction() as uow:
        assert uow.session.execute(select(users.c.enabled).where(users.c.id == s.users["alice"])).scalar_one() is False
        with pytest.raises(ApiError):
            SessionService(uow, settings).read(cookie)
    def crash(*args, **kwargs):
        raise RuntimeError("injected process interruption before provider call")
    monkeypatch.setattr(KeycloakAdmin, "set_enabled", crash)
    with s.transaction() as uow:
        with pytest.raises(RuntimeError):
            IdentityOperations(uow, settings).attempt(s.admin_actor(), operation.id)
    with s.transaction() as uow:
        detail = IdentityOperations(uow, settings).get(s.admin_actor(), operation.id)
        assert detail.phase == "provider_pending"
        assert uow.session.execute(select(sessions.c.id_hash)).first() is None


def test_real_smtp_success_before_local_completion_remains_unknown_without_resend(scope_scenario, settings, monkeypatch):
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.schemas import InvitationWrite
    from lzo.auth.models import identity_operations
    from lzo.auth.identity_admin import KeycloakAdmin
    s = scope_scenario
    email = "recovery-" + uuid4().hex + "@example.invalid"
    with s.transaction() as uow:
        operations = IdentityOperations(uow, settings)
        intent = operations.prepare_invite(s.admin_actor(), "invite-" + uuid4().hex,
                                          InvitationWrite(email=email, display_name="Recovery synthetic"))
        original_commit = uow.commit
        def crash_after_provider_success():
            phase = uow.session.execute(select(identity_operations.c.phase).where(identity_operations.c.id == intent.id)).scalar_one()
            if phase == "completed":
                raise RuntimeError("injected process interruption after SMTP before local commit")
            original_commit()
        monkeypatch.setattr(uow, "commit", crash_after_provider_success)
        with pytest.raises(RuntimeError):
            operations.attempt(s.admin_actor(), intent.id)
    def unexpected_resend(*args, **kwargs):
        raise AssertionError("Ambiguous email delivery must not be retried automatically")
    monkeypatch.setattr(KeycloakAdmin, "send_invitation", unexpected_resend)
    with s.transaction() as uow:
        result = IdentityOperations(uow, settings).attempt(s.admin_actor(), intent.id)
        assert result.phase == "delivery_unknown"
        assert result.provider_subject is not None and result.user is not None
    from pathlib import Path
    mailbox = Path(__file__).resolve().parents[3] / ".runtime/f03-mail"
    assert sum(email.encode() in file.read_bytes() for file in mailbox.glob("*.eml")) == 1


def test_old_disable_does_not_override_a_newer_user_revision(scope_scenario, settings, monkeypatch):
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.identity_admin import KeycloakAdmin
    from lzo.auth.schemas import UserWrite
    from lzo.auth.models import users
    from sqlalchemy import update
    s = scope_scenario
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == s.users["alice"]).values(issuer=settings.oidc_issuer))
        uow.commit()
        intent = IdentityOperations(uow, settings).prepare_disable(s.admin_actor(), "old-disable", s.users["alice"], 1, UserWrite(enabled=False))
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == s.users["alice"]).values(enabled=True, revision=3))
        uow.commit()
    def unexpected_provider_change(*args, **kwargs):
        raise AssertionError("Superseded intent must not change the provider")
    monkeypatch.setattr(KeycloakAdmin, "set_enabled", unexpected_provider_change)
    with s.transaction() as uow:
        result = IdentityOperations(uow, settings).attempt(s.admin_actor(), intent.id)
        assert result.phase == "failed"
        assert result.error_code == "REVISION_CONFLICT"


def test_completed_invitation_replay_preserves_original_result(scope_scenario, settings):
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.schemas import InvitationWrite
    from lzo.auth.models import users
    from sqlalchemy import update
    s = scope_scenario
    body = InvitationWrite(email="replay-" + uuid4().hex + "@example.invalid", display_name="Original")
    with s.transaction() as uow:
        operations = IdentityOperations(uow, settings)
        intent = operations.prepare_invite(s.admin_actor(), "stable-replay", body)
        completed = operations.attempt(s.admin_actor(), intent.id)
        assert completed.phase == "completed"
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == completed.user.id).values(display_name="Changed later"))
        uow.commit()
    with s.transaction() as uow:
        operations = IdentityOperations(uow, settings)
        replay = operations.prepare_invite(s.admin_actor(), "stable-replay", body)
        assert operations.attempt(s.admin_actor(), replay.id) == completed


def test_inflight_callback_cannot_survive_disable_and_reenable(scope_scenario, settings, monkeypatch):
    """Real PG interleaving at the callback's enabled-read/session-insert boundary."""
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from time import monotonic, sleep
    from urllib.parse import parse_qs, urlparse
    from sqlalchemy import text, update
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.identity_admin import KeycloakAdmin
    from lzo.auth.models import users
    from lzo.auth.oidc import OIDCClient
    from lzo.auth.schemas import UserWrite
    from lzo.auth.session import SessionService
    from lzo.errors import ApiError

    s = scope_scenario
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == s.users["alice"])
                            .values(issuer=settings.oidc_issuer))
        browser, url = SessionService(uow, settings).login()
        state = parse_qs(urlparse(url).query)["state"][0]
        uow.commit()
    # Only the external identity-provider boundary is bypassed: callback,
    # prepare_disable, enable, sessions and all locking use the actual services.
    monkeypatch.setattr(OIDCClient, "exchange", lambda *args: (
        {"synthetic": True}, {"iss": settings.oidc_issuer, "sub": "alice"}))
    monkeypatch.setattr(KeycloakAdmin, "set_enabled", lambda *args: None)
    at_insert, resume, disable_started, disable_done = (Event() for _ in range(4))
    pids = {}
    original_create = SessionService.create

    def paused_create(service, *args):
        pids["callback"] = service.uow.session.execute(text("SELECT pg_backend_pid()")).scalar_one()
        at_insert.set()
        assert resume.wait(10), "callback was not released"
        return original_create(service, *args)

    monkeypatch.setattr(SessionService, "create", paused_create)

    def callback():
        with s.transaction() as uow:
            cookie = SessionService(uow, settings).callback(browser, state, "synthetic-code")
            uow.commit()
            return cookie

    def disable():
        with s.transaction() as uow:
            pids["disable"] = uow.session.execute(text("SELECT pg_backend_pid()")).scalar_one()
            disable_started.set()
            try:
                return IdentityOperations(uow, settings).prepare_disable(
                    s.admin_actor(), "concurrent-disable", s.users["alice"], 1, UserWrite(enabled=False))
            finally:
                disable_done.set()

    blocked = False
    with ThreadPoolExecutor(max_workers=2) as pool:
        callback_future = pool.submit(callback)
        try:
            assert at_insert.wait(10), "callback did not reach session insertion"
            disable_future = pool.submit(disable)
            assert disable_started.wait(10)
            deadline = monotonic() + 5
            while not disable_done.is_set() and monotonic() < deadline:
                with s.app.connect() as connection:
                    blockers = connection.execute(text("SELECT pg_blocking_pids(:pid)"),
                                                  {"pid": pids["disable"]}).scalar_one()
                if pids["callback"] in blockers:
                    blocked = True
                    break
                sleep(0.01)
        finally:
            resume.set()
        cookie = callback_future.result(timeout=10)
        disable_future.result(timeout=10)
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            SessionService(uow, settings).read(cookie)
    with s.transaction() as uow:
        operations = IdentityOperations(uow, settings)
        intent = operations.prepare_disable(s.admin_actor(), "reenable", s.users["alice"], 2, UserWrite(enabled=True))
        assert operations.attempt(s.admin_actor(), intent.id).phase == "completed"
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            SessionService(uow, settings).read(cookie)
    assert blocked, "disable must wait until callback validation and insertion commit"
