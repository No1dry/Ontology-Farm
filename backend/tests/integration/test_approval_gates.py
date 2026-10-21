"""F06 gates against real PostgreSQL; schema fixtures are synthetic, not S02."""
import importlib.util


def test_approval_protocol_is_available():
    assert importlib.util.find_spec("lzo.governance.approvals") is not None, "F06 approval service is absent"


def test_trusted_operation_targets_preserve_all_reference_visibility(approval_scenario, monkeypatch):
    from dataclasses import replace
    from lzo.governance.risk import policies
    from lzo.governance.approvals import approvals
    s = approval_scenario
    legacy = s.submit()
    s.decide(legacy)
    assert s.execute(legacy).id == legacy.id
    with s.transaction() as uow:
        old = uow.session.execute(select(approvals.c.descriptor).where(approvals.c.id == legacy.id)).scalar_one()
        assert "operation_target_ids" not in old
    dependency = s.ids_by_name["object_b"]
    s.refs.append(dependency)
    source_view = s.grant("alice", "viewer", scope="farm_b")
    s.grant("admin", "viewer", scope="farm_b")
    with pytest.raises(ApiError, match="FORBIDDEN"):
        s.preview()  # Original default still requires edit on every reference.
    original = policies[s.kind]
    selected = (s.refs[0],)
    def scoped(uow, targets, command):
        return replace(original(uow, targets, command), operation_target_ids=selected)
    monkeypatch.setitem(policies, s.kind, scoped)
    ref = s.submit()
    s.decide(ref)
    assert s.execute(ref).reviewer_id == s.users["admin"]
    with s.transaction() as uow:
        snapshot = uow.session.execute(select(approvals.c.descriptor).where(approvals.c.id == ref.id)).scalar_one()
        assert snapshot["operation_target_ids"] == [str(s.refs[0])]
    selected = tuple(s.refs)
    with pytest.raises(ApiError, match="APPROVAL_STALE"):
        s.execute(ref)
    for invalid in ((), (dependency,), (s.refs[0], uuid4()), (s.refs[0], s.refs[0]), (str(s.refs[0]),)):
        selected = invalid
        with pytest.raises(ApiError, match="APPROVAL_POLICY_INVALID"):
            s.preview()
    selected = (s.refs[0],)
    s.revoke(source_view)
    with pytest.raises(ApiError, match="NOT_FOUND"):
        s.execute(ref)

import pytest
from sqlalchemy import insert, select, text, update
from uuid import uuid4
from lzo.errors import ApiError


@pytest.fixture
def approval_scenario(scope_scenario, monkeypatch):
    # Initialize trusted deployment registrations before temporary synthetic
    # policies. Restore every overwritten policy at fixture teardown.
    import lzo.main
    from lzo.governance.approvals import ApprovalService
    from lzo.governance.risk import ChangeDescriptor, policies, digest_change
    from lzo.governance.models import resource_heads
    from lzo.contracts import ResourceScope
    s = scope_scenario
    s.submitter_grant = s.grant("alice", "editor", scope="farm_a")
    s.reviewer_grant = s.grant("admin", "editor", scope="farm_a")
    s.kind = "schema.add_class"
    s.command = {"class_iri": "urn:synthetic:Class"}
    s.refs = [s.ids_by_name["object_a"]]
    s.conflicts = False
    s.semantic = True

    def resolve(uow, targets, command):
        # Test-only domain producer derives real DB baselines; no client echo.
        baseline = {str(ref): uow.session.execute(select(resource_heads.c.revision).where(
            resource_heads.c.resource_id == ref)).scalar_one() for ref in targets}
        return ChangeDescriptor(kind=s.kind, target_ids=tuple(targets), command=command,
            baseline=baseline, impact={"synthetic": True, "conflicts": s.conflicts},
            submit_action="edit", review_action="edit", semantic_change=s.semantic,
            changed_fields=tuple(command), unresolved_conflicts=s.conflicts,
            operation_key="synthetic:" + str(targets[0]))
    for kind in ("schema.add_class", "metadata.update", "risk.rules.update", "recalculation.execute"):
        monkeypatch.setitem(policies, kind, resolve)

    def preview():
        with s.transaction() as uow:
            return ApprovalService(uow).preview(s.actor("alice"), s.kind, s.refs, s.command)
    def submit():
        p = preview()
        with s.transaction() as uow:
            ref = ApprovalService(uow).submit(s.actor("alice"), s.kind, s.refs,
                p.change_digest, p.baseline, {"command": s.command})
            uow.commit()
            return ref
    def decide(ref, name="admin", decision="approve"):
        with s.transaction() as uow:
            result = ApprovalService(uow).decide(s.actor(name), ref.id, decision, "Synthetic review")
            uow.commit()
            return result
    def execute(ref, digest=None, baseline=None):
        with s.transaction() as uow:
            return ApprovalService(uow).require_executable(s.actor("alice"), ref.id,
                digest or ref.change_digest, baseline or ref.baseline)
    s.preview, s.submit, s.decide, s.execute = preview, submit, decide, execute
    return s


