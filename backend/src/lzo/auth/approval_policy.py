"""F03 trusted command policy: compare complete effective grants, retain history."""
from uuid import UUID, uuid4

from sqlalchemy import Column, DateTime, String, Table, func, insert, select
from sqlalchemy.dialects.postgresql import UUID as PGUUID

from lzo.auth import models
from lzo.auth.permissions import require_permission
from lzo.contracts import ResourceScope
from lzo.errors import ApiError, Conflict
from lzo.governance.approvals import ApprovalService
from lzo.governance.risk import ChangeDescriptor, classify, digest_change, register_policy, resolve_change
from lzo.governance.revisions import lock_ledger

footprints = Table("authorization_footprints", models.metadata,
    Column("id", PGUUID(as_uuid=True), primary_key=True),
    Column("user_id", PGUUID(as_uuid=True)), Column("command_digest", String),
    Column("recorded_at", DateTime(timezone=True)))


def record_footprints(uow, user_ids, command):
    if user_ids:
        uow.session.execute(insert(footprints), [
            {"id": uuid4(), "user_id": x, "command_digest": digest_change(command),
             "recorded_at": uow.session.execute(select(func.clock_timestamp())).scalar_one()}
            for x in sorted(set(user_ids))])


def _rights(grants, roles, members):
    rights = set()
    for grant in grants:
        if not grant["active"]:
            continue
        people = {grant["user_id"]} if grant["user_id"] else members.get(grant["group_id"], set())
        target = ("*" if grant["global_scope"] else
                  "scope:" + str(grant["scope_id"]) if grant["scope_id"] else
                  "resource:" + str(grant["resource_id"]))
        for person in people:
            for action in roles[grant["role_id"]]["actions"]:
                rights.add((person, grant["security_class"], target, action))
    return rights


