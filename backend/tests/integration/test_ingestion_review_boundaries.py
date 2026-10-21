"""Review regressions use registered source inputs and real governed effects."""
import pytest
from lzo.errors import ApiError
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.mapping import MappingService
from lzo.ingestion.rulings import RulingService
from lzo.ingestion.rules import RuleService
from lzo.ingestion.schemas import RulingPolicyRule, RuleExpr, ImpactCommand, ImpactSelection, PublishRuleCommand
from lzo.contracts import ResourceScope
from lzo.governance.approvals import ApprovalService
from test_ingestion_rule_versions import configuration_case, version, raw_change, activate
from test_ingestion_support_currentness import adopt


def publish(s, bundle, authority):
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        impact=RuleService(uow).impact(s.actor("alice"), ImpactCommand(target_bundle_id=bundle.id,
            selection=ImpactSelection(resource_ids=(),source_ids=(s.i02_source.id,),scope=ResourceScope(None,None,(),"schema")),rollback_of=None))
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Independent configuration")
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        RuleService(uow).publish(uow,s.actor("alice"),PublishRuleCommand(rule_version_id=authority.id,
            expected_revision=1,impact_id=impact.id,approval_id=impact.approval_id))
        uow.commit()


@pytest.mark.parametrize("changed",[True,False])
def test_preparation_fingerprints_exact_candidate_not_later_record(configuration_case,changed):
    s=configuration_case; bundle=activate(s)
    old=raw_change(s)
    raw_change(s,area="3.5" if changed else "2.5",extra="new metadata")
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        identity=IdentityService(uow).resolve(uow,s.actor("alice"),old,s.i02_mapping,True)
        mapped=MappingService(uow).plan(s.actor("alice"),old,s.i02_mapping,bundle,identity)
        if changed:
            with pytest.raises(ApiError) as error: RulingService(uow).prepare(uow,s.actor("alice"),mapped,bundle)
            assert error.value.code=="SOURCE_EVIDENCE_SUPERSEDED"
        else:
            conflict=RulingService(uow).prepare(uow,s.actor("alice"),mapped,bundle)
            assert conflict.candidate_values[-1].normalized_value.value=="25000.0"


def test_policy_only_input_invalidates_support_but_unrelated_metadata_does_not(configuration_case):
    s=configuration_case
    expression=RuleExpr(op="eq",args=(RuleExpr(op="path",path="quality_ok"),RuleExpr(op="literal",value={"value":True})))
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=RuleService(uow)
        policy=service.create_version(uow,s.actor("alice"),version("ruling",ruling_policies=(
            RulingPolicyRule(predicate_iris=("urn:area",),expression=expression,always_review=True,digest=""),)))
        bundle=service.stage_configuration(s.actor("alice"),(s.i02_mapping.id,),s.i02_authority.id,policy.id)
        uow.commit()
    publish(s,bundle,s.i02_authority)
    change=raw_change(s,fields={"quality_ok":True})
    identity,mapped,ruling=adopt(s,change,s.i02_mapping,bundle)
    raw_change(s,extra="irrelevant",fields={"quality_ok":True})
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        assert service.reevaluate(uow,s.actor("alice"),ruling.id,ruling.dependencies,False).state=="effective"
    raw_change(s,extra="irrelevant",fields={"quality_ok":False})
    with s.transaction() as uow:
        service=RulingService(uow)
        assert not service.currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        assert service.reevaluate(uow,s.actor("alice"),ruling.id,ruling.dependencies,False).state=="needs_review"
    missing=raw_change(s,extra="missing policy input")
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.currentness(uow,ruling.revision,mapped.facts[0].support.support_id).reason=="unknown_equivalence"
        identity=IdentityService(uow).resolve(uow,s.actor("alice"),missing,s.i02_mapping,False)
        candidate=MappingService(uow).plan(s.actor("alice"),missing,s.i02_mapping,bundle,identity)
        with pytest.raises(ApiError) as error:service.prepare(uow,s.actor("alice"),candidate,bundle)
        assert error.value.code=="RULING_EVALUATION_UNKNOWN"