def test_schema_addition_requires_independent_reviewer(approval_scenario):
    s = approval_scenario
    ref = s.submit()
    assert ref.risk == "high"
    with pytest.raises(ApiError, match="SEPARATION_OF_DUTIES"):
        s.decide(ref, "alice")
    with pytest.raises(ApiError):
        s.decide(ref, "nobody")
    s.decide(ref)
    assert s.execute(ref).reviewer_id == s.users["admin"]


def test_real_baseline_and_digest_are_checked_not_echoed(approval_scenario):
    from lzo.governance.revisions import append_revision
    s = approval_scenario
    ref = s.submit()
    s.decide(ref)
    with pytest.raises(ApiError, match="APPROVAL_STALE"):
        s.execute(ref, digest="0" * 64)
    with s.transaction() as uow:
        append_revision(uow, s.actor("alice"), s.refs[0], 1, {"changed": True}, "synthetic.changed")
        uow.commit()
    with pytest.raises(ApiError, match="APPROVAL_STALE"):
        s.execute(ref)  # passes original baseline, DB is newer


def test_low_risk_is_only_nonsemantic_metadata(approval_scenario):
    s = approval_scenario
    s.kind, s.command, s.semantic = "metadata.update", {"label": "Synthetic"}, False
    ref = s.submit()
    assert ref.risk == "low"
    assert s.execute(ref).reviewer_id is None
    s.semantic = True
    assert s.submit().risk == "high"
    s.kind, s.semantic = "risk.rules.update", False
    assert s.submit().risk == "high"


def test_reviewer_revocation_invalidates_approved_execution(approval_scenario):
    s = approval_scenario
    ref = s.submit()
    s.decide(ref)
    # Unrelated authorization version advancement leaves relevant rights intact.
    s.grant("nobody", "viewer", scope="farm_b")
    assert s.execute(ref).id == ref.id
    s.revoke(s.reviewer_grant)
    with pytest.raises(ApiError):
        s.execute(ref)


def test_submitter_revocation_and_conflicts_block_execution(approval_scenario):
    s = approval_scenario
    s.kind = "recalculation.execute"
    ref = s.submit()
    s.decide(ref)
    s.conflicts = True
    with pytest.raises(ApiError, match="CONFLICT_RULING_REQUIRED"):
        s.execute(ref)
    s.conflicts = False
    s.revoke(s.submitter_grant)
    with pytest.raises(ApiError):
        s.execute(ref)


def test_rejection_supersession_and_immutable_storage(approval_scenario):
    s = approval_scenario
    ref = s.submit()
    s.decide(ref, decision="reject")
    with pytest.raises(ApiError, match="APPROVAL_REQUIRED"):
        s.execute(ref)
    newer = s.submit()
    s.decide(newer)
    with pytest.raises(ApiError, match="APPROVAL_STALE"):
        s.execute(ref)
    with s.transaction() as uow:
        from sqlalchemy.exc import DBAPIError
        with pytest.raises(DBAPIError):
            uow.session.execute(text("UPDATE approvals SET change_digest='tampered' WHERE id=:id"), {"id": newer.id})
    with pytest.raises(ApiError, match="APPROVAL_DECIDED"):
        s.decide(newer, decision="reject")


def test_all_target_visibility_filters_approval_and_history(approval_scenario):
    from lzo.governance.approvals import ApprovalService
    from lzo.governance.history import HistoryService, HistoryQuery
    s = approval_scenario
    s.refs.append(s.ids_by_name["object_b"])
    extra = s.grant("alice", "editor", scope="farm_b")
    s.grant("admin", "editor", scope="farm_b")
    ref = s.submit()
    s.decide(ref)
    s.revoke(extra)
    with s.transaction() as uow:
        assert ApprovalService(uow).list(s.actor("alice"), None).items == []
        with pytest.raises(ApiError, match="NOT_FOUND"):
            ApprovalService(uow).get(s.actor("alice"), ref.id)
        assert HistoryService(uow).list(s.actor("alice"), HistoryQuery(resource_id=ref.id), None).items == []