def _resolve(kind, uow, target_ids, command):
    from lzo.auth.schemas import GrantWrite, RoleWrite, MembersWrite
    all_grants = [dict(x) for x in uow.session.execute(select(models.grants)).mappings()]
    all_roles = {x["id"]: dict(x) for x in uow.session.execute(select(models.roles)).mappings()}
    all_groups = {x["id"]: dict(x) for x in uow.session.execute(select(models.groups)).mappings()}
    members = {}
    for row in uow.session.execute(select(models.group_members)).mappings():
        members.setdefault(row["group_id"], set()).add(row["user_id"])
    before = _rights(all_grants, all_roles, members)
    next_grants, next_roles = list(all_grants), {key: dict(value) for key, value in all_roles.items()}
    next_members = {key: set(value) for key, value in members.items()}
    refs, affected, relevant_roles, relevant_groups = set(), set(), set(), set()
    baseline = {}
    if kind == "identity.grant.create":
        body = GrantWrite.model_validate(command)
        if body.approval_id:
            raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Approval reference is not command content")
        normalized = body.model_dump(mode="json", exclude={"approval_id"})
        grant = body.model_dump(exclude={"approval_id"})
        grant.update(active=True, id=uuid4(), revision=1)
        if grant["role_id"] not in all_roles:
            raise ApiError("NOT_FOUND", 404, "Role not found")
        relevant_roles.add(grant["role_id"])
        if grant["group_id"]:
            relevant_groups.add(grant["group_id"])
            affected.update(members.get(grant["group_id"], set()))
        else:
            affected.add(grant["user_id"])
        if grant["resource_id"]:
            refs.add(grant["resource_id"])
        next_grants.append(grant)
        operation = {key: normalized[key] for key in (
            "user_id", "group_id", "role_id", "scope_id", "resource_id", "security_class", "global_scope")}
    elif kind == "identity.role.update":
        if set(command) != {"role_id", "expected_revision", "body"}:
            raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid role command")
        role_id = UUID(command["role_id"])
        body = RoleWrite.model_validate(command["body"])
        if body.approval_id or role_id not in all_roles:
            raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid role command")
        if all_roles[role_id]["revision"] != command["expected_revision"]:
            raise Conflict("APPROVAL_STALE", "Role revision changed")
        relevant_roles.add(role_id)
        next_roles[role_id]["actions"] = body.actions
        for grant in all_grants:
            if grant["role_id"] == role_id:
                if grant["group_id"]:
                    relevant_groups.add(grant["group_id"])
                    affected.update(members.get(grant["group_id"], set()))
                elif grant["user_id"]:
                    affected.add(grant["user_id"])
        normalized = {**command, "body": body.model_dump(mode="json", exclude={"approval_id"})}
        operation = {"role_id": str(role_id)}
    elif kind == "identity.group.members":
        if set(command) != {"group_id", "expected_revision", "user_ids"}:
            raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Invalid membership command")
        group_id = UUID(command["group_id"])
        body = MembersWrite(user_ids=command["user_ids"])
        if group_id not in all_groups or all_groups[group_id]["revision"] != command["expected_revision"]:
            raise Conflict("APPROVAL_STALE", "Group revision changed")
        relevant_groups.add(group_id)
        affected.update(members.get(group_id, set()) | set(body.user_ids))
        next_members[group_id] = set(body.user_ids)
        normalized = {**command, "user_ids": sorted(str(x) for x in set(body.user_ids))}
        operation = {"group_id": str(group_id)}
    else:
        raise ApiError("APPROVAL_POLICY_UNKNOWN", 422, "Unknown identity command")

    # Complete current/prior grant bindings for affected people, including groups.
    for group_id, people in members.items():
        if people & affected:
            relevant_groups.add(group_id)
    for grant in all_grants:
        if grant["user_id"] in affected or grant["group_id"] in relevant_groups:
            refs.add(grant["id"])
            if grant["resource_id"]:
                refs.add(grant["resource_id"])
            relevant_roles.add(grant["role_id"])
            baseline["grant:" + str(grant["id"])] = grant["revision"]
    refs.update(affected | relevant_roles | relevant_groups)
    for role_id in relevant_roles:
        baseline["role:" + str(role_id)] = all_roles[role_id]["revision"]
    for group_id in relevant_groups:
        if group_id not in all_groups:
            raise ApiError("NOT_FOUND", 404, "Group not found")
        baseline["group:" + str(group_id)] = all_groups[group_id]["revision"]
    known = {}
    for person in affected:
        row = uow.session.execute(select(models.users).where(models.users.c.id == person)).mappings().first()
        if row is None:
            raise ApiError("NOT_FOUND", 404, "User not found")
        count = uow.session.execute(select(func.count()).select_from(footprints).where(
            footprints.c.user_id == person)).scalar_one()
        # Retained revoked grants also cover explicit installation/bootstrap data.
        prior = any(g["user_id"] == person or person in members.get(g["group_id"], set())
                    for g in all_grants)
        known[person] = count > 0 or prior
        baseline["user:" + str(person)] = row["revision"]
        baseline["footprint:" + str(person)] = count
    after = _rights(next_grants, next_roles, next_members)
    added = after - before
    risky = False
    for person, security, target, action in added:
        existing = {r for r in before if r[0] == person}
        resulting_scopes = {(r[1], r[2]) for r in after if r[0] == person}
        # Historical presence prevents revoke/regrant from resetting initiality.
        if (action not in {"view", "edit", "use"} or target == "*"
                or len(resulting_scopes) > 1 or existing or known[person]):
            risky = True
    # Empty groups also cannot warehouse sensitive grants for a future bypass.
    if kind == "identity.grant.create" and not affected:
        risky = grant["global_scope"] or bool(set(all_roles[grant["role_id"]]["actions"]) - {"view", "edit", "use"})
    if not refs or not set(target_ids) <= refs:
        raise ApiError("INVALID_APPROVAL_DESCRIPTOR", 422, "Targets differ from command references")
    return ChangeDescriptor(kind=kind, target_ids=tuple(sorted(refs)), command=normalized,
        baseline=baseline, impact={"affected_user_ids": sorted(str(x) for x in affected),
                                  "governed_user_ids": sorted(str(x) for x in affected
                                      if any(r[0] == x for r in before | after)),
                                  "added_rights": len(added), "removed_rights": len(before - after)},
        submit_action="identity.manage", review_action="identity.manage",
        global_security_class="identity", operation_key=digest_change({"kind": kind, **operation}),
        permission_expansion=risky, semantic_change=False)


for _kind in ("identity.grant.create", "identity.role.update", "identity.group.members"):
    register_policy(_kind, lambda uow, targets, command, kind=_kind: _resolve(kind, uow, targets, command))


def gate(uow, actor, kind, targets, command, approval_id):
    # Caller holds the exclusive authorization lock; pure revocation remains
    # available to identity administrators without unrelated business-view rights.
    require_permission(actor, "identity.manage", ResourceScope(None, None, (), "identity"), uow=uow)
    lock_ledger(uow)
    service = ApprovalService(uow)
    change = resolve_change(uow, kind, targets, command)
    if classify(change)[0] == "high":
        if approval_id is None:
            raise Conflict("APPROVAL_REQUIRED", "Independent approval required for effective privilege expansion")
        service.require_executable(actor, approval_id, change.change_digest, change.baseline)
    elif approval_id is not None:
        service.require_executable(actor, approval_id, change.change_digest, change.baseline)
    return change
