"""Real pre-adoption configuration publication through F06 and ontology catalog."""
from uuid import uuid4
import pytest
from sqlalchemy import insert, select
from lzo.contracts import ResourceScope
from lzo.ingestion.schemas import RuleVersion, SourceCreate, ImpactCommand, ImpactSelection, PublishRuleCommand, FieldMap, AuthorityRule
from lzo.ingestion.rules import RuleService
from lzo.ingestion.sources import SourceService
from lzo.governance.approvals import ApprovalService
from lzo.ontology.releases import current_execution_bundle
from test_ingestion_identity_rulings import profile


def version(kind, **kw):
    return RuleVersion(id=uuid4(), revision=1, kind=kind,
        scope=ResourceScope(None, None, (), "schema"), parent_id=None, rules=(), mapping_profile=None,
        alignment_rules=(), authority_rules=(), ruling_policies=(), status="draft", digest="").model_copy(update=kw)


@pytest.fixture
def configuration_case(object_case):
    from lzo.auth import models
    s = object_case
    with s.transaction() as uow:
        s.roles["source_manager"] = uuid4()
        uow.session.execute(insert(models.roles).values(id=s.roles["source_manager"], name="source-manager",
            actions=["view","source.read","source.manage","conflict.resolve","conflict.review"], revision=1))
        uow.commit()
    s.grant("alice", "source_manager", scope="farm_a")
    s.review_source_grant = s.grant("admin", "source_manager", scope="farm_a")
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        source = SourceService().create(uow, s.actor("alice"), SourceCreate(source_system="farm",
            source_instance="one", collection="pond", kind="file", data_nature="synthetic",
            scope=ResourceScope(None, s.scopes["farm_a"], (), "data")))
        proposed_profile=profile(source.id).model_copy(update={"fields":(
            FieldMap(source_path="area",predicate_iri="urn:area",datatype_iri="http://www.w3.org/2001/XMLSchema#decimal",source_unit="ha",target_unit="m2"),
            FieldMap(source_path="note",predicate_iri="urn:note"))})
        mapping = SourceService(uow).save_mapping(uow, s.actor("alice"), proposed_profile)
        service = RuleService(uow)
        authority = service.create_version(uow, s.actor("alice"), version("authority",authority_rules=(
            AuthorityRule(predicate_iri="urn:area",owner_policy="source_backed",owner_source_ids=(source.id,),digest=""),
            AuthorityRule(predicate_iri="urn:note",owner_policy="source_backed",owner_source_ids=(source.id,),digest=""))))
        ruling = service.create_version(uow, s.actor("alice"), version("ruling"))
        proposed = service.stage_configuration(s.actor("alice"), (mapping.id,), authority.id, ruling.id)
        uow.commit()
    s.i02_source, s.i02_mapping, s.i02_authority, s.i02_ruling, s.i02_bundle = source, mapping, authority, ruling, proposed
    def preview():
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            result = RuleService(uow).impact(s.actor("alice"), ImpactCommand(target_bundle_id=proposed.id,
                selection=ImpactSelection(resource_ids=(), source_ids=(source.id,), scope=ResourceScope(None,None,(),"schema")), rollback_of=None))
            uow.commit()
        return result
    s.i02_preview = preview
    return s


def activate(s):
    impact=s.i02_preview()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),impact.approval_id,"approve","Independent")
        uow.commit()
    with s.transaction() as uow:
        RuleService(uow).publish(uow,s.actor("alice"),PublishRuleCommand(rule_version_id=s.i02_authority.id,
            expected_revision=1,impact_id=impact.id,approval_id=impact.approval_id))
        uow.commit()
    return s.i02_bundle