def test_authorization_privilege_expansion_cannot_bypass_service(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GrantWrite
    s = scope_scenario
    s.grant("alice", "viewer", scope="farm_a")
    with s.transaction() as uow:
        with pytest.raises(ApiError, match="APPROVAL_REQUIRED"):
            AuthorizationAdmin(uow).create_grant(s.admin_actor(), GrantWrite(
                user_id=s.users["alice"], role_id=s.roles["editor"],
                security_class="data", scope_id=s.scopes["farm_a"]))


def test_revoke_then_regrant_cannot_reset_initial_assignment(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GrantWrite
    s = scope_scenario
    original = s.grant("alice", "viewer", scope="farm_a")
    s.revoke(original)
    with s.transaction() as uow:
        with pytest.raises(ApiError, match="APPROVAL_REQUIRED"):
            AuthorizationAdmin(uow).create_grant(s.admin_actor(), GrantWrite(
                user_id=s.users["alice"], role_id=s.roles["editor"],
                security_class="data", scope_id=s.scopes["farm_b"]))


def test_approval_http_routes_are_registered(api_client):
    paths = api_client.get('/openapi.json').json()['paths']
    assert '/api/v1/approvals' in paths
    assert '/api/v1/approvals/{approval_id}/decisions' in paths


def install_identity_reviewer(s):
    from lzo.auth import models
    from lzo.governance.revisions import append_revision
    with s.transaction() as uow:
        grant_id = uuid4()
        uow.session.execute(select(models.authorization_state).with_for_update()).one()
        uow.session.execute(insert(models.grants).values(id=grant_id, user_id=s.users['nobody'],
            role_id=s.roles['administrator'], security_class='identity', global_scope=True, revision=1))
        uow.session.execute(insert(models.resource_scopes).values(resource_id=grant_id,
            resource_kind='grant', security_class='identity'))
        append_revision(uow, s.admin_actor(), grant_id, 0, {'installation': 'synthetic reviewer'}, 'grant.fixture_installed')
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version + 1))
        uow.commit()


def approve_identity(s, kind, targets, command):
    from lzo.governance.approvals import ApprovalService
    with s.transaction() as uow:
        svc = ApprovalService(uow)
        p = svc.preview(s.admin_actor(), kind, targets, command)
        ref = svc.submit(s.admin_actor(), kind, targets, p.change_digest, p.baseline, {'command': command})
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('nobody'), ref.id, 'approve', 'Independent synthetic admin')
        uow.commit()
    return ref


