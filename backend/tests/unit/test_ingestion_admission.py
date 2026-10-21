"""Only a complete trusted ordinary-import proof may lower F06's default."""
from uuid import uuid4
from dataclasses import replace
from lzo.governance import risk
from lzo.governance.approvals import _snapshot


def test_ordinary_import_requires_exact_versioned_proof_and_kind():
    assert hasattr(risk,"OrdinaryImportEvidence"), "No typed ordinary import proof contract"
    identifier=uuid4()
    change=risk.ChangeDescriptor(kind="ingestion.import.execute",target_ids=(identifier,),command={"preview_id":str(identifier)},
        baseline={str(identifier):1},impact={},submit_action="source.manage",review_action="conflict.review",operation_key="import:one")
    assert risk.classify(change)[0]=="high"
    proof=risk.OrdinaryImportEvidence(evaluator_version="ordinary-import-v1",policy_digest="a"*64,manifest_digest="b"*64,
        registered_current_inputs=True,deterministic_effects=True,explicit_policy_coverage=True,bounded_impact=True,current_rights=True,
        effect_classes=("create","facts"))
    ordinary=replace(change,ordinary_import=proof)
    assert risk.classify(ordinary)[0]=="low"
    for name in ("registered_current_inputs","deterministic_effects","explicit_policy_coverage","bounded_impact","current_rights"):
        assert risk.classify(replace(change,ordinary_import=replace(proof,**{name:False})))[0]=="high"
    assert risk.classify(replace(ordinary,kind="ingestion.rules.publish"))[0]=="high"
    assert risk.classify(replace(ordinary,permission_expansion=True))[0]=="high"
    assert risk.classify(replace(ordinary,ordinary_import=replace(proof,evaluator_version="unknown")))[0]=="high"
    legacy=replace(change,kind="ingestion.ruling")
    assert _snapshot(legacy)["system_policy_version"]==1
    assert "ordinary_import" not in _snapshot(legacy)
    assert _snapshot(ordinary)["system_policy_version"]=="ordinary-import-v1"
def test_admission_policy_only_belongs_to_ruling_versions():
    from uuid import uuid4
    import pytest
    from pydantic import ValidationError
    from lzo.ingestion.schemas import RuleVersion,ImportAdmissionPolicy
    from lzo.contracts import ResourceScope
    policy=ImportAdmissionPolicy(effect_classes=("create",),predicate_iris=(),class_iris=("urn:Pond",),
        owner_scope_ids=(uuid4(),),max_resources=1,max_withdrawals=0,max_rows=1)
    with pytest.raises(ValidationError):
        RuleVersion(kind="authority",scope=ResourceScope(None,None,(),"schema"),parent_id=None,rules=(),mapping_profile=None,
            alignment_rules=(),authority_rules=(),ruling_policies=(),import_admission=policy)
