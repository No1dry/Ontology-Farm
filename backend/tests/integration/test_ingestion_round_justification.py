"""Replacement justification and case visibility use actual registered evidence."""
from uuid import UUID,uuid4
import pytest
from sqlalchemy import select,insert,update
from lzo.errors import ApiError
from lzo.ingestion.rulings import RulingService
from lzo.ingestion.identity import IdentityService
from lzo.ingestion.mapping import MappingService
from lzo.ingestion.schemas import RulingCommand
from lzo.governance.approvals import ApprovalService
from lzo.semantic import facts
from lzo.semantic.schemas import CreateObjectInput,FactPatch
from test_ingestion_rule_versions import configuration_case,raw_change,activate
from test_ingestion_support_currentness import adopt


def review_snapshot(s,change):
    from lzo.artifacts.service import ArtifactService
    from lzo.artifacts.store import ArtifactStore
    s.grant("admin","editor",scope="farm_a")
    with s.transaction() as uow:
        store=ArtifactStore(s.config.file_root/"artifacts",max_upload_bytes=s.config.artifact_max_upload_bytes)
        service=ArtifactService(uow,store,s.config)
        event=service.review(s.actor("admin"),change.snapshot.version_id,"accepted","Independent snapshot review",
            facts.revision_ref(uow,change.snapshot.version_id).revision)
        uow.commit()
    return event


def approve_resolve(s,conflict,rationale):
    with s.transaction() as uow:
        approval=RulingService(uow).preview(s.actor("alice"),conflict.id,conflict.candidate_ids,(),rationale)
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Independent ruling review");uow.commit()
    with s.transaction() as uow:
        ruling=RulingService(uow).resolve(uow,s.actor("alice"),conflict.id,RulingCommand(expected_revision=conflict.revision,
            chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale=rationale,approval_id=approval.id))
        uow.commit()
    return ruling


def refresh_after_review(s,original):
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=RulingService(uow)
        review=service.reevaluate(uow,s.actor("alice"),original.id,original.dependencies,False)
        assert review.state=="needs_review"
        case=service.get(s.actor("alice"),original.conflict_id)
        refreshed=service.refresh(uow,s.actor("alice"),case.id,case.revision,review.revision)
        uow.commit()
    return refreshed


def test_same_record_snapshot_review_replacement_has_immutable_round_justification(configuration_case):
    from lzo.ingestion.models import source_records
    from lzo.governance.models import revisions
    s=configuration_case;bundle=activate(s);change=raw_change(s)
    identity,mapped,original=adopt(s,change,s.i02_mapping,bundle)
    with s.transaction() as uow:
        old_assertion=uow.session.execute(select(facts.assertions.c.payload).where(facts.assertions.c.id==mapped.facts[0].assertion_id)).scalar_one()
        old_support=uow.session.execute(select(facts.supports.c.payload).where(facts.supports.c.id==mapped.facts[0].support.support_id)).scalar_one()
        old_record=uow.session.execute(select(source_records.c.payload).where(source_records.c.id==change.change_id)).scalar_one()
    review_snapshot(s,change)
    refreshed=refresh_after_review(s,original)
    with s.transaction() as uow:
        from lzo.ingestion.rulings import _context
        prepared=_context(uow,refreshed.id)
        intent={"conflict_id":str(refreshed.id),"chosen_candidate_ids":sorted(map(str,refreshed.candidate_ids)),
            "rejected_candidate_ids":[],"rationale":"Replace snapshot-reviewed justification"}
        first=ApprovalService(uow).preview(s.actor("alice"),"ingestion.ruling",[refreshed.id],intent)
        second=ApprovalService(uow).preview(s.actor("alice"),"ingestion.ruling",[refreshed.id],intent)
        assert first.change_digest==second.change_digest and _context(uow,refreshed.id)==prepared
    replacement=approve_resolve(s,refreshed,"Replace snapshot-reviewed justification")
    with s.transaction() as uow:
        uow.ontology_settings=s.config;service=RulingService(uow)
        effect=service.accepted_effect(s.actor("alice"),replacement.id)
        patch=FactPatch.model_validate(effect["patch"])
        # Run the real immutable S03 insertion in a later transaction. A reused
        # support ID must fail here on the unfixed producer.
        facts.apply_fact_patch(uow,s.actor("alice"),patch,bundle)
        assert patch.additions[0].assertion_id!=mapped.facts[0].assertion_id
        assert patch.additions[0].support.support_id!=mapped.facts[0].support.support_id
        assert patch.additions[0].support.snapshot==change.snapshot
        assert patch.additions[0].support.mapping_version_id==s.i02_mapping.id
        origin=patch.additions[0].support.raw_value["ruling_round_origin"]
        assert origin["round_id"]==prepared["round_id"]
        assert origin["mapped_assertion_id"]==str(mapped.facts[0].assertion_id)
        assert origin["mapped_support_id"]==str(mapped.facts[0].support.support_id)
        assert origin["change_id"]==str(change.change_id) and origin["profile_id"]==str(s.i02_mapping.id)
        assert origin["snapshot"]==facts.json_value(change.snapshot)
        assert patch.additions[0].support.evidence_refs==mapped.facts[0].support.evidence_refs
        assert service.accepted_effect(s.actor("alice"),replacement.id)==effect
        uow.commit()
    with s.transaction() as uow:
        service=RulingService(uow)
        assert not service.currentness(uow,original.revision,mapped.facts[0].support.support_id).eligible
        assert service.currentness(uow,replacement.revision,patch.additions[0].support.support_id).eligible
        assert service.reevaluate(uow,s.actor("alice"),original.id,original.dependencies,False).state=="superseded"
        current=facts.current_assertions(uow,identity.resource_id)
        assert len(current)==1 and current[0]["payload"]["decision_ref"]==facts.json_value(replacement.revision)
        assert uow.session.execute(select(facts.assertions.c.payload).where(facts.assertions.c.id==mapped.facts[0].assertion_id)).scalar_one()==old_assertion
        assert uow.session.execute(select(facts.supports.c.payload).where(facts.supports.c.id==mapped.facts[0].support.support_id)).scalar_one()==old_support
        assert uow.session.execute(select(source_records.c.payload).where(source_records.c.id==change.change_id)).scalar_one()==old_record
        assert len(uow.session.execute(select(source_records.c.id)).all())==1
    with s.transaction() as uow,pytest.raises(ApiError):
        facts.apply_fact_patch(uow,s.actor("alice"),patch,bundle)