def test_exact_approved_grant_executes_and_changed_command_is_rejected(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GrantWrite
    s = scope_scenario
    install_identity_reviewer(s)
    s.grant('alice', 'viewer', scope='farm_a')
    body = GrantWrite(user_id=s.users['alice'], role_id=s.roles['editor'],
        scope_id=s.scopes['farm_a'], security_class='data')
    ref = approve_identity(s, 'identity.grant.create', [body.role_id], body.model_dump(mode='json', exclude={'approval_id'}))
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_STALE'):
            AuthorizationAdmin(uow).create_grant(s.admin_actor(), body.model_copy(update={
                'approval_id': ref.id, 'scope_id': s.scopes['farm_b']}))
    with s.transaction() as uow:
        AuthorizationAdmin(uow).create_grant(s.admin_actor(), body.model_copy(update={'approval_id': ref.id}))
        uow.commit()
    assert s.allowed('alice', 'edit', 'object_a')


def test_role_and_group_expansion_use_same_gate(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GroupWrite, GrantWrite, RoleWrite
    s = scope_scenario
    install_identity_reviewer(s)
    s.grant('alice', 'viewer', scope='farm_a')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_REQUIRED'):
            AuthorizationAdmin(uow).update_role(s.admin_actor(), s.roles['viewer'], 1,
                RoleWrite(name='viewer expanded', actions=['view', 'edit']))
    with s.transaction() as uow:
        group = AuthorizationAdmin(uow).create_group(s.admin_actor(), GroupWrite(name='Second scope'))
        uow.commit()
    with s.transaction() as uow:
        AuthorizationAdmin(uow).create_grant(s.admin_actor(), GrantWrite(group_id=group.id,
            role_id=s.roles['viewer'], scope_id=s.scopes['farm_b'], security_class='data'))
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_REQUIRED'):
            AuthorizationAdmin(uow).set_members(s.admin_actor(), group.id, 1, [s.users['alice']])
    ref = approve_identity(s, 'identity.group.members', [group.id],
        {'group_id': str(group.id), 'expected_revision': 1, 'user_ids': [str(s.users['alice'])]})
    with s.transaction() as uow:
        AuthorizationAdmin(uow).set_members(s.admin_actor(), group.id, 1, [s.users['alice']], approval_id=ref.id)
        uow.commit()
    assert s.allowed('alice', 'view', 'object_b')
    with s.transaction() as uow:
        AuthorizationAdmin(uow).set_members(s.admin_actor(), group.id, 2, [])
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_REQUIRED'):
            AuthorizationAdmin(uow).set_members(s.admin_actor(), group.id, 3, [s.users['alice']])


def test_unknown_policy_and_extra_baseline_fail_closed(approval_scenario):
    from lzo.governance.approvals import ApprovalService
    s = approval_scenario
    p = s.preview()
    with s.transaction() as uow:
        svc = ApprovalService(uow)
        with pytest.raises(ApiError, match='APPROVAL_POLICY_UNKNOWN'):
            svc.preview(s.actor('alice'), 'unknown.kind', s.refs, {})
        with pytest.raises(ApiError, match='APPROVAL_STALE'):
            svc.submit(s.actor('alice'), s.kind, s.refs, p.change_digest,
                {**p.baseline, 'unknown': 1}, {'command': s.command})

@pytest.fixture
def approval_client(approval_scenario, settings):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from lzo.auth.session import SessionService, SESSION_COOKIE
    from lzo.main import create_app
    s = approval_scenario
    config = settings.model_copy(update={'session_encryption_key': Fernet.generate_key().decode(),
        'public_origin': 'https://testserver', 'list_page_size': 1})
    app = create_app(config)
    app.state.engine.dispose()
    app.state.engine = s.app
    with TestClient(app, base_url='https://testserver') as client:
        def login(name):
            with s.transaction() as uow:
                cookie = SessionService(uow, config).create(s.users[name], {'synthetic': True})
                uow.commit()
            client.cookies.set(SESSION_COOKIE, cookie)
            return client.get('/api/v1/session').json()['csrf_token']
        client.login_as = login
        yield client


def test_http_current_rights_preconditions_and_idempotency(approval_scenario, approval_client):
    s, client = approval_scenario, approval_client
    csrf = client.login_as('alice')
    p = s.preview()
    body = {'kind': s.kind, 'target_ids': [str(x) for x in s.refs], 'command': s.command,
            'change_digest': p.change_digest, 'baseline': p.baseline}
    assert client.post('/api/v1/approvals', json=body).status_code == 403
    headers = {'X-CSRF-Token': csrf}
    assert client.post('/api/v1/approvals', json=body, headers=headers).status_code == 428
    headers['Idempotency-Key'] = 'submit'
    response = client.post('/api/v1/approvals', json=body, headers=headers)
    assert response.status_code == 201
    ref = response.json()
    assert client.post('/api/v1/approvals', json=body, headers=headers).json() == ref
    url = '/api/v1/approvals/' + ref['id'] + '/decisions'
    headers = {'X-CSRF-Token': client.login_as('admin'), 'Idempotency-Key': 'review'}
    data = {'decision': 'approve', 'comment': 'Synthetic independent review'}
    assert client.post(url, json=data, headers=headers).status_code == 428
    headers['If-Match'] = '"1"'
    reviewed = client.post(url, json=data, headers=headers)
    assert reviewed.status_code == 200
    assert client.post(url, json=data, headers=headers).json() == reviewed.json()
    headers['Idempotency-Key'] = 'other-review'
    assert client.post(url, json=data, headers=headers).status_code == 409
    s.revoke(s.reviewer_grant)
    headers['Idempotency-Key'] = 'review'
    assert client.post(url, json=data, headers=headers).status_code == 404


@pytest.mark.parametrize("target_case,expected_status,expected_code", [
    ("unknown", 404, "NOT_FOUND"),
    ("hidden", 404, "NOT_FOUND"),
    ("visible_without_submit", 403, "FORBIDDEN"),
    ("visible_without_submit_and_hidden", 404, "NOT_FOUND"),
])
def test_http_preview_target_visibility_contract(
        approval_scenario, approval_client, target_case, expected_status, expected_code):
    from lzo.auth.schemas import GrantWrite
    s, client = approval_scenario, approval_client
    if target_case in {"unknown", "hidden"}:
        target = uuid4() if target_case == "unknown" else s.ids_by_name["object_b"]
        command = GrantWrite(user_id=s.users["nobody"], role_id=s.roles["viewer"],
            resource_id=target, security_class="data").model_dump(mode="json", exclude={"approval_id"})
        body = {"kind": "identity.grant.create", "target_ids": [str(s.roles["viewer"])],
                "command": command}
        name = "admin"  # Has identity.manage, but no view of object_b.
    else:
        s.grant("nobody", "viewer", scope="farm_a")
        targets = [s.ids_by_name["object_a"]]
        if target_case == "visible_without_submit_and_hidden":
            targets.append(s.ids_by_name["object_b"])
        body = {"kind": s.kind, "target_ids": [str(x) for x in targets], "command": s.command}
        name = "nobody"
    response = client.post("/api/v1/approvals/preview", json=body,
        headers={"X-CSRF-Token": client.login_as(name)})
    assert response.status_code == expected_status
    assert response.json()["code"] == expected_code


def test_preview_retains_stale_actor_error_before_visibility_checks(approval_scenario):
    from lzo.governance.approvals import ApprovalService
    s = approval_scenario
    captured = s.actor("alice")
    s.revoke(s.submitter_grant)
    with s.transaction() as uow:
        with pytest.raises(ApiError) as error:
            ApprovalService(uow).preview(captured, s.kind, s.refs, s.command)
        assert error.value.status == 403
        assert error.value.code == "AUTHORIZATION_CHANGED"


def test_publish_authority_and_review_authority_are_distinct(scope_scenario):
    from lzo.auth import models
    from lzo.governance.approvals import ApprovalService
    from lzo.governance.risk import ChangeDescriptor, register_policy
    from lzo.governance.models import resource_heads
    KIND = "schema.add_class"
    def resolve(uow, targets, command):
        return ChangeDescriptor(kind=KIND, target_ids=tuple(targets), command=command,
            baseline={str(x): uow.session.execute(select(resource_heads.c.revision).where(
                resource_heads.c.resource_id == x)).scalar_one() for x in targets},
            impact={"synthetic": True}, submit_action="schema.edit", review_action="schema.review",
            execute_action="schema.publish", operation_key="publish:" + str(targets[0]))
    s = scope_scenario
    # The test-only synthetic policy uses three explicit actions on this fixture.
    register_policy(KIND, resolve)
    reviewer_role = uuid4()
    with s.transaction() as uow:
        uow.session.execute(update(models.roles).where(models.roles.c.id == s.roles['editor']).values(
            actions=['view', 'schema.edit', 'schema.publish']))
        uow.session.execute(insert(models.roles).values(id=reviewer_role, name='Review only',
            actions=['view', 'schema.review'], revision=1))
        uow.session.execute(insert(models.resource_scopes).values(resource_id=reviewer_role,
            security_class='identity', resource_kind='role'))
        uow.commit()
    s.roles['reviewer'] = reviewer_role
    s.grant('alice', 'editor', scope='farm_a')
    s.grant('admin', 'reviewer', scope='farm_a')
    target = s.ids_by_name['object_a']
    command = {'resource_id': str(target), 'class_iri': 'urn:synthetic:f06:Class'}
    with s.transaction() as uow:
        svc = ApprovalService(uow)
        p = svc.preview(s.actor('alice'), KIND, [target], command)
        ref = svc.submit(s.actor('alice'), KIND, [target], p.change_digest, p.baseline, {'command': command})
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'), ref.id, 'approve', 'Reviewer has no edit or publish')
        uow.commit()
    with s.transaction() as uow:
        assert ApprovalService(uow).require_executable(s.actor('alice'), ref.id, ref.change_digest, ref.baseline).id == ref.id
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import RoleWrite
    with s.transaction() as uow:
        AuthorizationAdmin(uow).update_role(s.admin_actor(), s.roles['editor'], 1,
            RoleWrite(name='Publisher revoked', actions=['view', 'schema.edit']))
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='FORBIDDEN'):
            ApprovalService(uow).require_executable(s.actor('alice'), ref.id, ref.change_digest, ref.baseline)


def test_scope_and_object_uuid_are_distinct_authorization_targets(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GrantWrite
    s = scope_scenario
    s.grant('alice', 'viewer', resource='object_a')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_REQUIRED'):
            AuthorizationAdmin(uow).create_grant(s.admin_actor(), GrantWrite(user_id=s.users['alice'],
                role_id=s.roles['viewer'], scope_id=s.ids_by_name['object_a'], security_class='data'))


def test_malformed_policy_command_is_typed_validation_error(scope_scenario):
    from lzo.governance.approvals import ApprovalService
    s = scope_scenario
    with s.transaction() as uow:
        with pytest.raises(ApiError) as error:
            ApprovalService(uow).preview(s.admin_actor(), 'identity.role.update', [s.roles['viewer']],
                {'role_id': 'not-a-uuid', 'expected_revision': 1, 'body': {}})
        assert error.value.status == 422


def test_authorization_audit_retains_approval_reference(scope_scenario):
    from lzo.auth.identity_admin import AuthorizationAdmin
    from lzo.auth.schemas import GrantWrite
    from lzo.governance.models import revisions
    s = scope_scenario
    install_identity_reviewer(s)
    s.grant('alice', 'viewer', scope='farm_a')
    body = GrantWrite(user_id=s.users['alice'], role_id=s.roles['editor'],
        scope_id=s.scopes['farm_a'], security_class='data')
    ref = approve_identity(s, 'identity.grant.create', [body.role_id], body.model_dump(mode='json', exclude={'approval_id'}))
    with s.transaction() as uow:
        grant = AuthorizationAdmin(uow).create_grant(s.admin_actor(), body.model_copy(update={'approval_id': ref.id}))
        payload = uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id == grant.id)).scalar_one()
        assert payload['approval_id'] == str(ref.id)


