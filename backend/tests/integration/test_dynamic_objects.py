"""Dynamic metadata and strict endpoints through real published snapshots."""
from uuid import uuid4
import pytest


@pytest.mark.parametrize("object_case", ["sibling_cycle"], indirect=True)
def test_unreached_sibling_cycle_is_finite_but_reached_cycle_rejects_atomically(object_case):
    s = object_case
    excluded, source = uuid4(), uuid4()
    assert s.create_object(resource_id=excluded).status_code == 201
    assert s.create_object(resource_id=s.qualifying_target).status_code == 201
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    response = s.patch(source, 1, [s.assertion(source, "urn:monitors",
        f"urn:lzo:object:{excluded}", kind="iri")])
    assert response.status_code == 200, response.text
    response = s.patch(source, 2, [s.assertion(source, "urn:monitors",
        f"urn:lzo:object:{s.qualifying_target}", kind="iri")])
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_CONTEXT_INCOMPLETE"
    view = s.client.get(f"/api/v1/objects/{source}").json()
    assert view["revision"] == 2
    assert [v["value"] for v in view["properties"]["urn:monitors"]] == [f"urn:lzo:object:{excluded}"]


@pytest.mark.parametrize("object_case", ["conditional_sibling_cycle"], indirect=True)
def test_withdrawal_reaching_sibling_cycle_removes_truth_and_marks_pending(object_case):
    s = object_case
    target, source = uuid4(), uuid4()
    status = s.assertion(target, "urn:status", "bypass")
    assert s.create_object(assertions=[status], resource_id=target).status_code == 201
    response = s.create_object("urn:Sensor", [s.assertion(source),
        s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")], resource_id=source)
    assert response.status_code == 201, response.text
    response = s.patch(target, 1, withdrawals=[status["support"]["support_id"]])
    assert response.status_code == 200, response.text
    target_view = s.client.get(f"/api/v1/objects/{target}").json()
    assert target_view["revision"] == 2 and target_view["properties"] == {}
    assert target_view["validation_state"] == "pending" and target_view["validation_report"] is None
    assert s.client.get(f"/api/v1/objects/{source}").json()["validation_state"] == "pending"


@pytest.mark.parametrize("object_case", ["qualified_siblings"], indirect=True)
def test_qualified_sibling_context_and_leaf_impact_without_sibling_path(object_case):
    s = object_case
    leaf, target, source = uuid4(), uuid4(), uuid4()
    status = s.assertion(leaf, "urn:status", "ok")
    assert s.create_object(assertions=[status], resource_id=leaf).status_code == 201
    assert s.create_object(assertions=[s.assertion(target, "urn:tag", "yes"),
        s.assertion(target, "urn:next", f"urn:lzo:object:{leaf}", kind="iri")],
        resource_id=target).status_code == 201
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    link = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    # No alternate-path value exists. Its qualifier still evaluates target/leaf.
    response = s.patch(source, 1, [link])
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "FACT_VALIDATION_FAILED"
    assert s.client.get(f"/api/v1/objects/{source}").json()["revision"] == 1
    assert s.patch(leaf, 1, withdrawals=[status["support"]["support_id"]]).status_code == 200
    assert s.patch(source, 1, [link]).status_code == 200
    # Changing only the sibling qualifier's leaf must find the upstream P root.
    response = s.patch(leaf, 2, [s.assertion(leaf, "urn:status", "ok")])
    assert response.status_code == 422, response.text
    assert s.client.get(f"/api/v1/objects/{leaf}").json()["revision"] == 2
    assert s.patch(leaf, 2, [s.assertion(leaf, "urn:status", "other")]).status_code == 200


def test_active_required_draft_partial_and_inherited_form(object_case):
    s = object_case
    assert s.create_object("urn:SpecialSensor").status_code == 422
    response = s.create_object("urn:SpecialSensor", state="draft")
    assert response.status_code == 201, response.text
    identifier = response.json()["revision"]["resource_id"]
    form = s.client.get(f"/api/v1/objects/{identifier}/form-schema")
    assert form.status_code == 200, form.text
    assert form.json()["fields"][0]["iri"] == "urn:note"
    assert form.json()["fields"][0]["required"] is True


def test_domain_range_cannot_repair_endpoint(object_case):
    s = object_case
    identifier = uuid4()
    sensor = s.create_object("urn:Sensor", [s.assertion(identifier)], resource_id=identifier)
    assert sensor.status_code == 201, sensor.text
    shipment = s.create_object("urn:Shipment")
    assert shipment.status_code == 201, shipment.text
    target = shipment.json()["revision"]["resource_id"]
    response = s.patch(identifier, 1, [s.assertion(identifier, "urn:monitors",
        f"urn:lzo:object:{target}", kind="iri")])
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "LINK_ENDPOINT_INCOMPATIBLE"
    assert s.client.get(f"/api/v1/objects/{target}").json()["class_iris"] == ["urn:Shipment"]
    assert s.client.get("/api/v1/links").json()["items"] == []
    # Target has an unrelated required Sensor field missing. Its Pond type is
    # sufficient for this relationship; it must not become another root focus.
    target_id = uuid4()
    created = s.client.post("/api/v1/objects", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(target_id),
            "class_iris": ["urn:Pond", "urn:Sensor"], "display_name": "Incomplete target",
            "owner_scope_id": str(s.scopes["farm_a"]), "related_scope_ids": [],
            "state": "draft", "data_nature": "synthetic", "assertions": [], "decision_ref": None}})
    assert created.status_code == 201, created.text
    response = s.patch(identifier, 1, [s.assertion(identifier, "urn:monitors",
        f"urn:lzo:object:{target_id}", kind="iri")])
    assert response.status_code == 200, response.text


