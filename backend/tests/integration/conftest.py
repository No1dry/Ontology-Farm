"""Semantic acceptance uses the real configured Fuseki service."""
from uuid import UUID, uuid4
import pytest
from test_schema_releases import schema_case
from conftest_ingestion import evidence_case, artifact_http, source_servers, source_servers_without_tls, database_case, database_case_without_tls, scan_case


@pytest.fixture
def object_case(schema_case, request):
    """Published definitions, authenticated HTTP, real PG/Fuseki; no service doubles."""
    from lzo.worker.runner import Runner
    from sqlalchemy import select
    from lzo.ontology.models import bundles
    s = schema_case
    s.object_editor_grant = s.grant("alice", "editor", scope="farm_a")
    document = {
        "classes": [{"iri": "urn:Sensor", "label": "Sensor"},
                    {"iri": "urn:SpecialSensor", "label": "Special", "parents": ["urn:Sensor"]},
                    {"iri": "urn:Pond", "label": "Pond"},
                    {"iri": "urn:Shipment", "label": "Shipment"}],
        "properties": [{"iri": "urn:note", "label": "Note",
                        "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
                        "owner_policy_iri": "urn:lzo:authority:ontology_owned"},
                       {"iri": "urn:area", "label": "Area",
                        "datatype_iri": "http://www.w3.org/2001/XMLSchema#decimal",
                        "owner_policy_iri": "urn:lzo:authority:source_backed"}],
        "relations": [{"iri": "urn:monitors", "label": "Monitors",
                       "domain_iris": ["urn:Sensor"], "range_iris": ["urn:Pond"]}],
        "shapes": """@prefix sh:<http://www.w3.org/ns/shacl#>.
          <urn:SensorShape> a sh:NodeShape; sh:targetClass <urn:Sensor>;
            sh:property [sh:path <urn:note>; sh:minCount 1; sh:maxCount 1]."""}
    if getattr(request, "param", None) == "nested":
        document["shapes"] += """
          <urn:NestedSensor> a sh:NodeShape; sh:targetClass <urn:Sensor>;
            sh:property [sh:path <urn:monitors>; sh:node <urn:NestedPond>].
          <urn:NestedPond> a sh:NodeShape;
            sh:property [sh:path <urn:area>; sh:minCount 1]."""
    if getattr(request, "param", None) in ("target_objects", "upstream", "filtered_upstream"):
        document["properties"].append({"iri": "urn:status", "label": "Status",
            "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
            "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
    if getattr(request, "param", None) == "target_objects":
        document["shapes"] += """
          [] a sh:NodeShape; sh:targetObjectsOf <urn:monitors>;
            sh:property [sh:path <urn:status>; sh:minCount 1; sh:maxCount 1]."""
    if getattr(request, "param", None) == "upstream":
        document["properties"].append({"iri": "urn:flag", "label": "Flag",
            "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
            "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
        document["shapes"] += """
          <urn:UpstreamRoot> a sh:NodeShape; sh:targetClass <urn:Sensor>;
            sh:property [sh:path <urn:monitors>; sh:node <urn:PondStatus>].
          <urn:PondStatus> sh:property [sh:path <urn:status>; sh:minCount 1; sh:pattern "^ok$"].
          <urn:NegatedRoot> a sh:NodeShape; sh:targetClass <urn:Sensor>;
            sh:property [sh:path <urn:monitors>; sh:node <urn:NoForbiddenFlag>].
          <urn:NoForbiddenFlag> sh:not [sh:property [sh:path <urn:flag>; sh:hasValue "forbidden"]]."""
    if getattr(request, "param", None) == "filtered_upstream":
        document["shapes"] += """
          <urn:SelectedRoot> a sh:NodeShape; sh:targetClass <urn:Shipment>;
            sh:property [sh:path <urn:monitors>; sh:node <urn:PondStatus>].
          <urn:PondStatus> sh:property [sh:path <urn:status>; sh:pattern "^ok$"]."""
    if getattr(request, "param", None) == "recursive":
        document["relations"].append({"iri": "urn:next", "label": "Next",
            "domain_iris": ["urn:Pond"], "range_iris": ["urn:Pond"]})
        document["shapes"] += """
          <urn:RecursiveRoot> a sh:NodeShape; sh:targetClass <urn:Pond>; sh:node <urn:Chain>.
          <urn:Chain> sh:property [sh:path <urn:next>; sh:node <urn:Chain>].
          <urn:Diamond> a sh:NodeShape; sh:targetClass <urn:Pond>;
            sh:and (<urn:Shared> <urn:Shared>).
          <urn:Shared> sh:property [sh:path <urn:note>; sh:pattern ".*"].
          <urn:Disabled> a sh:NodeShape; sh:targetClass <urn:Pond>;
            sh:deactivated true; sh:node <urn:Loop>.
          <urn:Loop> sh:node <urn:Loop>."""
    if getattr(request, "param", None) == "qualified_siblings":
        document["properties"].extend({"iri": iri, "label": iri,
            "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
            "owner_policy_iri": "urn:lzo:authority:ontology_owned"}
            for iri in ("urn:tag", "urn:status"))
        document["relations"].extend([
            {"iri": "urn:next", "label": "Next", "domain_iris": ["urn:Pond"], "range_iris": ["urn:Pond"]},
            {"iri": "urn:alternate", "label": "Alternate",
             "domain_iris": ["urn:Sensor"], "range_iris": ["urn:Pond"]}])
        document["shapes"] += """
          <urn:QualifiedRoot> sh:targetSubjectsOf <urn:monitors>; sh:property <urn:P>, <urn:Q>.
          <urn:P> sh:path <urn:monitors>; sh:qualifiedValueShape <urn:X>;
            sh:qualifiedMinCount 1; sh:qualifiedValueShapesDisjoint true.
          <urn:Q> sh:path <urn:alternate>; sh:qualifiedValueShape <urn:Y>; sh:qualifiedMinCount 0.
          <urn:X> sh:property [sh:path <urn:tag>; sh:hasValue "yes"].
          <urn:Y> sh:property [sh:path <urn:next>; sh:minCount 1;
            sh:node [sh:property [sh:path <urn:status>; sh:hasValue "ok"]]]."""
    if getattr(request, "param", None) in ("sibling_cycle", "conditional_sibling_cycle"):
        s.qualifying_target = uuid4()
        document["properties"].append({"iri": "urn:status", "label": "Status",
            "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
            "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
        document["relations"].append({"iri": "urn:alternate", "label": "Alternate",
            "domain_iris": ["urn:Sensor"], "range_iris": ["urn:Pond"]})
        qualifier = f'<urn:X> sh:in ("not-the-IRI-value" <urn:lzo:object:{s.qualifying_target}>).'
        if request.param == "conditional_sibling_cycle":
            qualifier = '<urn:X> sh:not [sh:property [sh:path <urn:status>; sh:hasValue "bypass"]].'
        document["shapes"] += """
          <urn:QualifiedRoot> sh:targetSubjectsOf <urn:monitors>; sh:property <urn:P>, <urn:Q>.
          <urn:P> sh:path <urn:monitors>; sh:qualifiedValueShape <urn:X>;
            sh:qualifiedMaxCount 1; sh:qualifiedValueShapesDisjoint true.
          <urn:Q> sh:path <urn:alternate>; sh:qualifiedValueShape <urn:Y>; sh:qualifiedMinCount 0.
          <urn:Y> sh:node <urn:Y>.
          """ + qualifier
    draft = s.create(document)
    assert s.publish(draft, s.approve(draft)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow:
        s.bundle_id = uow.session.execute(select(bundles.c.id)).scalar_one()
    s.login()
    s.runner = lambda: Runner(s.app, s.config)
    def assertion(resource_id, predicate="urn:note", value="synthetic note", state="accepted",
                  source_id=None, datatype=None, kind="literal", assertion_id=None):
        return {"assertion_id": str(assertion_id or uuid4()), "resource_id": str(resource_id),
            "predicate_iri": predicate, "object": {"kind": kind, "value": value,
                "datatype_iri": datatype, "language": None}, "state": state,
            "data_nature": "synthetic", "valid_from": "2026-01-01T00:00:00Z",
            "valid_to": None, "support": {"support_id": str(uuid4()), "source_id": str(source_id) if source_id else None,
                "record_key": None, "source_version": None, "snapshot": None,
                "raw_value": {"value": value}, "mapping_version_id": None,
                "evidence_refs": [], "observed_at": None}}
    s.assertion = assertion
    def create_object(class_iri="urn:Pond", assertions=None, state="active", resource_id=None):
        identifier = resource_id or uuid4()
        body = {"resource_id": str(identifier), "class_iris": [class_iri], "display_name": "Synthetic object",
            "owner_scope_id": str(s.scopes["farm_a"]), "related_scope_ids": [str(s.scopes["farm_b"])],
            "state": state, "data_nature": "synthetic",
            "assertions": assertions or [], "decision_ref": None}
        return s.client.post("/api/v1/objects", headers=s.headers(),
            json={"create": body, "bundle_id": str(s.bundle_id)})
    s.create_object = create_object
    def patch(resource_id, revision, additions=(), withdrawals=(), decision=None, headers=None):
        return s.client.patch(f"/api/v1/objects/{resource_id}", headers=headers or s.headers(revision),
            json={"bundle_id": str(s.bundle_id), "patch": {"resource_id": str(resource_id),
                "expected_revision": revision, "additions": list(additions),
                "withdraw_support_ids": list(withdrawals), "decision_ref": decision}})
    s.patch = patch
    return s


@pytest.fixture
def fact_case(object_case):
    """Test-only real PG/F06 producer proves S03's port, not an I02 implementation."""
    from copy import deepcopy
    from sqlalchemy import insert, select, update
    from lzo.auth import models
    from lzo.contracts import RevisionRef
    from lzo.governance.approvals import ApprovalService
    from lzo.governance.revisions import append_revision
    from lzo.governance.risk import ChangeDescriptor, register_policy
    from lzo.governance.models import resource_heads
    from lzo.semantic import facts
    from lzo.semantic.schemas import DecisionEvidence
    from lzo.errors import Forbidden
    s = object_case
    s.sources = [uuid4(), uuid4()]
    s.grant("admin", "viewer", scope="farm_a")
    with s.transaction() as uow:
        for source in s.sources:
            uow.session.execute(insert(models.resource_scopes).values(resource_id=source,
                resource_kind="source", security_class="data", scope_id=s.scopes["farm_a"]))
            append_revision(uow, s.actor("alice"), source, 0, {"synthetic_source": True}, "source.fixture")
        role = uuid4()
        uow.session.execute(insert(models.roles).values(id=role, name="fact-review", actions=["object.review"], revision=1))
        s.fact_review_grant = uuid4()
        uow.session.execute(insert(models.grants).values(id=s.fact_review_grant, user_id=s.users["admin"],
            role_id=role, scope_id=s.scopes["farm_a"], security_class="data", revision=1))
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1))
        uow.commit()
    from lzo.semantic.projection import register_nonsemantic
    register_nonsemantic("source.fixture", "test-v1")
    def resolver(uow, targets, command):
        creation = "create" in command
        patch = command["create"] if creation else command["patch"]
        resource_id = s.sources[0] if creation else UUID(patch["resource_id"])
        refs = {resource_id, UUID(command["bundle_id"])}
        for a in patch["assertions"] if creation else patch["additions"]:
            if a["support"]["source_id"]:
                refs.add(UUID(a["support"]["source_id"]))
        for a in facts.current_assertions(uow, resource_id) if not creation else ():
            for support in a["supports"]:
                if str(support["id"]) in patch["withdraw_support_ids"] and support["payload"]["source_id"]:
                    refs.add(UUID(support["payload"]["source_id"]))
        baseline = {str(ref): facts.head(uow, ref)["revision"] for ref in refs}
        kind = "test.object.create" if creation else "test.fact.accept"
        return ChangeDescriptor(kind=kind, target_ids=(resource_id, *sorted(refs-{resource_id}, key=str)),
            command=command, baseline=baseline, impact={"effect": command}, submit_action="edit",
            review_action="object.review", execute_action="edit", operation_key=f"test.accept:{resource_id}",
            operation_target_ids=(resource_id,))
    register_policy("test.fact.accept", resolver)
    register_policy("test.object.create", resolver)
    class EvidencePort:
        def verify(self, uow, actor, decision_ref, effect, bundle):
            service = ApprovalService(uow)
            approval = service.get(actor, decision_ref.resource_id)
            command = deepcopy(effect)
            creation = "create" in command
            command["create" if creation else "patch"]["decision_ref"] = None
            command["bundle_id"] = str(bundle.id)
            expected_kind = "test.object.create" if creation else "test.fact.accept"
            if approval.kind != expected_kind or approval.command != command:
                raise Forbidden("ACCEPTED_DECISION_INVALID", "Decision does not bind this exact effect")
            change = service.preview(actor, approval.kind, list(approval.target_ids), command)
            service.require_executable(actor, approval.id, change.change_digest, change.baseline)
            pins = tuple(facts.revision_ref(uow, ref) for ref in approval.target_ids)
            return DecisionEvidence(references=(decision_ref, *pins))
    previous = facts._decision_port
    facts.register_decision_port(EvidencePort())
    def approval_for(resource_id, revision, additions=(), withdrawals=()):
        from lzo.semantic.schemas import FactPatch
        command = {"operation": "facts.patch", "bundle_id": str(s.bundle_id),
            "patch": {"resource_id": str(resource_id), "expected_revision": revision,
                "additions": list(additions), "withdraw_support_ids": list(withdrawals), "decision_ref": None}}
        command["patch"] = FactPatch.model_validate(command["patch"]).model_dump(mode="json")
        with s.transaction() as uow:
            service = ApprovalService(uow)
            change = service.preview(s.actor("alice"), "test.fact.accept", [UUID(str(resource_id))], command)
            approval = service.submit(s.actor("alice"), change.kind, list(change.target_ids),
                change.change_digest, change.baseline, {"command": command})
            uow.commit()
        with s.transaction() as uow:
            service = ApprovalService(uow)
            service.decide(s.actor("admin"), approval.id, "approve", "Independent synthetic evidence review")
            ref = facts.revision_ref(uow, approval.id)
            uow.commit()
        return facts.json_value(ref)
    s.approval_for = approval_for
    def creation_approval(create):
        from lzo.semantic.schemas import CreateObjectInput
        create = CreateObjectInput.model_validate(create).model_dump(mode="json")
        command = {"operation": "object.create", "bundle_id": str(s.bundle_id), "create": create}
        with s.transaction() as uow:
            service = ApprovalService(uow)
            change = service.preview(s.actor("alice"), "test.object.create", [s.sources[0]], command)
            approval = service.submit(s.actor("alice"), change.kind, list(change.target_ids),
                change.change_digest, change.baseline, {"command": command})
            uow.commit()
        with s.transaction() as uow:
            ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "Independent synthetic creation")
            ref = facts.revision_ref(uow, approval.id)
            uow.commit()
        return facts.json_value(ref)
    s.creation_approval = creation_approval
    s.login()
    yield s
    facts.register_decision_port(previous)


def pytest_collection_modifyitems(config, items):
    if config.getoption("--check-persisted-rdf", default=False):
        selected = [item for item in items if item.name == "test_persistence_without_reseed"]
        config.hook.pytest_deselected(items=[item for item in items if item not in selected])
        items[:] = selected


@pytest.fixture
def rdf_repo(settings):
    from lzo.semantic.fuseki import FusekiRepository
    return FusekiRepository(settings.fuseki_url)


@pytest.fixture
def semantica_adapter():
    from lzo.semantic.semantica_adapter import SemanticaAdapter
    return SemanticaAdapter()


@pytest.fixture
def bundle():
    from lzo.contracts import ExecutionBundle
    return ExecutionBundle(uuid4(), uuid4(), None, uuid4(), uuid4(), uuid4(), "")
