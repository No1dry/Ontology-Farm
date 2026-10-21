"""Approved S03 composite merge includes inbound objects and immutable lineage."""
from uuid import UUID,uuid4
import pytest
from sqlalchemy import insert,select
from lzo.ingestion.identity import IdentityService
from lzo.governance.approvals import ApprovalService
from lzo.errors import ApiError


def test_three_entity_merge_classifies_all_prospective_values_without_order_winner(object_case):
    from lzo.auth import models
    from lzo.semantic import facts
    s=object_case
    with s.transaction() as uow:
        s.roles["merge_operator"]=uuid4()
        uow.session.execute(insert(models.roles).values(id=s.roles["merge_operator"],name="merge-operator",
            actions=["view","identity.merge","identity.review","edit"],revision=1))
        uow.commit()
    s.grant("alice","merge_operator",scope="farm_a")
    s.grant("admin","merge_operator",scope="farm_a")
    keep,one,two=uuid4(),uuid4(),uuid4()
    assert s.create_object(resource_id=keep).status_code==201
    first=s.assertion(one,value="first disputed")
    second=s.assertion(two,value="second disputed")
    first["valid_to"]=second["valid_to"]="2030-01-01T00:00:00Z"
    later=s.assertion(one,value="later undisputed")
    later["valid_from"]="2030-01-01T00:00:00Z"
    assert s.create_object(resource_id=one,assertions=[first,later]).status_code==201
    assert s.create_object(resource_id=two,assertions=[second]).status_code==201
    expected={UUID(first["assertion_id"]),UUID(second["assertion_id"])}
    for order in ((one,two),(two,one)):
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            proposal=IdentityService(uow).preview_merge(s.actor("alice"),keep,order)
            assert set(proposal.preserved_candidate_assertion_ids)==expected
            assert proposal.blocking_reasons==()
            uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),proposal.approval_id,"approve","Keep both disputed values pending")
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
        moved=facts.current_assertions(uow,keep)
        candidates=[a for a in moved if a["payload"]["state"]=="candidate"]
        assert len(moved)==3 and len(candidates)==2
        assert {a["payload"]["object"]["value"] for a in candidates}=={"first disputed","second disputed"}
        future=[a for a in facts.current_assertions(uow,keep,eligible_only=False) if a["payload"]["object"]["value"]=="later undisputed"]
        assert len(future)==1 and future[0]["payload"]["state"]=="accepted"
        assert future[0]["payload"]["valid_from"]=="2030-01-01T00:00:00Z"
        assert future[0]["payload"]["valid_to"] is None
        uow.commit()


def test_merge_preserves_identity_history_and_stale_inbound_is_rejected(object_case):
    from lzo.auth import models
    from lzo.semantic import facts
    s=object_case
    with s.transaction() as uow:
        s.roles["merge_operator"]=uuid4()
        uow.session.execute(insert(models.roles).values(id=s.roles["merge_operator"],name="merge-operator",
            actions=["view","identity.merge","identity.review","edit"],revision=1))
        uow.commit()
    s.grant("alice","merge_operator",scope="farm_a")
    reviewer_grant=s.grant("admin","merge_operator",scope="farm_a")
    keep,old,inbound=uuid4(),uuid4(),uuid4()
    assert s.create_object(resource_id=keep,assertions=[s.assertion(keep,value="canonical note")]).status_code==201
    original=s.assertion(old,value="retained origin")
    assert s.create_object(resource_id=old,assertions=[original]).status_code==201
    edge=s.assertion(inbound,predicate="urn:monitors",value=facts.object_iri(old),kind="iri")
    assert s.create_object(resource_id=inbound,class_iri="urn:Sensor",assertions=[s.assertion(inbound),edge]).status_code==201
    def preview():
        with s.transaction() as uow:
            uow.ontology_settings=s.config
            result=IdentityService(uow).preview_merge(s.actor("alice"),keep,(old,))
            uow.commit()
        with s.transaction() as uow:
            ApprovalService(uow).decide(s.actor("admin"),result.approval_id,"approve","Independent identity review")
            uow.commit()
        return result
    proposal=preview()
    assert str(inbound) in proposal.baseline
    assert UUID(original["assertion_id"]) in proposal.preserved_candidate_assertion_ids
    assert s.patch(inbound,1,[s.assertion(inbound,state="candidate",value="concurrent")]).status_code==200
    with s.transaction() as uow,pytest.raises(ApiError) as error:
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
    assert error.value.code=="APPROVAL_STALE"
    proposal=preview()
    s.revoke(reviewer_grant)
    with s.transaction() as uow,pytest.raises(ApiError) as error:
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
    assert error.value.status in (403,404)
    s.grant("admin","merge_operator",scope="farm_a")
    # The entire approved composite is still caller-committed, including aliases
    # and consumption. An abandoned UoW must restore every affected head.
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
    with s.transaction() as uow:
        assert facts.get_object_row(uow,s.actor("alice"),old)["payload"]["state"]=="active"
        assert len(facts.current_assertions(uow,keep))==1
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        result=IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
        assert result.resource_id==keep
        uow.commit()
    with s.transaction() as uow:
        assert IdentityService(uow).resolve_alias(s.actor("alice"),old)==keep
        assert facts.get_object_row(uow,s.actor("alice"),old)["payload"]["state"]=="inactive"
        moved=facts.current_assertions(uow,keep)
        candidate=next(a for a in moved if a["payload"]["state"]=="candidate")
        assert candidate["payload"]["object"]["value"]=="retained origin"
        assert candidate["supports"][0]["payload"]["raw_value"]["merge_origin"]["support_id"]==original["support"]["support_id"]
        assert [a["payload"]["object"]["value"] for a in moved if a["payload"]["state"]=="accepted"]==["canonical note"]
        edges=facts.current_assertions(uow,inbound)
        assert any(a["payload"]["object"]["value"]==facts.object_iri(keep) for a in edges)
        from lzo.semantic.merge import owns_origin_withdrawal
        from lzo.contracts import RevisionRef
        rewritten=next(a for a in edges if a["payload"]["object"]["value"]==facts.object_iri(keep))
        origin=rewritten["supports"][0]["payload"]["raw_value"]["merge_origin"]
        original_support=uow.session.execute(select(facts.supports).where(facts.supports.c.id==UUID(origin["support_id"]))).mappings().one()
        exact=rewritten["payload"]["decision_ref"]
        ref=RevisionRef(UUID(exact["resource_id"]),exact["revision"],UUID(exact["event_id"]))
        assert not original_support["active"] and owns_origin_withdrawal(uow,ref,origin,original_support)
        assert not owns_origin_withdrawal(uow,RevisionRef(uuid4(),1,uuid4()),origin,original_support)
        assert not owns_origin_withdrawal(uow,ref,{**origin,"withdrawn_by_merge":False},original_support)
    with s.transaction() as uow,pytest.raises(ApiError):
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id)