def test_source_pagination_filters_hidden_profile_dependencies_before_lookahead(configuration_case):
    from uuid import uuid4
    from lzo.ingestion.sources import SourceService
    from lzo.ingestion.schemas import SourceCreate,AlignmentRule
    from test_ingestion_identity_rulings import profile
    s=configuration_case
    from lzo.auth.session import resolve_identity
    with s.transaction() as uow:
        s.users["source_reader"]=resolve_identity(uow,"https://test-issuer","source-reader","Source reader","reader@example.invalid")
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=SourceService(uow)
        sources=[s.i02_source]+[service.create(uow,s.actor("alice"),SourceCreate(source_system="other",
            source_instance=str(i),collection="pond",kind="file",data_nature="synthetic",
            scope=ResourceScope(None,s.scopes["farm_a"],(),"data"))) for i in range(2)]
        sources.sort(key=lambda source:str(source.id))
        rules=RuleService(uow)
        alignment=rules.create_version(uow,s.actor("alice"),version("alignment",alignment_rules=(AlignmentRule(
            id=uuid4(),digest="",class_iri="urn:Pond",key_paths=("code",),normalizers=(),
            scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),confirmed=True),)))
        profiles=[service.save_mapping(uow,s.actor("alice"),profile(source.id).model_copy(update={
            "confirmed_identity_rule_id":alignment.alignment_rules[0].id if i==1 else None})) for i,source in enumerate(sources)]
        bundle=rules.stage_configuration(s.actor("alice"),tuple(p.id for p in profiles),s.i02_authority.id,s.i02_ruling.id)
        impact=rules.impact(s.actor("alice"),ImpactCommand(target_bundle_id=bundle.id,selection=ImpactSelection(
            resource_ids=(),source_ids=tuple(source.id for source in sources),scope=ResourceScope(None,None,(),"schema")),rollback_of=None))
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Independent configuration");uow.commit()
    with s.transaction() as uow:
        RuleService(uow).publish(uow,s.actor("alice"),PublishRuleCommand(rule_version_id=s.i02_authority.id,
            expected_revision=1,impact_id=impact.id,approval_id=impact.approval_id));uow.commit()
    for i,resource in enumerate([*sources,*profiles]):
        label="list-resource-"+str(i);s.ids_by_name[label]=resource.id
        s.grant("source_reader","source_manager",resource=label)
    with s.transaction() as uow:
        service=SourceService(uow);actor=s.actor("source_reader")
        assert service.read(s.actor("alice"),sources[1].id).mapping_version_id==profiles[1].id
        with pytest.raises(ApiError) as hidden:service.read(actor,sources[1].id)
        assert hidden.value.status==404
        first=service.list(actor,limit=1)
        assert [v.id for v in first.items]==[sources[0].id]
        assert first.next_cursor==str(sources[0].id)
        second=service.list(actor,cursor=first.next_cursor,limit=1)
        assert [v.id for v in second.items]==[sources[2].id] and second.next_cursor is None
        assert [v.id for v in service.list(actor,limit=2).items]==[sources[0].id,sources[2].id]
        assert service.list(actor,limit=2).next_cursor is None
        assert service.read(actor,sources[2].id).mapping_version_id==profiles[2].id
        with pytest.raises(ApiError) as error:service.read(actor,sources[1].id)
        assert error.value.status==404