def test_candidate_is_separate_idempotency_and_source_owned_write(object_case):
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    fact = s.assertion(obj, "urn:area", "12.50", "candidate",
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    headers = s.headers(1)
    response = s.patch(obj, 1, [fact], headers=headers)
    assert response.status_code == 200, response.text
    assert s.patch(obj, 1, [fact], headers=headers).json() == response.json()
    assert s.client.get(f"/api/v1/objects/{obj}").json()["properties"] == {}
    fact = s.assertion(obj, "urn:area", "12.50",
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    assert s.patch(obj, 2, [fact]).status_code == 403
    assert s.client.get(f"/api/v1/objects/{obj}").json()["revision"] == 2
    assert s.patch(obj, 2, headers=s.headers(1)).status_code == 422
    assert s.patch(obj, 2, headers=s.headers()).status_code == 428
    unknown = s.assertion(obj, "urn:monitors", "urn:unknown:entity", kind="iri")
    assert s.patch(obj, 2, [unknown]).status_code == 422
    # Geography metadata never grants Farm B readers access to Farm A objects.
    s.grant("nobody", "viewer", scope="farm_b")
    s.login("nobody")
    assert s.client.get(f"/api/v1/objects/{obj}").status_code == 404


def test_candidates_do_not_replace_current_validation_and_required_withdrawal_is_allowed(object_case):
    s = object_case
    obj = uuid4()
    note = s.assertion(obj)
    assert s.create_object("urn:Sensor", [note], resource_id=obj).status_code == 201
    candidate = s.assertion(obj, value="competing note", state="candidate")
    assert s.patch(obj, 1, [candidate]).status_code == 200
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["validation_state"] == "conforms"
    assert view["candidate_validation_report"]["conforms"] is False
    assert s.patch(obj, 2, withdrawals=[note["support"]["support_id"]]).status_code == 200
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["properties"] == {} and view["state"] == "active"
    assert view["validation_state"] == "needs_review"


@pytest.mark.parametrize("object_case", ["upstream"], indirect=True)
def test_candidate_report_has_independent_head_time_and_permission_freshness(object_case, monkeypatch):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    from lzo.semantic import facts
    s = object_case
    target, source = uuid4(), uuid4()
    status = s.assertion(target, "urn:status", "ok")
    end = datetime.now(timezone.utc) + timedelta(days=1)
    status["valid_to"] = end.isoformat()
    assert s.create_object(assertions=[status], resource_id=target).status_code == 201
    candidate = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", state="candidate", kind="iri")
    assert s.create_object("urn:Sensor", [s.assertion(source), candidate], resource_id=source).status_code == 201
    url = f"/api/v1/objects/{source}"
    before = s.client.get(url).json()
    assert before["validation_state"] == "conforms"
    assert before["candidate_validation_report"]["conforms"] is True
    with monkeypatch.context() as m:
        m.setattr(facts, "evaluation_time", lambda uow: end + timedelta(seconds=1))
        expired = s.client.get(url).json()
        assert expired["validation_state"] == "conforms"
        assert expired["candidate_validation_report"] is None
    assert before["next_transition_at"] is not None
    assert s.patch(target, 1, withdrawals=[status["support"]["support_id"]]).status_code == 200
    after = s.client.get(url).json()
    assert after["validation_state"] == "conforms"
    assert after["candidate_validation_report"] is None
    # Old immutable events have no candidate evidence: never borrow accepted evidence.
    with s.transaction() as uow:
        from sqlalchemy import select
        from lzo.governance.models import revisions
        from lzo.semantic import fact_validation
        row = facts.get_object_row(uow, s.actor("alice"), source)
        previous_format = dict(uow.session.execute(select(revisions.c.payload).where(
            revisions.c.resource_id == source, revisions.c.revision == 1)).scalar_one())
        previous_format.pop("candidate_validation_evidence")
        assert fact_validation.is_fresh(uow, s.actor("alice"), row, previous_format) is True
        assert fact_validation.is_fresh(uow, s.actor("alice"), row, previous_format, candidate=True) is False
    # Full content dependencies are unchanged: hiding required candidate context
    # must not expose a report through a downgraded object read.
    s.ids_by_name["candidate_source"] = UUID(str(source))
    s.grant("nobody", "viewer", resource="candidate_source")
    s.login("nobody")
    assert s.client.get(url).status_code == 404


@pytest.mark.parametrize("object_case", ["upstream"], indirect=True)
def test_existing_subject_validation_retains_focused_aggregate_scope(object_case):
    s = object_case
    unrelated = s.create_object("urn:Sensor", state="draft")
    assert unrelated.status_code == 201
    first, second = uuid4(), uuid4()
    upstream = []
    for identifier in (first, second):
        assert s.create_object(assertions=[s.assertion(identifier, "urn:status", "ok")],
            resource_id=identifier).status_code == 201
        source = uuid4()
        assert s.create_object("urn:Sensor", [s.assertion(source),
            s.assertion(source, "urn:monitors", f"urn:lzo:object:{identifier}", kind="iri")],
            resource_id=source).status_code == 201
        upstream.append(source)
    values = [s.assertion(first, "urn:status", "ok"), s.assertion(second, "urn:status", "ok")]
    response = s.client.post("/api/v1/validation", headers=s.headers(),
        json={"bundle_id": str(s.bundle_id), "facts": values})
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["conforms"] is True
    assert report["invocation_scope"] == "focused"
    assert set(report["focus_iris"]) == {f"urn:lzo:object:{identifier}"
                                       for identifier in (first, second, *upstream)}
    assert report["root_selection"] == {f"urn:lzo:object:{identifier}": ["urn:UpstreamRoot"]
                                       for identifier in upstream}
    with s.transaction() as uow:
        from sqlalchemy import select
        from lzo.ontology.models import bundles
        digest = uow.session.execute(select(bundles.c.shapes_digest).where(bundles.c.id == s.bundle_id)).scalar_one()
    assert report["original_shapes_digest"] == digest and report["evaluation_time"]
    # Empty input is a focused empty check, never a dataset conformance claim.
    empty = s.client.post("/api/v1/validation", headers=s.headers(),
        json={"bundle_id": str(s.bundle_id), "facts": []})
    assert empty.status_code == 200, empty.text
    assert empty.json()["invocation_scope"] == "focused"
    assert empty.json()["focus_iris"] == []
    assert empty.json()["original_shapes_digest"] == report["original_shapes_digest"]
    assert empty.json()["evaluation_time"]


def test_deactivation_blocks_future_inbound_without_disclosing_hidden_subject(fact_case):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    s = fact_case
    target = s.create_object().json()["revision"]["resource_id"]
    source = uuid4()
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    link = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    link["valid_from"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    response = s.client.post("/api/v1/links", headers=s.headers(1), json={
        "source_id": str(source), "target_id": target, "predicate_iri": "urn:monitors",
        "assertion_id": link["assertion_id"], "state": "accepted", "data_nature": "synthetic",
        "valid_from": link["valid_from"], "support": link["support"], "bundle_id": str(s.bundle_id)})
    assert response.status_code == 201, response.text
    url = f"/api/v1/objects/{target}/deactivate"
    response = s.client.post(url, headers=s.headers(1), json={"reason": "Blocked by future link"})
    assert response.status_code == 409 and response.json()["code"] == "OBJECT_HAS_INBOUND_RELATIONS"
    s.ids_by_name["retirement_target"] = UUID(target)
    s.grant("nobody", "viewer", resource="retirement_target")
    s.grant("nobody", "editor", resource="retirement_target")
    s.login("nobody")
    response = s.client.post(url, headers=s.headers(1), json={"reason": "No hidden impact"})
    assert response.status_code == 404 and response.json()["details"] is None


def test_deactivation_requires_independent_approval_and_retains_identity(fact_case):
    s = fact_case
    response = s.create_object()
    assert response.status_code == 201, response.text
    obj = response.json()["revision"]["resource_id"]
    command = {"operation": "object.deactivate", "resource_id": obj,
               "expected_revision": 1, "reason": "Synthetic lifecycle test"}
    preview = s.client.post("/api/v1/approvals/preview", headers=s.headers(),
        json={"kind": "object.deactivate", "target_ids": [obj], "command": command})
    assert preview.status_code == 200, preview.text
    data = preview.json()
    approval = s.client.post("/api/v1/approvals", headers=s.headers(), json={k: data[k]
        for k in ("kind", "target_ids", "command", "change_digest", "baseline")})
    assert approval.status_code == 201, approval.text
    approval_id = approval.json()["id"]
    url = f"/api/v1/objects/{obj}/deactivate"
    body = {"reason": command["reason"], "approval_id": approval_id}
    assert s.client.post(url, headers=s.headers(1), json=body).status_code == 409
    s.login("admin")
    reviewed = s.client.post(f"/api/v1/approvals/{approval_id}/decisions", headers=s.headers(1),
        json={"decision": "approve", "comment": "Independent retirement"})
    assert reviewed.status_code == 200, reviewed.text
    s.login()
    response = s.client.post(url, headers=s.headers(1), json=body)
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["state"] == "inactive" and view["iri"] == f"urn:lzo:object:{obj}"
    assert view["owner_scope_id"] == str(s.scopes["farm_a"])
    assert s.client.post(url, headers=s.headers(1), json=body).status_code == 409
    source = uuid4()
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    response = s.patch(source, 1, [s.assertion(source, "urn:monitors",
        f"urn:lzo:object:{obj}", kind="iri")])
    assert response.status_code == 409 and response.json()["code"] == "LINK_TARGET_INACTIVE"


@pytest.mark.parametrize("object_case", ["nested"], indirect=True)
def test_nested_shape_context_is_complete_and_hidden_evidence_fails_closed(fact_case):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    s = fact_case
    target = s.create_object().json()["revision"]["resource_id"]
    source = uuid4()
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    link = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    assert s.patch(source, 1, [link]).status_code == 422
    area = s.assertion(target, "urn:area", "12.50", source_id=s.sources[0],
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    area["valid_to"] = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    decision = s.approval_for(target, 1, [area])
    response = s.patch(target, 1, [area], decision=decision)
    assert response.status_code == 200, response.text
    response = s.patch(source, 1, [link])
    assert response.status_code == 200, response.text
    future = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    future["valid_from"] = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()
    response = s.patch(source, 2, [future])
    assert response.status_code == 422, response.text
    s.ids_by_name["nested_target"] = UUID(target)
    s.grant("nobody", "viewer", resource="nested_target")
    s.login("nobody")
    proposed = uuid4()
    response = s.client.post("/api/v1/validation", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(proposed),
            "class_iris": ["urn:Sensor"], "display_name": "Validation only",
            "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "synthetic",
            "assertions": [s.assertion(proposed), s.assertion(proposed, "urn:monitors",
                f"urn:lzo:object:{target}", kind="iri")]}})
    assert response.status_code == 404 and response.json()["details"] is None


def test_imported_type_provenance_is_not_declassified(fact_case):
    s = fact_case
    target = uuid4()
    area = s.assertion(target, "urn:area", "12.50", source_id=s.sources[0],
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    create = {"resource_id": str(target), "class_iris": ["urn:Pond"], "display_name": "Synthetic import",
        "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "imported_unverified",
        "assertions": [area], "decision_ref": None}
    create["decision_ref"] = s.creation_approval(create)
    response = s.client.post("/api/v1/objects", headers=s.headers(),
        json={"bundle_id": str(s.bundle_id), "create": create})
    assert response.status_code == 201, response.text
    s.ids_by_name["imported_target"] = target
    s.grant("nobody", "viewer", resource="imported_target")
    s.login("nobody")
    proposed = uuid4()
    response = s.client.post("/api/v1/validation", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(proposed),
            "class_iris": ["urn:Sensor"], "display_name": "Validation only",
            "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "synthetic",
            "assertions": [s.assertion(proposed), s.assertion(proposed, "urn:monitors",
                f"urn:lzo:object:{target}", kind="iri")]}})
    assert response.status_code == 404
    assert s.client.get(f"/api/v1/objects/{target}").status_code == 404


def test_new_published_fields_use_current_form_and_old_bundle_cannot_write(object_case):
    from sqlalchemy import select
    from lzo.ontology.models import catalog, releases
    s = object_case
    s.grant("admin", "viewer", scope="farm_a")
    s.login()
    obj = s.create_object().json()["revision"]["resource_id"]
    old_bundle = s.bundle_id
    document = s.client.get("/api/v1/schema/releases").json()["items"][0]["document"]
    document["properties"].append({"iri": "urn:newField", "label": "New field",
        "datatype_iri": "http://www.w3.org/2001/XMLSchema#string",
        "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
    document["shapes"] += """
       <urn:NewPondShape> a sh:NodeShape; sh:targetClass <urn:Pond>;
          sh:property [sh:path <urn:newField>]."""
    document["ui_metadata"]["migration_plan"] = {
        "description": "Add optional field; no current value requires migration",
        "validation": "Validate current authoritative data with published SHACL",
        "rollback": "Retain the previous immutable release and values"}
    draft = s.create(document)
    published = s.publish(draft, s.approve(draft))
    assert published.status_code == 202, published.text
    for _ in range(6):
        s.runner().run_once()
    with s.transaction() as uow:
        current = uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
        current_bundle = uow.session.execute(select(releases.c.bundle_id).where(releases.c.id == current)).scalar_one()
    assert current_bundle != old_bundle
    form = s.client.get(f"/api/v1/objects/{obj}/form-schema")
    assert form.status_code == 200, form.text
    assert [field["iri"] for field in form.json()["fields"]] == ["urn:newField"]
    assert s.patch(obj, 1, [s.assertion(obj, "urn:newField", "new")]).status_code == 409
    s.bundle_id = current_bundle
    response = s.patch(obj, 1, [s.assertion(obj, "urn:newField", "new")])
    assert response.status_code == 200, response.text


@pytest.mark.parametrize("object_case", ["target_objects"], indirect=True)
def test_objects_of_targets_use_proposed_overlay_and_authorized_incoming_witnesses(object_case):
    from datetime import datetime, timedelta, timezone
    from uuid import UUID
    s = object_case
    target = s.create_object().json()["revision"]["resource_id"]
    source = uuid4()
    assert s.create_object("urn:Sensor", [s.assertion(source)], resource_id=source).status_code == 201
    link = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    candidate = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri", state="candidate")
    assert s.patch(source, 1, [candidate]).status_code == 200
    view = s.client.get(f"/api/v1/objects/{target}").json()
    assert view["validation_state"] == "conforms"
    expired = s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")
    expired["valid_to"] = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    assert s.patch(source, 2, [expired]).status_code == 200
    assert s.client.get(f"/api/v1/objects/{target}").json()["validation_state"] == "conforms"
    response = s.patch(source, 3, [link])
    assert response.status_code == 422, response.text
    status = s.assertion(target, "urn:status", "ok")
    assert s.patch(target, 1, [status]).status_code == 200
    assert s.patch(source, 3, [link]).status_code == 200
    # A previously absent incoming target predicate now invalidates the old report.
    view = s.client.get(f"/api/v1/objects/{target}").json()
    assert view["validation_state"] == "pending" and view["validation_report"] is None
    assert s.patch(target, 2, [s.assertion(target, "urn:status", "second")]).status_code == 422
    s.ids_by_name["incoming_target"] = UUID(target)
    s.grant("nobody", "editor", resource="incoming_target")
    s.login("nobody")
    response = s.patch(target, 2, [s.assertion(target, "urn:status", "hidden witness")])
    assert response.status_code == 404 and response.json()["details"] is None
    s.login()
    assert s.patch(source, 4, withdrawals=[link["support"]["support_id"]]).status_code == 200
    assert s.patch(target, 2, [s.assertion(target, "urn:status", "second")]).status_code == 200
    response = s.patch(source, 5, [s.assertion(source, "urn:monitors", "literal")])
    assert response.status_code == 422 and response.json()["code"] == "UNSUPPORTED_AFFECTED_TARGET"
    self_id = uuid4()
    response = s.client.post("/api/v1/objects", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(self_id),
            "class_iris": ["urn:Sensor", "urn:Pond"], "display_name": "Self-targeted object",
            "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "synthetic",
            "assertions": [s.assertion(self_id), s.assertion(self_id, "urn:status", "ok"),
                s.assertion(self_id, "urn:monitors", f"urn:lzo:object:{self_id}", kind="iri")]}})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("object_case", ["upstream"], indirect=True)
def test_nested_upstream_impact_is_precise_and_hidden_upstream_cannot_veto_withdrawal(object_case, monkeypatch):
    from uuid import UUID
    s = object_case
    target = uuid4()
    status = s.assertion(target, "urn:status", "ok")
    assert s.create_object(assertions=[status], resource_id=target).status_code == 201
    sources = []
    for state in ("active", "draft"):
        source = uuid4()
        values = [s.assertion(source, "urn:monitors", f"urn:lzo:object:{target}", kind="iri")]
        if state == "active":
            values.append(s.assertion(source))
        assert s.create_object("urn:Sensor", values, state=state, resource_id=source).status_code == 201
        sources.append(source)
    s.ids_by_name["withdraw_target"] = target
    s.grant("nobody", "editor", resource="withdraw_target")
    witness_grants = []
    for number, source in enumerate(sources):
        name = f"upstream_{number}"
        s.ids_by_name[name] = source
        witness_grants.append(s.grant("nobody", "viewer", resource=name))
    s.login("nobody")
    # Reverse impact must not activate the draft source's unrelated SensorShape.
    second = s.assertion(target, "urn:status", "ok")
    original_headers = s.headers(1)
    assert s.patch(target, 1, [second], headers=original_headers).status_code == 200
    s.login()
    # This creates a nested success under sh:not. No old violation identifies
    # this dependency, and the status-only shape is unaffected by the new flag.
    response = s.patch(target, 2, [s.assertion(target, "urn:flag", "forbidden")])
    assert response.status_code == 422, response.text
    response = s.patch(target, 2, [s.assertion(target, "urn:status", "bad")])
    assert response.status_code == 422, response.text
    from lzo.semantic import fact_validation
    with monkeypatch.context() as limited:
        limited.setattr(fact_validation, "MAX_DEPTH", 0)
        response = s.patch(target, 2, [s.assertion(target, "urn:status", "ok")])
        assert response.status_code == 422 and response.json()["code"] == "VALIDATION_CONTEXT_BUDGET"
    for grant in witness_grants:
        s.revoke(grant)
    s.login("nobody")
    view = s.client.get(f"/api/v1/objects/{target}").json()
    assert view["validation_state"] == "pending" and view["validation_report"] is None
    response = s.patch(target, 2, [s.assertion(target, "urn:status", "ok")])
    assert response.status_code == 404 and response.json()["details"] is None
    replay = {**s.headers(1), "Idempotency-Key": original_headers["Idempotency-Key"]}
    response = s.patch(target, 1, [second], headers=replay)
    assert response.status_code == 404 and response.json()["details"] is None
    assert s.client.get(f"/api/v1/objects/{target}").json()["revision"] == 2
    response = s.patch(target, 2, withdrawals=[status["support"]["support_id"], second["support"]["support_id"]])
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{target}").json()
    assert view["properties"] == {} and view["validation_report"] is None
    from lzo.governance.history import HistoryService
    from lzo.errors import ApiError
    with s.transaction() as uow:
        with pytest.raises(ApiError, match="NOT_FOUND"):
            HistoryService(uow).get(s.actor("nobody"), target, 2)
        current = HistoryService(uow).get(s.actor("nobody"), target, 3)
        assert not {str(value) for value in sources} & {
            pin["resource_id"] for pin in current.payload["dependency_revisions"]}
    s.login()
    view = s.client.get(f"/api/v1/objects/{sources[0]}").json()
    assert view["validation_state"] == "pending" and view["validation_report"] is None


@pytest.mark.parametrize("object_case", ["filtered_upstream"], indirect=True)
def test_hidden_upstream_candidates_require_authorization_only_when_root_applies(object_case):
    s = object_case
    target = uuid4()
    assert s.create_object(resource_id=target).status_code == 201
    source = uuid4()
    values = [s.assertion(source), s.assertion(source, "urn:monitors",
        f"urn:lzo:object:{target}", kind="iri")]
    assert s.create_object("urn:Sensor", values, resource_id=source).status_code == 201
    s.ids_by_name["visible_leaf"] = target
    s.grant("nobody", "editor", resource="visible_leaf")
    s.login("nobody")
    response = s.patch(target, 1, [s.assertion(target, "urn:status", "ok")])
    assert response.status_code == 200, response.text
    s.login()
    selected = uuid4()
    response = s.client.post("/api/v1/objects", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(selected),
            "class_iris": ["urn:Sensor", "urn:Shipment"], "display_name": "Selected hidden predecessor",
            "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "synthetic",
            "assertions": [s.assertion(selected), s.assertion(selected, "urn:monitors",
                f"urn:lzo:object:{target}", kind="iri")]}})
    assert response.status_code == 201, response.text
    s.login("nobody")
    response = s.patch(target, 2, [s.assertion(target, "urn:status", "ok")])
    assert response.status_code == 404 and response.json()["details"] is None
    assert s.client.get(f"/api/v1/objects/{target}").json()["revision"] == 2