def raw_change(s, area="2.5", note="platform note", extra="one", *, temporal=None, fields=None):
    import io,json
    from lzo.storage.blobs import get_blob_store
    from lzo.artifacts.service import ArtifactService
    from lzo.ingestion.sources import UploadService
    from lzo.ingestion.files import FileParser
    from lzo.ingestion.schemas import FileOptions
    store=get_blob_store(s.config)
    blob=store.put(io.BytesIO(json.dumps([{"code":"P001","name":"Pond","area":area,"note":note,"extra":extra,**(temporal or {}),**(fields or {})}]).encode()),"application/json")
    options=FileOptions(format="json",encoding="utf-8",delimiter=",",sheet=None,header_row=1,records_path="",
        key_paths=("code",),source_crs=None,source_timezone="Asia/Shanghai",data_nature="synthetic")
    if temporal is not None:
        from lzo.ingestion.schemas import TemporalFields
        options=options.model_copy(update={"temporal_fields":TemporalFields(valid_from_path="start",valid_to_path="end",observed_at_path="seen")})
    with s.transaction() as uow:
        ArtifactService(uow,store,s.config).register_upload(s.actor("alice"),s.ids_by_name["object_a"],blob,filename="pond.json",metadata={})
        service=UploadService(uow,store,s.config)
        binding=service.register(s.actor("alice"),s.i02_source.id,blob,options,str(uuid4()))
        parsed=FileParser(store,binding).parse(blob,binding.source_key,options)
        assert parsed.complete
        service.persist(s.actor("alice"),binding,parsed)
        uow.commit()
    return parsed.changes[0]


def test_pre_adoption_publication_is_blocked_by_real_accepted_history(configuration_case,rdf_repo,gis_tick):
    from lzo.ingestion.identity import IdentityService
    from lzo.ingestion.mapping import MappingService
    from lzo.ingestion.rulings import RulingService
    from lzo.ingestion.schemas import RulingCommand
    from lzo.semantic.facts import register_object
    from lzo.semantic.schemas import CreateObjectInput
    from lzo.errors import ApiError
    s=configuration_case
    bundle=activate(s); change=raw_change(s)
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        replacement=RuleService(uow).stage_configuration(s.actor("alice"),(s.i02_mapping.id,),s.i02_authority.id,s.i02_ruling.id)
        pending_publication=RuleService(uow).impact(s.actor("alice"),ImpactCommand(target_bundle_id=replacement.id,
            selection=ImpactSelection(resource_ids=(),source_ids=(s.i02_source.id,),scope=ResourceScope(None,None,(),"schema")),rollback_of=None))
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        identity=IdentityService(uow).resolve(uow,s.actor("alice"),change,s.i02_mapping,True)
        mapped=MappingService(uow).plan(s.actor("alice"),change,s.i02_mapping,bundle,identity)
        assert len(mapped.facts)==1 and mapped.facts[0].object.value=="25000.0"
        conflict=RulingService(uow).prepare(uow,s.actor("alice"),mapped,bundle)
        approval=RulingService(uow).preview(s.actor("alice"),conflict.id,conflict.candidate_ids,(),"Accept measured area")
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"),approval.id,"approve","Independent field review")
        uow.commit()
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        service=RulingService(uow)
        ruling=service.resolve(uow,s.actor("alice"),conflict.id,RulingCommand(expected_revision=1,
            chosen_candidate_ids=conflict.candidate_ids,rejected_candidate_ids=(),rationale="Accept measured area",approval_id=approval.id))
        create=CreateObjectInput.model_validate(service.accepted_effect(s.actor("alice"),ruling.id)["create"])
        uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError) as altered:
        from lzo.governance.revisions import append_revision
        from lzo.ingestion.rulings import _latest
        uow.ontology_settings=s.config
        append_revision(uow,s.actor("alice"),conflict.id,2,_latest(uow,conflict.id),"ingestion.conflict.resolved")
        register_object(uow,s.actor("alice"),create,bundle)
    assert altered.value.code=="APPROVAL_STALE"
    # An immutable decision survives its own conflict transition, but grants
    # remain current when it is first consumed by S03 in a later transaction.
    s.revoke(s.review_source_grant)
    with s.transaction() as uow,pytest.raises(ApiError) as denied:
        uow.ontology_settings=s.config
        register_object(uow,s.actor("alice"),create,bundle)
    assert denied.value.status in (403,404)
    s.review_source_grant=s.grant("admin","source_manager",scope="farm_a")
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        register_object(uow,s.actor("alice"),create,bundle)
        # Caller rollback leaves both semantic effect and consumption absent.
    with s.transaction() as uow:
        uow.ontology_settings=s.config
        receipt=register_object(uow,s.actor("alice"),create,bundle)
        uow.commit()
    with s.transaction() as uow,pytest.raises(ApiError):
        uow.ontology_settings=s.config
        register_object(uow,s.actor("alice"),create,bundle)
    with s.transaction() as uow,pytest.raises(ApiError) as error:
        from lzo.ingestion.rules import no_prior_adoption
        no_prior_adoption(uow)
    assert error.value.code=="HISTORICAL_IMPACT_REQUIRED"
    with s.transaction() as uow,pytest.raises(ApiError) as error:
        ApprovalService(uow).decide(s.actor("admin"),pending_publication.approval_id,"approve","Cannot publish across adoption")
    assert error.value.code=="HISTORICAL_IMPACT_REQUIRED"
    original=ruling.revision
    support_id=mapped.facts[0].support.support_id
    assert change.valid_from is None and mapped.facts[0].valid_from.year==1
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import graph_iri
    from lzo.semantic import facts
    gis_tick(s)  # The deployed split worker delivers GIS before the exact RDF event.
    with s.transaction() as uow:
        stored=facts.current_assertions(uow,identity.resource_id)[0]
        assert stored["payload"]["valid_from"].startswith("0001-01-01")
        projected=ProjectionService(uow,rdf_repo,actor=s.actor("alice")).project_revision(receipt.revision)
        assert projected.state=="synced"
        uow.commit()
    assert any(t.predicate_iri=="urn:area" and t.object.value=="25000.0" for t in rdf_repo.read_graph(graph_iri(identity.resource_id,"accepted")))
    raw_change(s,extra="unrelated replacement")
    with s.transaction() as uow:
        service=RulingService(uow)
        assert service.currentness(uow,original,support_id).eligible
        unchanged=service.reevaluate(uow,s.actor("alice"),ruling.id,ruling.dependencies,False)
        assert unchanged.id==ruling.id and unchanged.state=="effective"
        uow.commit()
    raw_change(s,area="3.5",extra="material replacement")
    with s.transaction() as uow:
        service=RulingService(uow)
        assert not service.currentness(uow,original,support_id).eligible
        from lzo.semantic import facts
        assert not facts.current_assertions(uow,identity.resource_id)
        changed=service.reevaluate(uow,s.actor("alice"),ruling.id,ruling.dependencies,False)
        assert changed.state=="needs_review" and changed.revision.revision==2
        from lzo.governance.models import revisions
        original_payload=uow.session.execute(select(revisions.c.payload).where(revisions.c.event_id==original.event_id)).scalar_one()
        assert original_payload["state"]=="effective"
        uow.commit()


