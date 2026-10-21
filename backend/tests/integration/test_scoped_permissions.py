"""Real PostgreSQL authorization: complete grants and current rights."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from lzo.contracts import ResourceScope
from lzo.errors import Forbidden


def test_roles_do_not_cross_product(scope_scenario):
    s = scope_scenario
    s.grant("alice", "editor", scope="farm_a")
    s.grant("alice", "viewer", scope="farm_b")
    assert s.allowed("alice", "edit", "object_a")
    assert not s.allowed("alice", "edit", "object_b")
    assert s.allowed("alice", "view", "object_b")
    assert set(s.ids("alice", "edit")) == {s.ids_by_name["object_a"]}


def test_service_relationship_and_owner_metadata_never_grant(scope_scenario):
    s = scope_scenario
    s.grant("alice", "editor", scope="farm_b")
    assert not s.allowed("alice", "view", "shared_device")
    s.grant("alice", "viewer", resource="shared_device")
    assert s.allowed("alice", "view", "shared_device")
    assert not s.allowed("alice", "edit", "shared_device")
    assert not s.allowed("alice", "use", "shared_device")


def test_security_class_is_not_inferred_from_scope(scope_scenario):
    s = scope_scenario
    s.grant("alice", "editor", scope="farm_a")
    with s.transaction() as uow:
        from lzo.auth.permissions import require_permission
        with pytest.raises(Forbidden):
            require_permission(s.actor("alice"), "edit",
                ResourceScope(s.ids_by_name["object_a"], None, (), "schema"), uow=uow)


def test_revocation_rejects_captured_actor_and_new_actor_loses_access(scope_scenario):
    s = scope_scenario
    grant = s.grant("alice", "editor", scope="farm_a")
    captured = s.actor("alice")
    s.revoke(grant)
    with s.transaction() as uow:
        from lzo.auth.permissions import require_permission
        with pytest.raises(Forbidden) as failure:
            require_permission(captured, "view", s.resource("object_a"), uow=uow)
        assert failure.value.code == "AUTHORIZATION_CHANGED"
    assert not s.allowed("alice", "view", "object_a")


def test_identity_is_bound_to_issuer_and_subject_not_email(scope_scenario):
    from lzo.auth.session import resolve_identity
    with scope_scenario.transaction() as uow:
        a = resolve_identity(uow, "https://issuer-a", "sub-a", "Same", "same@example.invalid")
        again = resolve_identity(uow, "https://issuer-a", "sub-a", "Renamed", "new@example.invalid")
        b = resolve_identity(uow, "https://issuer-b", "sub-a", "Same", "same@example.invalid")
        c = resolve_identity(uow, "https://issuer-a", "sub-b", "Same", "same@example.invalid")
        assert a == again
        assert len({a, b, c}) == 3


def test_unauthorized_iterator_does_not_return_hidden_objects(scope_scenario):
    assert list(scope_scenario.ids("nobody", "view")) == []


def test_group_membership_and_role_changes_are_current_and_audited(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GroupWrite, RoleWrite, GrantWrite
    from lzo.governance.models import revisions
    s = scope_scenario
    with s.transaction() as uow:
        service = AuthorizationAdmin(uow)
        group = service.create_group(s.admin_actor(), GroupWrite(name="Operators"))
        uow.commit()
    with s.transaction() as uow:
        service = AuthorizationAdmin(uow)
        service.set_members(s.admin_actor(), group.id, 1, [s.users["alice"]])
        uow.commit()
    with s.transaction() as uow:
        service = AuthorizationAdmin(uow)
        grant = service.create_grant(s.admin_actor(), GrantWrite(
            group_id=group.id, role_id=s.roles["viewer"],
            scope_id=s.scopes["farm_a"], security_class="data"))
        uow.commit()
    assert s.allowed("alice", "view", "object_a")
    with s.transaction() as uow:
        service = AuthorizationAdmin(uow)
        service.set_members(s.admin_actor(), group.id, 2, [])
        uow.commit()
    assert not s.allowed("alice", "view", "object_a")
    with s.transaction() as uow:
        events = uow.session.execute(select(revisions.c.event_type).where(
            revisions.c.resource_id.in_([group.id, grant.id]))).scalars().all()
    assert events.count("group.members_changed") == 2
    assert "grant.created" in events
