"""Confirmed scoped business-key identity reserves once under concurrency."""
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import pytest
from sqlalchemy import select,func
from lzo.contracts import ResourceScope
from lzo.ingestion.schemas import AlignmentRule,SourceCreate,ImpactCommand,ImpactSelection,PublishRuleCommand
from lzo.ingestion.sources import SourceService
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.rules import RuleService
from lzo.governance.approvals import ApprovalService
from lzo.ingestion.models import identity_bindings,alignment_keys
from test_ingestion_rule_versions import configuration_case,version,raw_change
from test_ingestion_identity_rulings import profile


def test_stable_identity_requires_confirmed_business_key_and_concurrent_unique_reserve(configuration_case):
    s=configuration_case
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        rule=RuleService(uow).create_version(uow,s.actor("alice"),version("alignment",alignment_rules=(AlignmentRule(
            id=uuid4(),digest="",class_iri="urn:Pond",key_paths=("code",),normalizers=(),
            scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),confirmed=True),)))
        shared=rule.alignment_rules[0].id
        first=SourceService(uow).save_mapping(uow,s.actor("alice"),s.i02_mapping.model_copy(update={"confirmed_identity_rule_id":shared}))
        second_source=SourceService().create(uow,s.actor("alice"),SourceCreate(source_system="other",source_instance="two",collection="pond",
            kind="file",scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),data_nature="synthetic"))
        second=SourceService(uow).save_mapping(uow,s.actor("alice"),profile(second_source.id).model_copy(update={"confirmed_identity_rule_id":shared}))
        name_source=SourceService().create(uow,s.actor("alice"),SourceCreate(source_system="other",source_instance="three",collection="pond",
            kind="file",scope=ResourceScope(None,s.scopes["farm_a"],(),"data"),data_nature="synthetic"))
        name_only=SourceService(uow).save_mapping(uow,s.actor("alice"),profile(name_source.id).model_copy(update={"identity_paths":()}))
        bundle=RuleService(uow).stage_configuration(s.actor("alice"),(first.id,second.id,name_only.id),s.i02_authority.id,s.i02_ruling.id)
        impact=RuleService(uow).impact(s.actor("alice"),ImpactCommand(target_bundle_id=bundle.id,selection=ImpactSelection(
            resource_ids=(),source_ids=(s.i02_source.id,second_source.id,name_source.id),scope=ResourceScope(None,None,(),"schema")),rollback_of=None))
        uow.commit()
    s.revoke(s.review_source_grant)
    s.grant("admin","editor",scope="farm_a")
    for label,source in (("first",s.i02_source),("second",second_source),("third",name_source)):
        s.ids_by_name[label]=source.id
    s.grant("admin","source_manager",resource="first")
    s.grant("admin","source_manager",resource="second")
    from lzo.errors import ApiError
    with s.transaction() as uow,pytest.raises(ApiError) as partial:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Missing third source authority")
    assert partial.value.status==403
    s.grant("admin","source_manager",resource="third")
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Confirmed cross-source identity")
        uow.commit()
    with s.transaction() as uow:
        RuleService(uow).publish(uow,s.actor("alice"),PublishRuleCommand(rule_version_id=s.i02_authority.id,expected_revision=1,
            impact_id=impact.id,approval_id=impact.approval_id))
        uow.commit()
    first_change=raw_change(s)
    original=s.i02_source
    s.i02_source=second_source;second_change=raw_change(s)
    s.i02_source=name_source;ambiguous=raw_change(s)
    s.i02_source=original
    with s.transaction() as uow:
        dry=IdentityService(uow).resolve(uow,s.actor("alice"),first_change,first,False)
        assert dry.resource_id is None and dry.state=="new"
        assert uow.session.execute(select(func.count()).select_from(identity_bindings)).scalar_one()==0
    barrier=Barrier(2);actor=s.actor("alice")
    def reserve(pair):
        change,profile=pair
        with s.transaction() as uow:
            barrier.wait(timeout=10)
            match=IdentityService(uow).resolve(uow,actor,change,profile,True)
            uow.commit()
            return match
    with ThreadPoolExecutor(max_workers=2) as workers:
        a,b=list(workers.map(reserve,((first_change,first),(second_change,second))))
    assert a.resource_id==b.resource_id
    with s.transaction() as uow:
        match=IdentityService(uow).resolve(uow,s.actor("alice"),ambiguous,name_only,True)
        assert match.state=="candidate" and match.resource_id is None
        assert uow.session.execute(select(func.count()).select_from(identity_bindings)).scalar_one()==2
        assert uow.session.execute(select(func.count()).select_from(alignment_keys)).scalar_one()==1
