"""Real registered file clocks, independent decisions, and read-time truth."""
from datetime import datetime,timedelta,timezone
import time
from uuid import uuid4
from lzo.contracts import ResourceScope
from lzo.ingestion.schemas import AlignmentRule,SourceCreate,AuthorityRule,ImpactCommand,ImpactSelection,PublishRuleCommand,RulingCommand
from lzo.ingestion.sources import SourceService
from lzo.ingestion.rules import RuleService
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.mapping import MappingService
from lzo.ingestion.rulings import RulingService
from lzo.governance.approvals import ApprovalService
from lzo.semantic import facts
from lzo.semantic.schemas import CreateObjectInput,FactPatch
from test_ingestion_rule_versions import configuration_case,version,raw_change,activate


def adopt(s,change,profile,bundle):
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        identity=IdentityService(uow).resolve(uow,s.actor("alice"),change,profile,True)
        mapped=MappingService(uow).plan(s.actor("alice"),change,profile,bundle,identity)
        service=RulingService(uow);conflict=service.prepare(uow,s.actor("alice"),mapped,bundle)
        approval=service.preview(s.actor("alice"),conflict.id,conflict.candidate_ids,(),"Accept independent evidence")
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Independent field review")
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=RulingService(uow);ruling=service.resolve(uow,s.actor("alice"),conflict.id,RulingCommand(expected_revision=1,
            chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale="Accept independent evidence",approval_id=approval.id))
        effect=service.accepted_effect(s.actor("alice"),ruling.id)
        if effect["operation"]=="object.create":facts.register_object(uow,s.actor("alice"),CreateObjectInput.model_validate(effect["create"]),bundle)
        else:facts.apply_fact_patch(uow,s.actor("alice"),FactPatch.model_validate(effect["patch"]),bundle)
        uow.commit()
    return identity,mapped,ruling


def test_expired_last_support_withdraws_even_while_ruling_pending(configuration_case):
    s=configuration_case;bundle=activate(s)
    expiry=datetime.now(timezone.utc)+timedelta(seconds=20)
    change=raw_change(s,temporal={"start":None,"end":expiry.isoformat(),"seen":None})
    identity,mapped,ruling=adopt(s,change,s.i02_mapping,bundle)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        pending=RulingService(uow).prepare(uow,s.actor("alice"),mapped,bundle)
        uow.commit()
    before=s.client.get(f"/api/v1/objects/{identity.resource_id}").json()
    assert before["properties"]["urn:area"]
    while datetime.now(timezone.utc)<expiry:time.sleep(.1)
    after=s.client.get(f"/api/v1/objects/{identity.resource_id}").json()
    assert after["properties"]=={} and after["owner_scope_id"]==str(s.scopes["farm_a"])
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.get(s.actor("alice"),pending.id).state=="open"
        assert service.ruling(s.actor("alice"),ruling.id).revision==ruling.revision
        assert service.currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible


