"""Independent support IDs, exact RDF identity and immutable object identity."""


def test_executor_incompleteness_allows_removal_but_not_addition_or_programming_errors(object_case, monkeypatch):
    import pytest
    from uuid import uuid4
    from lzo.semantic import files, semantica_adapter
    s = object_case
    first, second = uuid4(), uuid4()
    notes = {}
    for identifier in (first, second):
        notes[identifier] = s.assertion(identifier)
        assert s.create_object(assertions=[notes[identifier],
            s.assertion(identifier, value="candidate-only", state="candidate")],
            resource_id=identifier).status_code == 201
    execute = semantica_adapter.bounded_json
    def incomplete(module, payload, **kwargs):
        # Exercise the actual input-budget classification, without a heavy
        # subprocess timeout and without replacing the genuine validator.
        with monkeypatch.context() as m:
            m.setattr(files, "MAX_BYTES", 1)
            return execute(module, payload, **kwargs)
    monkeypatch.setattr(semantica_adapter, "bounded_json", incomplete)
    response = s.patch(first, 1, withdrawals=[notes[first]["support"]["support_id"]])
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{first}").json()
    assert view["properties"] == {} and view["validation_state"] == "pending"
    assert view["validation_report"] is None and view["candidate_validation_report"] is None
    response = s.patch(first, 2, [s.assertion(first)])
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "VALIDATION_EXECUTION_INCOMPLETE"
    assert s.client.get(f"/api/v1/objects/{first}").json()["revision"] == 2
    def candidate_incomplete(module, payload, **kwargs):
        return (incomplete(module, payload, **kwargs) if "candidate-only" in payload["data"]
                else execute(module, payload, **kwargs))
    monkeypatch.setattr(semantica_adapter, "bounded_json", candidate_incomplete)
    response = s.patch(second, 1, withdrawals=[notes[second]["support"]["support_id"]])
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{second}").json()
    assert view["properties"] == {} and view["validation_state"] == "conforms"
    assert view["candidate_validation_report"] is None
    monkeypatch.setattr(semantica_adapter, "bounded_json", execute)
    assert s.patch(second, 2, [notes[second] | {
        "assertion_id": str(uuid4()), "support": notes[second]["support"] | {"support_id": str(uuid4())}}]).status_code == 200
    current = s.client.get(f"/api/v1/objects/{second}/assertions").json()
    support_id = next(a["supports"][0]["support_id"] for a in current["items"] if a["state"] == "accepted")
    def programming_error(*args, **kwargs):
        raise RuntimeError("unexpected executor programming failure")
    monkeypatch.setattr(semantica_adapter, "bounded_json", programming_error)
    with pytest.raises(RuntimeError, match="unexpected executor programming"):
        s.patch(second, 3, withdrawals=[support_id])
    view = s.client.get(f"/api/v1/objects/{second}").json()
    assert view["revision"] == 3 and view["properties"]["urn:note"]