def test_refreshed_conflict_visibility_filters_prior_ruling_before_pagination(configuration_case):
    from lzo.auth import models
    from lzo.auth.session import resolve_identity
    from lzo.ontology.drafts import definition_source_closure
    from lzo.ingestion.rulings import _context
    s=configuration_case;bundle=activate(s);cases=[]
    for index in range(3):
        change=raw_change(s,fields={"code":"P"+str(index)})
        with s.transaction() as uow:
            identity=IdentityService(uow).resolve(uow,s.actor("alice"),change,s.i02_mapping,True)
            mapped=MappingService(uow).plan(s.actor("alice"),change,s.i02_mapping,bundle,identity)
            case=RulingService(uow).prepare(uow,s.actor("alice"),mapped,bundle)
            cases.append((case,change));uow.commit()
    cases.sort(key=lambda pair:str(pair[0].id));middle,change=cases[1]
    original=approve_resolve(s,middle,"Accept middle source")
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        effect=RulingService(uow).accepted_effect(s.actor("alice"),original.id)
        facts.register_object(uow,s.actor("alice"),CreateObjectInput.model_validate(effect["create"]),bundle);uow.commit()
    review_snapshot(s,change);refreshed=refresh_after_review(s,original)
    with s.transaction() as uow:
        s.users["case_reader"]=resolve_identity(uow,"https://test-issuer","case-reader","Case reader","case@example.invalid")
        role=uuid4();uow.session.execute(insert(models.roles).values(id=role,name="case-reader",actions=["view"],revision=1))
        required={case.id for case,_ in cases}
        for case,_ in cases:required.update(UUID(i) for i in _context(uow,case.id)["protected_ids"])
        required=definition_source_closure(uow,required)-{original.id}
        # Explicit isolated fixture grants use each real resource's classification;
        # this reader inherits neither schema-global nor scope-wide visibility.
        for row in uow.session.execute(select(models.resource_scopes).where(models.resource_scopes.c.resource_id.in_(required))).mappings():
            uow.session.execute(insert(models.grants).values(id=uuid4(),user_id=s.users["case_reader"],role_id=role,
                resource_id=row["resource_id"],security_class=row["security_class"],revision=1))
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1));uow.commit()
    def pages(hidden):
        with s.transaction() as uow:
            service=RulingService(uow);actor=s.actor("case_reader")
            if hidden:
                with pytest.raises(ApiError) as denied:service.get(actor,refreshed.id)
                assert denied.value.status==404
            else:assert service.get(actor,refreshed.id).id==refreshed.id
            expected=[case.id for case,_ in cases if not hidden or case.id!=refreshed.id]
            first=service.list_conflicts(actor,limit=1)
            assert [c.id for c in first.items]==expected[:1]
            collected=list(first.items);cursor=first.next_cursor
            while cursor:
                page=service.list_conflicts(actor,cursor=cursor,limit=1)
                assert page.items
                collected.extend(page.items);cursor=page.next_cursor
            assert [c.id for c in collected]==expected
            complete=service.list_conflicts(actor,limit=len(expected))
            assert [c.id for c in complete.items]==expected and complete.next_cursor is None
    pages(True)
    s.ids_by_name["prior-ruling"]=original.id
    grant=s.grant("case_reader","viewer",resource="prior-ruling")
    pages(False)
    s.revoke(grant);pages(True)