@pytest.mark.parametrize("object_case", ["recursive"], indirect=True)
def test_recursive_context_rejects_active_cycle_but_allows_finite_shared_and_deactivated_shapes(object_case):
    s = object_case
    identifiers = [uuid4() for _ in range(3)]
    for identifier in identifiers:
        response = s.create_object(resource_id=identifier)
        assert response.status_code == 201, response.text
    first, second, third = identifiers
    for source, target in ((second, third), (first, second)):
        response = s.patch(source, 1, [s.assertion(source, "urn:next",
            f"urn:lzo:object:{target}", kind="iri")])
        assert response.status_code == 200, response.text
    response = s.patch(third, 1, [s.assertion(third, "urn:next",
        f"urn:lzo:object:{first}", kind="iri")])
    assert response.status_code == 422 and response.json()["code"] == "VALIDATION_CONTEXT_INCOMPLETE"
    assert s.client.get(f"/api/v1/objects/{third}").json()["revision"] == 1
    # An RDF self-link with only a plain class check has no recursive evaluation.
    source = uuid4()
    response = s.client.post("/api/v1/objects", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(source),
            "class_iris": ["urn:Sensor", "urn:Pond"], "display_name": "Ordinary self link",
            "owner_scope_id": str(s.scopes["farm_a"]), "state": "active", "data_nature": "synthetic",
            "assertions": [s.assertion(source), s.assertion(source, "urn:monitors",
                f"urn:lzo:object:{source}", kind="iri")]}})
    assert response.status_code == 201, response.text