def test_material_review_replacement_keeps_history_and_consumes_exact_own_pair(configuration_case):
    from sqlalchemy import select
    from lzo.governance.models import revisions
    from lzo.ingestion.schemas import RulingCommand,RulingView,ConflictView
    from lzo.semantic import facts
    from lzo.semantic.schemas import FactPatch
    s=configuration_case;bundle=activate(s)
    identity,mapped,original=adopt(s,raw_change(s),s.i02_mapping,bundle)
    with s.transaction() as uow:
        original_payload=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==original.revision.event_id)).scalar_one()
    fresh=raw_change(s,area="3.5")
    with s.transaction() as uow:
        service=RulingService(uow)
        conflict=service.get(s.actor("alice"),original.conflict_id)
    body={"expected_revision":conflict.revision,"ruling_ref":facts.json_value(original.revision)}
    url=f"/api/v1/conflicts/{conflict.id}/review"
    assert s.client.post(url,headers=s.headers(),json=body).status_code==428
    headers=s.headers(conflict.revision)
    response=s.client.post(url,headers=headers,json=body)
    assert response.status_code==200
    review=RulingView.model_validate(response.json());assert review.state=="needs_review"
    replay=s.client.post(url,headers=headers,json=body)
    assert replay.status_code==200 and replay.json()==response.json()
    conflict=ConflictView.model_validate(s.client.get(f"/api/v1/conflicts/{conflict.id}").json())
    body={"expected_revision":conflict.revision,"ruling_ref":facts.json_value(review.revision)}
    url=f"/api/v1/conflicts/{conflict.id}/refresh"
    assert s.client.post(url,headers=s.headers(),json=body).status_code==428
    headers=s.headers(conflict.revision)
    response=s.client.post(url,headers=headers,json=body)
    assert response.status_code==200
    refreshed=ConflictView.model_validate(response.json())
    replay=s.client.post(url,headers=headers,json=body)
    assert replay.status_code==200 and replay.json()==response.json()
    assert refreshed.id==conflict.id and refreshed.state=="open"
    assert refreshed.candidate_values[0].snapshot==fresh.snapshot
    assert refreshed.candidate_values[0].normalized_value.value=="35000.0"
    with s.transaction() as uow:
        service=RulingService(uow)
        approval=service.preview(s.actor("alice"),refreshed.id,refreshed.candidate_ids,(),"Replace changed evidence")
        uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError):
        RulingService(uow).refresh(uow,s.actor("alice"),conflict.id,conflict.revision,review.revision)
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Independent replacement review");uow.commit()
    command=RulingCommand(expected_revision=refreshed.revision,chosen_candidate_ids=refreshed.candidate_ids,
        rejected_candidate_ids=(),rationale="Replace changed evidence",approval_id=approval.id)
    with s.transaction() as uow:
        from lzo.ingestion.models import rulings
        old_approval=uow.session.execute(select(rulings.c.payload).where(rulings.c.id==original.id)).scalar_one()["approval_id"]
        from uuid import UUID
        with pytest.raises(ApiError):RulingService(uow).resolve(uow,s.actor("alice"),refreshed.id,
            command.model_copy(update={"approval_id":UUID(old_approval)}))
    # Abandon a successful resolution: both replacement and supersession roll back.
    with s.transaction() as uow:
        replacement=RulingService(uow).resolve(uow,s.actor("alice"),refreshed.id,command)
        assert replacement.replaces_id==original.id
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.ruling(s.actor("alice"),original.id).revision==review.revision
        replacement=service.resolve(uow,s.actor("alice"),refreshed.id,command)
        assert service.ruling(s.actor("alice"),original.id).state=="superseded"
        uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError):
        RulingService(uow).resolve(uow,s.actor("alice"),refreshed.id,command)
    # Neither exact own transition permits an external head advance, even with
    # identical payload. Both attempts roll back and do not poison later checks.
    from lzo.governance.revisions import append_revision
    for identifier in (original.id,refreshed.id):
        with s.transaction() as uow:
            uow.ontology_settings=s.config;service=RulingService(uow)
            ref=facts.revision_ref(uow,identifier)
            payload=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==ref.event_id)).scalar_one()
            append_revision(uow,s.actor("alice"),identifier,ref.revision,payload,"ingestion.ruling.reevaluated")
            effect=service.accepted_effect(s.actor("alice"),replacement.id)
            with pytest.raises(ApiError):facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
    s.revoke(s.review_source_grant)
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=RulingService(uow)
        effect=service.accepted_effect(s.actor("alice"),replacement.id)
        with pytest.raises(ApiError) as revoked:facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
        assert revoked.value.status in (403,404)
    s.grant("admin","source_manager",scope="farm_a")
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
        # Successful S03 work abandoned with the caller transaction.
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=RulingService(uow)
        effect=service.accepted_effect(s.actor("alice"),replacement.id)
        facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
        uow.commit()
    with s.transaction() as uow:
        service=RulingService(uow)
        assert not service.currentness(uow,original.revision,mapped.facts[0].support.support_id).eligible
        assert service.ruling(s.actor("alice"),replacement.id).state=="effective"
        assert uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==original.revision.event_id)).scalar_one()==original_payload
        assert {r.id for r in service.list_rulings(s.actor("alice"),refreshed.id).items}=={original.id,replacement.id}
        assert [a["payload"]["object"]["value"] for a in facts.current_assertions(uow,identity.resource_id)]==["35000.0"]
    with s.transaction() as uow,pytest.raises(ApiError):
        facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
    raw_change(s,area="4.5")
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.reevaluate(uow,s.actor("alice"),replacement.id,replacement.dependencies,False).state=="needs_review"
        assert service.reevaluate(uow,s.actor("alice"),original.id,original.dependencies,False).state=="superseded"
        assert not service.currentness(uow,original.revision,mapped.facts[0].support.support_id).eligible