def test_upgrade_marks_removed_group_members_noninitial_once(settings):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import create_engine
    schema = 'f06_upgrade_' + uuid4().hex
    owner = create_engine(settings.migration_database_url)
    root = Path(__file__).resolve().parents[3]
    cfg = Config(str(root / 'database/alembic.ini'))
    cfg.attributes.update(schema=schema, connection_url=settings.migration_database_url)
    former, never_granted, new_user, group, role = [uuid4() for _ in range(5)]
    try:
        with owner.begin() as conn:
            conn.execute(text(f'CREATE SCHEMA "{schema}" AUTHORIZATION lzo_owner'))
            conn.execute(text(f'GRANT USAGE ON SCHEMA "{schema}" TO lzo_app'))
        command.upgrade(cfg, '0004_jobs')
        with owner.begin() as conn:
            conn.execute(text(f'SET search_path TO "{schema}"'))
            conn.execute(text("INSERT INTO platform_users(id,issuer,subject,display_name) VALUES (:id,'synthetic','former','former'),(:other,'synthetic','unassigned','unassigned')"), {'id': former, 'other': never_granted})
            conn.execute(text("INSERT INTO platform_groups VALUES (:id,'Former group',1)"), {'id': group})
            conn.execute(text("INSERT INTO platform_roles VALUES (:id,'Former role','[\"view\"]',1)"), {'id': role})
            conn.execute(text("INSERT INTO permission_grants(id,group_id,role_id,scope_id,security_class,revision) VALUES (:id,:group,:role,:scope,'data',1)"), {'id': uuid4(), 'group': group, 'role': role, 'scope': uuid4()})
            conn.execute(text('INSERT INTO group_members VALUES (:group,:user)'), {'group': group, 'user': former})
            conn.execute(text('DELETE FROM group_members WHERE user_id=:user'), {'user': former})
        command.upgrade(cfg, 'head')
        command.upgrade(cfg, 'head')
        with owner.begin() as conn:
            conn.execute(text(f'SET search_path TO "{schema}"'))
            for person in (former, never_granted):
                assert conn.execute(text('SELECT count(*) FROM authorization_footprints WHERE user_id=:id'), {'id': person}).scalar_one() == 1
            conn.execute(text("INSERT INTO platform_users(id,issuer,subject,display_name) VALUES (:id,'synthetic','new','new')"), {'id': new_user})
            assert conn.execute(text('SELECT count(*) FROM authorization_footprints WHERE user_id=:id'), {'id': new_user}).scalar_one() == 0
    finally:
        with owner.begin() as conn:
            conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        owner.dispose()
