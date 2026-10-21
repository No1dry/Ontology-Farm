"""HTTP contracts over real isolated PostgreSQL and encrypted cookies."""
import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient


@pytest.fixture
def identity_client(scope_scenario, settings):
    from lzo.main import create_app
    from lzo.auth.session import SessionService, SESSION_COOKIE
    config = settings.model_copy(update={"session_encryption_key": Fernet.generate_key().decode(),
                                         "public_origin": "https://testserver", "invitation_smtp_configured": False})
    app = create_app(config)
    app.state.engine.dispose()
    app.state.engine = scope_scenario.app
    with TestClient(app, base_url="https://testserver") as client:
        def login(name):
            with scope_scenario.transaction() as uow:
                cookie = SessionService(uow, config).create(scope_scenario.users[name], {"synthetic": True})
                uow.commit()
            client.cookies.set(SESSION_COOKIE, cookie)
            return client.get("/api/v1/session").json()["csrf_token"]
        client.login_as = login
        yield client


def test_hidden_lists_history_and_csrf(identity_client, scope_scenario):
    client = identity_client
    csrf = client.login_as("nobody")
    for path in ("users", "groups", "roles", "grants"):
        assert client.get("/api/v1/" + path).status_code == 403
    assert client.get("/api/v1/history").json() == {"items": [], "next_cursor": None}
    hidden = scope_scenario.ids_by_name["object_a"]
    assert client.get(f"/api/v1/history/{hidden}/revisions/1").status_code == 404
    assert client.post("/api/v1/groups", json={"name": "denied"}).json()["code"] == "CSRF_INVALID"
    assert client.post("/api/v1/groups", json={"name": "denied"}, headers={"X-CSRF-Token": csrf}).status_code == 403


def test_admin_preconditions_idempotency_and_membership_revision(identity_client):
    client = identity_client
    csrf = client.login_as("admin")
    headers = {"X-CSRF-Token": csrf}
    assert client.post("/api/v1/groups", json={"name": "group"}, headers=headers).status_code == 428
    headers["Idempotency-Key"] = "create-group"
    created = client.post("/api/v1/groups", json={"name": "group"}, headers=headers)
    assert created.status_code == 201
    assert client.post("/api/v1/groups", json={"name": "group"}, headers=headers).json() == created.json()
    assert client.post("/api/v1/groups", json={"name": "different"}, headers=headers).status_code == 409
    group_id = created.json()["id"]
    headers["Idempotency-Key"] = "members"
    assert client.put(f"/api/v1/groups/{group_id}/members", json={"user_ids": []}, headers=headers).status_code == 428
    headers["If-Match"] = '"1"'
    changed = client.put(f"/api/v1/groups/{group_id}/members", json={"user_ids": []}, headers=headers)
    assert changed.status_code == 200 and changed.json()["revision"] == 2
    assert client.get(f"/api/v1/groups/{group_id}/members").json() == {"items": [], "next_cursor": None}
    headers["Idempotency-Key"] = "stale-members"
    assert client.put(f"/api/v1/groups/{group_id}/members", json={"user_ids": []}, headers=headers).status_code == 409


def test_role_change_invalidates_previous_actions(identity_client, scope_scenario):
    client, s = identity_client, scope_scenario
    csrf = client.login_as("admin")
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "role"}
    role = client.post("/api/v1/roles", json={"name": "temporary", "actions": ["view", "edit"]}, headers=headers).json()
    headers["Idempotency-Key"] = "grant"
    grant = client.post("/api/v1/grants", json={"user_id": str(s.users["alice"]), "role_id": role["id"],
        "scope_id": str(s.scopes["farm_a"]), "security_class": "data"}, headers=headers)
    assert grant.status_code == 201
    assert s.allowed("alice", "edit", "object_a")
    headers.update({"Idempotency-Key": "change-role", "If-Match": '"1"'})
    changed = client.patch("/api/v1/roles/" + role["id"], json={"name": "temporary", "actions": ["view"]}, headers=headers)
    assert changed.status_code == 200
    assert s.allowed("alice", "view", "object_a") and not s.allowed("alice", "edit", "object_a")


def test_invitation_without_smtp_never_reports_delivery(identity_client):
    client = identity_client
    csrf = client.login_as("admin")
    response = client.post("/api/v1/users/invitations",
        json={"email": "synthetic@example.invalid", "display_name": "Synthetic"},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "invite"})
    assert response.status_code == 503
    assert response.json()["code"] == "INVITATION_MAIL_UNAVAILABLE"


def test_superseded_disable_replay_returns_http_conflict(identity_client, scope_scenario, monkeypatch):
    from sqlalchemy import update
    from lzo.auth.identity_admin import KeycloakAdmin
    from lzo.auth.identity_operations import IdentityOperations
    from lzo.auth.models import users
    from lzo.auth.schemas import UserWrite
    client, s = identity_client, scope_scenario
    config = client.app.state.settings
    csrf = client.login_as("admin")
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == s.users["alice"]).values(issuer=config.oidc_issuer))
        uow.commit()
        IdentityOperations(uow, config).prepare_disable(
            s.admin_actor(), "superseded-http", s.users["alice"], 1, UserWrite(enabled=False))
    with s.transaction() as uow:
        uow.session.execute(update(users).where(users.c.id == s.users["alice"]).values(enabled=True, revision=3))
        uow.commit()

    def unexpected_provider_change(*args):
        raise AssertionError("Superseded operation must not reach the provider")

    monkeypatch.setattr(KeycloakAdmin, "set_enabled", unexpected_provider_change)
    response = client.patch(f"/api/v1/users/{s.users['alice']}", json={"enabled": False},
        headers={"X-CSRF-Token": csrf, "Idempotency-Key": "superseded-http", "If-Match": '"1"'})
    assert response.status_code == 409, response.json()
    assert response.json()["code"] == "REVISION_CONFLICT"