def test_real_publication_preserves_profiles_and_consumes_approval(configuration_case):
    from lzo.errors import ApiError
    s = configuration_case
    with s.transaction() as uow:
        assert current_execution_bundle(uow, s.actor("alice")).id != s.i02_bundle.id
    impact = s.i02_preview()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"), impact.approval_id, "approve", "Independent publication review")
        uow.commit()
    command = PublishRuleCommand(rule_version_id=s.i02_authority.id, expected_revision=1,
        impact_id=impact.id, approval_id=impact.approval_id)
    headers=s.headers(1)
    response=s.client.post(f"/api/v1/rules/{s.i02_authority.id}/publish",headers=headers,json=command.model_dump(mode="json"))
    assert response.status_code==200,response.text
    replay=s.client.post(f"/api/v1/rules/{s.i02_authority.id}/publish",headers=headers,json=command.model_dump(mode="json"))
    assert replay.status_code==200 and replay.json()==response.json()
    with s.transaction() as uow:
        assert current_execution_bundle(uow, s.actor("alice")).id == s.i02_bundle.id
        assert SourceService(uow).read(s.actor("alice"), s.i02_source.id).mapping_version_id == s.i02_mapping.id
        assert RuleService(uow).get(s.actor("alice"),s.i02_authority.id).status=="published"
    assert s.client.get(f"/api/v1/rules/impact/{impact.id}").status_code==200
    for path in ("rules/versions","conflicts"):
        assert s.client.get("/api/v1/"+path+"?cursor=not-a-uuid").status_code==422
    with s.transaction() as uow, pytest.raises(ApiError):
        RuleService(uow).publish(uow, s.actor("alice"), command)


def test_publication_rechecks_scoped_review_rights(configuration_case):
    from lzo.errors import ApiError
    s = configuration_case
    impact = s.i02_preview()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor("admin"), impact.approval_id, "approve", "Independent")
        uow.commit()
    s.revoke(s.review_source_grant)
    with s.transaction() as uow, pytest.raises(ApiError) as error:
        RuleService(uow).publish(uow, s.actor("alice"), PublishRuleCommand(rule_version_id=s.i02_authority.id,
            expected_revision=1, impact_id=impact.id, approval_id=impact.approval_id))
    assert error.value.status in (403,404)