def test_registered_file_expiry_and_material_invalidation_preserve_independent_support(configuration_case):
    s=configuration_case;first_source=s.i02_source
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=RuleService(uow)
        alignment=service.create_version(uow,s.actor("alice"),version("alignment",alignment_rules=(AlignmentRule(id=uuid4(),digest="",
            class_iri="urn:Pond",key_paths=("code",),normalizers=(),scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),confirmed=True),)))
        first=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={"confirmed_identity_rule_id":alignment.alignment_rules[0].id}))
        second_source=SourceService().create(uow,s.actor("alice"),SourceCreate(source_system="other",source_instance="two",collection="pond",
            kind="file",scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),data_nature="synthetic"))
        second=SourceService(uow).save_mapping(uow,s.actor("alice"),first.model_copy(update={"source_id":second_source.id}))
        authority=service.create_version(uow,s.actor("alice"),version("authority",authority_rules=(AuthorityRule(predicate_iri="urn:area",
            owner_policy="source_backed",owner_source_ids=(first_source.id,second_source.id),digest=""),)))
        bundle=service.stage_configuration(s.actor("alice"),(first.id,second.id),authority.id,s.i02_ruling.id)
        impact=service.impact(s.actor("alice"),ImpactCommand(target_bundle_id=bundle.id,selection=ImpactSelection(resource_ids=(),
            source_ids=(first_source.id,second_source.id),scope=ResourceScope(None,None,(),"schema")),rollback_of=None));uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Independent configuration review");uow.commit()
    with s.transaction() as uow:
        service=RuleService(uow);service.publish(uow,s.actor("alice"),PublishRuleCommand(rule_version_id=authority.id,expected_revision=1,
            impact_id=impact.id,approval_id=impact.approval_id));uow.commit()
    expiry=datetime.now(timezone.utc)+timedelta(seconds=40)
    temporal={"start":"2026-01-01T00:00:00Z","end":expiry.isoformat(),"seen":"2026-01-01T01:00:00Z"}
    one=raw_change(s,temporal=temporal)
    identity,mapped,ruling=adopt(s,one,first,bundle)
    assert one.received_at!=one.valid_from and mapped.facts[0].valid_to==expiry
    s.i02_source=second_source
    two=raw_change(s,temporal={**temporal,"end":None})
    other,other_mapped,other_ruling=adopt(s,two,second,bundle)
    assert other.resource_id==identity.resource_id
    with s.transaction() as uow:
        assert RulingService(uow).currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        assert len(facts.current_assertions(uow,identity.resource_id))==2
    assert datetime.now(timezone.utc)<expiry,"Test must observe both supports before real expiry"
    while datetime.now(timezone.utc)<expiry:time.sleep(.1)
    view=s.client.get(f"/api/v1/objects/{identity.resource_id}").json()
    assert view["properties"]["urn:area"][0]["value"]=="25000.0"
    s.i02_source=first_source
    raw_change(s,area="3.5",temporal=temporal)
    with s.transaction() as uow:
        service=RulingService(uow)
        assert not service.currentness(uow,ruling.revision,mapped.facts[0].support.support_id).eligible
        assert service.currentness(uow,other_ruling.revision,other_mapped.facts[0].support.support_id).eligible
        current=facts.current_assertions(uow,identity.resource_id)
        assert len(current)==1 and current[0]["supports"][0]["id"]==other_mapped.facts[0].support.support_id


def test_merged_source_support_keeps_material_justification_and_platform_note(configuration_case):
    from sqlalchemy import insert
    from lzo.auth import models
    s=configuration_case;bundle=activate(s)
    s.bundle_id=bundle.id
    change=raw_change(s)
    identity,mapped,ruling=adopt(s,change,s.i02_mapping,bundle)
    with s.transaction() as uow:
        s.roles["merge_operator"]=uuid4()
        uow.session.execute(insert(models.roles).values(id=s.roles["merge_operator"],name="merge-operator",
            actions=["view","identity.merge","identity.review","edit"],revision=1));uow.commit()
    s.grant("alice","merge_operator",scope="farm_a");s.grant("admin","merge_operator",scope="farm_a")
    canonical=uuid4()
    assert s.create_object(resource_id=canonical,assertions=[s.assertion(canonical,value="platform note")]).status_code==201
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        proposal=IdentityService(uow).preview_merge(s.actor("alice"),canonical,(identity.resource_id,));uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),proposal.approval_id,"approve","Independent merge review");uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        IdentityService(uow).merge(uow,s.actor("alice"),proposal.id,proposal.approval_id);uow.commit()
    before=s.client.get(f"/api/v1/objects/{canonical}").json()
    assert set(before["properties"])=={"urn:area","urn:note"}
    raw_change(s,area="3.5")
    after=s.client.get(f"/api/v1/objects/{canonical}").json()
    assert set(after["properties"])=={"urn:note"} and after["properties"]["urn:note"][0]["value"]=="platform note"
    with s.transaction() as uow:
        assert IdentityService(uow).resolve_alias(s.actor("alice"),identity.resource_id)==canonical
        assert RulingService(uow).ruling(s.actor("alice"),ruling.id).revision==ruling.revision