def test_due_materialization_survives_typed_executor_incompleteness(object_case, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import time
    from uuid import uuid4
    from lzo.semantic import files, semantica_adapter
    s = object_case
    identifier = uuid4()
    note = s.assertion(identifier)
    end = datetime.now(timezone.utc) + timedelta(seconds=15)
    note["valid_to"] = end.isoformat()
    assert s.create_object(assertions=[note, s.assertion(identifier, state="candidate")],
        resource_id=identifier).status_code == 201
    execute = semantica_adapter.bounded_json
    def incomplete(module, payload, **kwargs):
        with monkeypatch.context() as m:
            m.setattr(files, "MAX_BYTES", 1)
            return execute(module, payload, **kwargs)
    monkeypatch.setattr(semantica_adapter, "bounded_json", incomplete)
    while datetime.now(timezone.utc) < end:
        time.sleep(.2)
    for _ in range(8):
        if not s.runner().run_once():
            break
        view = s.client.get(f"/api/v1/objects/{identifier}").json()
        if view["revision"] == 2:
            break
    view = s.client.get(f"/api/v1/objects/{identifier}").json()
    assert view["revision"] == 2, view
    assert view["properties"] == {} and view["validation_state"] == "pending"
    assert view["candidate_validation_report"] is None
    with s.transaction() as uow:
        from sqlalchemy import select
        from lzo.governance.models import revisions
        from lzo.worker.models import jobs
        payload = uow.session.execute(select(revisions.c.payload).where(
            revisions.c.resource_id == identifier, revisions.c.revision == 2)).scalar_one()
        assert not any(t["predicate_iri"] == "urn:note" for t in payload["graphs"]["accepted"])
        assert uow.session.execute(select(jobs.c.status).where(
            jobs.c.kind == "facts.validity", jobs.c.resource_id == identifier)).scalar_one() == "succeeded"


def test_delayed_enqueue_cannot_be_claimed_early(jobs):
    from datetime import datetime, timedelta, timezone
    from lzo.worker.service import enqueue_at
    scheduled = datetime.now(timezone.utc) + timedelta(minutes=3)
    with jobs.transaction() as uow:
        enqueue_at(uow, jobs.actor(), jobs.kind, jobs.payload, None, scheduled)
        uow.commit()
    assert jobs.runner().run_once() is False
    assert jobs.effects() == []


def test_withdraw_one_support_preserves_other_and_platform_object(object_case):
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    first = s.assertion(obj)
    second = s.assertion(obj, assertion_id=first["assertion_id"])
    assert s.patch(obj, 1, [first, second]).status_code == 200
    response = s.patch(obj, 2, withdrawals=[first["support"]["support_id"]])
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["properties"]["urn:note"][0]["value"] == "synthetic note"
    response = s.patch(obj, 3, withdrawals=[second["support"]["support_id"]])
    assert response.status_code == 200, response.text
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["properties"] == {} and view["data_nature"] == "synthetic"
    assert view["owner_scope_id"] == str(s.scopes["farm_a"])
    assert obj not in [str(x) for x in s.ids("nobody", "view")]


def test_future_and_expired_facts_use_real_delayed_worker(object_case):
    from datetime import datetime, timedelta, timezone
    import time
    from sqlalchemy import select
    from lzo.worker.models import jobs
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    fact = s.assertion(obj)
    start = datetime.now(timezone.utc) + timedelta(seconds=15)
    fact["valid_from"] = start.isoformat()
    fact["valid_to"] = (start + timedelta(seconds=60)).isoformat()
    response = s.patch(obj, 1, [fact])
    assert response.status_code == 200, response.text
    assert s.client.get(f"/api/v1/objects/{obj}").json()["properties"] == {}
    with s.transaction() as uow:
        scheduled = uow.session.execute(select(jobs).where(
            jobs.c.kind == "facts.validity", jobs.c.resource_id == obj)).mappings().all()
        assert len(scheduled) == 1
        assert scheduled[0]["available_at"] == start
    while datetime.now(timezone.utc) < start:
        time.sleep(.2)
    assert s.client.get(f"/api/v1/objects/{obj}").json()["properties"]["urn:note"]
    for _ in range(6):
        assert s.runner().run_once()
        if s.client.get(f"/api/v1/objects/{obj}").json()["revision"] == 3:
            break
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["revision"] == 3
    # A later object revision makes the already scheduled expiry job stale.
    assert s.patch(obj, 3).status_code == 200
    while datetime.now(timezone.utc) < start + timedelta(seconds=60):
        time.sleep(.2)
    assert s.client.get(f"/api/v1/objects/{obj}").json()["properties"] == {}
    for _ in range(10):
        if not s.runner().run_once():
            break
    assert s.client.get(f"/api/v1/objects/{obj}").json()["revision"] == 5
    with s.transaction() as uow:
        stale = uow.session.execute(select(jobs.c.cursor).where(jobs.c.kind == "facts.validity",
            jobs.c.resource_id == obj)).scalars().all()
        assert any(item.get("superseded") is True for item in stale)


def test_external_multiple_supports_require_real_independent_evidence(fact_case):
    from uuid import UUID, uuid4
    from lzo.semantic import facts
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    s = fact_case
    identifier = uuid4()
    note = s.assertion(identifier)
    created = s.create_object(assertions=[note], resource_id=identifier)
    assert created.status_code == 201, created.text
    obj = created.json()["revision"]["resource_id"]
    first = s.assertion(obj, "urn:area", "12.50", source_id=s.sources[0],
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    second = s.assertion(obj, "urn:area", "12.50", source_id=s.sources[1],
        datatype="http://www.w3.org/2001/XMLSchema#decimal", assertion_id=first["assertion_id"])
    assert s.patch(obj, 1, [first, second]).status_code == 403
    random = {"resource_id": str(uuid4()), "revision": 1, "event_id": str(uuid4())}
    assert s.patch(obj, 1, [first, second], decision=random).status_code == 404
    decision = s.approval_for(obj, 1, [first, second])
    adapter = facts._decision_port
    facts.register_decision_port(None)
    assert s.patch(obj, 1, [first, second], decision=decision).status_code == 403
    facts.register_decision_port(adapter)
    changed = {**second, "object": {**second["object"], "value": "12.500"}}
    assert s.patch(obj, 1, [first, changed], decision=decision).status_code == 403
    response = s.patch(obj, 1, [first, second], decision=decision)
    assert response.status_code == 200, response.text
    for _ in range(4):
        s.runner().run_once()
    values = FusekiRepository(s.config.fuseki_url).read_graph(graph_iri(UUID(obj), "accepted"))
    assert [(t.object.value, t.object.datatype_iri) for t in values if t.predicate_iri == "urn:area"] == [
        ("12.50", "http://www.w3.org/2001/XMLSchema#decimal")]
    # Type-only validation does not read the target's restricted field evidence.
    s.ids_by_name["type_target"] = UUID(obj)
    s.grant("nobody", "viewer", resource="type_target")
    s.login("nobody")
    assert s.client.get(f"/api/v1/objects/{obj}").status_code == 404
    proposed = uuid4()
    response = s.client.post("/api/v1/validation", headers=s.headers(), json={
        "bundle_id": str(s.bundle_id), "create": {"resource_id": str(proposed),
            "class_iris": ["urn:Sensor"], "display_name": "Validation only",
            "owner_scope_id": str(s.scopes["farm_a"]), "related_scope_ids": [],
            "state": "active", "data_nature": "synthetic", "decision_ref": None,
            "assertions": [s.assertion(proposed), s.assertion(proposed, "urn:monitors",
                f"urn:lzo:object:{obj}", kind="iri")]}})
    assert response.status_code == 200 and response.json()["conforms"], response.text
    s.login()
    for revision, support in ((2, first), (3, second)):
        withdrawn = [support["support"]["support_id"]]
        decision = s.approval_for(obj, revision, withdrawals=withdrawn)
        response = s.patch(obj, revision, withdrawals=withdrawn, decision=decision)
        assert response.status_code == 200, response.text
        view = s.client.get(f"/api/v1/objects/{obj}").json()
        assert ("urn:area" in view["properties"]) == (revision == 2)
        assert view["properties"]["urn:note"][0]["value"] == "synthetic note"
        assert view["data_nature"] == "synthetic"
    for _ in range(4):
        s.runner().run_once()
    values = FusekiRepository(s.config.fuseki_url).read_graph(graph_iri(UUID(obj), "accepted"))
    assert not any(t.predicate_iri == "urn:area" for t in values)
    assert any(t.predicate_iri == "urn:note" for t in values)
    third = s.assertion(obj, "urn:area", "12.500", source_id=s.sources[0],
        datatype="http://www.w3.org/2001/XMLSchema#decimal")
    decision = s.approval_for(obj, 4, [third])
    from lzo.governance.revisions import append_revision
    with s.transaction() as uow:
        append_revision(uow, s.actor("alice"), s.sources[0], 1,
                        {"synthetic_source": "new evidence"}, "source.fixture")
        uow.commit()
    assert s.patch(obj, 4, [third], decision=decision).status_code == 409
    decision = s.approval_for(obj, 4, [third])
    from sqlalchemy import update
    from lzo.auth.models import grants, authorization_state
    with s.transaction() as uow:
        uow.session.execute(update(grants).where(grants.c.id == s.fact_review_grant).values(active=False))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1))
        uow.commit()
    s.login()
    assert s.patch(obj, 4, [third], decision=decision).status_code == 403


def test_expiry_read_truth_survives_original_actor_edit_revocation(object_case):
    from datetime import datetime, timedelta, timezone
    import time
    from sqlalchemy import select
    from lzo.worker.models import jobs
    s = object_case
    obj = s.create_object().json()["revision"]["resource_id"]
    fact = s.assertion(obj)
    end = datetime.now(timezone.utc) + timedelta(seconds=30)
    fact["valid_to"] = end.isoformat()
    assert s.patch(obj, 1, [fact]).status_code == 200
    s.grant("alice", "viewer", scope="farm_a")
    s.revoke(s.object_editor_grant)
    s.login()
    while datetime.now(timezone.utc) < end:
        time.sleep(.2)
    view = s.client.get(f"/api/v1/objects/{obj}").json()
    assert view["properties"] == {} and view["validation_state"] == "pending"
    for _ in range(6):
        if not s.runner().run_once():
            break
    with s.transaction() as uow:
        row = uow.session.execute(select(jobs).where(jobs.c.kind == "facts.validity",
            jobs.c.resource_id == obj)).mappings().one()
        assert row["status"] == "failed"
    assert s.client.get(f"/api/v1/objects/{obj}").json()["revision"] == 2
