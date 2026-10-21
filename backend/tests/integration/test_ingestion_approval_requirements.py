"""Conjunctive trusted scoped requirements preserve F06 actor rechecks."""
from uuid import UUID
import pytest
from lzo.governance.risk import ChangeDescriptor, ScopedActionRequirement, register_policy
from lzo.governance.approvals import ApprovalService, _snapshot
from lzo.ontology.drafts import head
from lzo.errors import ApiError


def test_scoped_phase_requirements_recheck_original_reviewer(evidence_case):
    s = evidence_case
    source = UUID(s.source().json()["id"])
    other_source = UUID(s.source().json()["id"])
    sources=tuple(sorted((source,other_source),key=str))
    s.ids_by_name["source_one"]=source;s.ids_by_name["source_two"]=other_source
    target = s.ids_by_name["object_a"]
    s.grant("admin", "editor", scope="farm_a")
    reviewer = s.grant("admin", "source_manager", resource="source_one")
    def resolver(uow, targets, command):
        return ChangeDescriptor(kind="test.i02.scoped", target_ids=(target, *sources), command={},
            baseline={str(i):head(uow,i)["revision"] for i in (target, *sources)}, impact={},
            submit_action="edit", review_action="edit", execute_action="edit", operation_key="i02-scoped",
            operation_target_ids=(target,), scoped_action_requirements=tuple(
                ScopedActionRequirement(phase, "source.manage", sources) for phase in ("execute", "review", "submit")))
    register_policy("test.i02.scoped", resolver)
    with s.transaction() as uow:
        service = ApprovalService(uow)
        change = service.preview(s.actor("alice"), "test.i02.scoped", [target], {})
        approval = service.submit(s.actor("alice"), change.kind, list(change.target_ids), change.change_digest,
            change.baseline, {"command":{}})
        uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError) as partial:
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Partial source authority")
    assert partial.value.status==403
    s.grant("admin","source_manager",resource="source_two")
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "Independent")
        uow.commit()
    s.revoke(reviewer)
    with s.transaction() as uow, pytest.raises(ApiError) as error:
        ApprovalService(uow).require_executable(s.actor("alice"), approval.id, change.change_digest, change.baseline)
    assert error.value.status == 403
    s.grant("admin","source_manager",resource="source_one")
    s.revoke(s.source_grant)
    with s.transaction() as uow,pytest.raises(ApiError) as submitter:
        ApprovalService(uow).require_executable(s.actor("alice"),approval.id,change.change_digest,change.baseline)
    assert submitter.value.status==403


def test_empty_scoped_requirements_preserve_existing_descriptor_bytes():
    from uuid import uuid4
    from lzo.governance.risk import digest_change
    target = uuid4()
    change = ChangeDescriptor(kind="legacy", target_ids=(target,), command={}, baseline={str(target):1}, impact={},
        submit_action="edit", review_action="edit", operation_key="legacy")
    assert "scoped_action_requirements" not in _snapshot(change)
    assert change.change_digest == digest_change({"kind":"legacy", "targets":[str(target)], "command":{}})


@pytest.mark.parametrize("case",["phase","outside","duplicate","unordered"])
def test_invalid_trusted_scoped_requirements_fail_closed(case):
    from uuid import uuid4
    from lzo.governance.risk import resolve_change
    target,other=uuid4(),uuid4()
    requirement={"phase":ScopedActionRequirement("unknown","edit",(target,)),
        "outside":ScopedActionRequirement("submit","edit",(other,)),
        "duplicate":ScopedActionRequirement("submit","edit",(target,target)),
        "unordered":ScopedActionRequirement("submit","edit",tuple(sorted((target,other),key=str,reverse=True)))}[case]
    def resolver(uow,targets,command):
        return ChangeDescriptor(kind="test.i02.invalid",target_ids=(target,other),command={},baseline={str(target):1},impact={},
            submit_action="edit",review_action="edit",operation_key="invalid",scoped_action_requirements=(requirement,))
    if case=="outside":
        requirement=ScopedActionRequirement("submit","edit",(uuid4(),))
    register_policy("test.i02.invalid",resolver)
    with pytest.raises(ApiError) as error:resolve_change(None,"test.i02.invalid",[],{})
    assert error.value.code=="APPROVAL_POLICY_INVALID"
