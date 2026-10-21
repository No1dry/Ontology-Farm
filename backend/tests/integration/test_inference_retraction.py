"""S04 acceptance uses the real S02/S03/worker fixtures and durable services."""
from importlib.util import find_spec
from uuid import UUID, uuid4

import pytest


def service():
    assert find_spec("lzo.semantic.proofs"), "S04 proof evidence service is missing"
    from lzo.semantic import proofs
    return proofs


def test_type_bridge_preserves_creation_and_observed_lifecycle_separately(object_case):
    s = object_case
    identifier = uuid4()
    response = s.create_object("urn:SpecialSensor", [s.assertion(identifier)], resource_id=identifier)
    assert response.status_code == 201, response.text
    creation = response.json()["revision"]
    assert s.patch(identifier, 1, [s.assertion(identifier, value="new note")],
                   withdrawals=[]).status_code == 422  # actual maxCount remains enforced
    assert s.patch(identifier, 1).status_code == 200
    from lzo.auth.permissions import permission_context
    with s.transaction() as uow, permission_context(uow):
        value = service().get_type_declaration_evidence(uow, s.actor("alice"), identifier)
        assert value.declaration_revision.event_id == UUID(creation["event_id"])
        assert value.declaration_revision.revision == 1
        assert value.lifecycle_revision.revision == 2
        assert value.class_iris == ("urn:SpecialSensor",)
        assert value.data_nature == "synthetic"
        assert value.declaration_bundle_id == s.bundle_id
        assert value.dependency_refs
        assert value.lifecycle_state == "active"


def test_type_bridge_denies_current_unreadable_object(object_case):
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    s = object_case
    identifier = UUID(s.create_object().json()["revision"]["resource_id"])
    with s.transaction() as uow, permission_context(uow), pytest.raises(ApiError):
        service().get_type_declaration_evidence(uow, s.actor("nobody"), identifier)


def test_profile_activation_is_independently_approved_and_replaces_common_bundle(object_case):
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.errors import ApiError
    from lzo.semantic import inference
    from lzo.ontology.releases import get_execution_bundle
    s = object_case
    assert hasattr(inference, "activate_inference_profile"), "S04 approved profile activation is missing"
    with s.transaction() as uow, permission_context(uow):
        old = get_execution_bundle(s.actor("alice"), s.bundle_id)
        from lzo.semantic.inference_jobs import _profile
        with pytest.raises(ApiError) as unsupported:
            _profile(uow, s.actor("alice"), old)
        assert unsupported.value.code == "INFERENCE_PROFILE_UNSUPPORTED"
        change = inference.preview_inference_profile(uow, s.actor("alice"))
        approval = ApprovalService(uow).submit(s.actor("alice"), change.kind,
            list(change.target_ids), change.change_digest, change.baseline, {"command": change.command})
        uow.commit()
    with s.transaction() as uow, permission_context(uow), pytest.raises(ApiError):
        ApprovalService(uow).decide(s.actor("alice"), approval.id, "approve", "self")
    with s.transaction() as uow, permission_context(uow):
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "independent")
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        new = inference.activate_inference_profile(uow, s.actor("alice"), approval.id)
        assert new.id != old.id and new.schema_release_id == old.schema_release_id
        assert new.inference_version_id != old.inference_version_id
        assert get_execution_bundle(s.actor("alice"), old.id) == old
        uow.commit()
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic.facts import effective_bundle
    with s.transaction() as uow, permission_context(uow):
        assert current_execution_bundle(uow, s.actor("alice")) == new
        with pytest.raises(ApiError):
            effective_bundle(uow, s.actor("alice"), old)
        effective_bundle(uow, s.actor("alice"), new)
        uow.ontology_settings = s.config
        with pytest.raises(ApiError) as stale:
            inference.activate_inference_profile(uow, s.actor("alice"), approval.id)
        assert stale.value.code == "APPROVAL_STALE"


@pytest.fixture
def inference_case(schema_case, monkeypatch):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic import inference
    from lzo.semantic.projection import register_nonsemantic, ProjectionService
    from lzo.worker.runner import Runner
    s = schema_case
    s.worker_errors = []
    original_finish = Runner.finish
    def observed_finish(runner, ctx, error=None):
        if error is not None:
            import traceback
            s.worker_errors.append("".join(traceback.format_exception(error)))
        return original_finish(runner, ctx, error)
    monkeypatch.setattr(Runner, "finish", observed_finish)
    s.data_grant = s.grant("alice", "editor", scope="farm_a")
    s.grant("admin", "viewer", scope="farm_a")
    for event in ("role.fixture_installed", "grant.fixture_installed", "object.fixture_created"):
        register_nonsemantic(event, "test-inference-fixture-v1")
    s.config = s.config.model_copy(update={"job_max_attempts": 1})
    s.client.app.state.settings = s.config
    s.document = {"classes": [{"iri": "urn:Node", "label": "Node"},
        {"iri": "urn:Special", "label": "Special", "parents": ["urn:Node"]}],
        "relations": [{"iri": "urn:connects", "label": "Connects",
            "domain_iris": ["urn:Node"], "range_iris": ["urn:Node"], "transitive": True}]}
    draft = s.create(s.document)
    assert s.publish(draft, s.approve(draft)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        change = inference.preview_inference_profile(uow, s.actor("alice"))
        approval = ApprovalService(uow).submit(s.actor("alice"), change.kind,
            list(change.target_ids), change.change_digest, change.baseline, {"command": change.command})
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "independent fixture")
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        s.bundle = inference.activate_inference_profile(uow, s.actor("alice"), approval.id)
        uow.commit()
    s.ids = {}
    def create(name, cls="urn:Node", scope="farm_a"):
        identifier = uuid4()
        response = s.client.post("/api/v1/objects", headers=s.headers(), json={
            "bundle_id": str(s.bundle.id), "create": {"resource_id": str(identifier),
                "class_iris": [cls], "display_name": name, "owner_scope_id": str(s.scopes[scope]),
                "state": "active", "data_nature": "test", "assertions": []}})
        assert response.status_code == 201, response.text
        s.ids[name] = identifier
        return identifier
    s.create_node = create
    def edge(source, target, state="accepted", assertion_id=None, valid_to=None):
        identifier = s.ids[source]
        revision = s.client.get(f"/api/v1/objects/{identifier}").json()["revision"]
        value = {"assertion_id": str(assertion_id or uuid4()), "resource_id": str(identifier),
            "predicate_iri": "urn:connects", "object": {"kind": "iri",
                "value": f"urn:lzo:object:{s.ids[target]}"}, "state": state, "data_nature": "test",
            "valid_from": "2026-01-01T00:00:00Z", "valid_to": valid_to,
            "support": {"support_id": str(uuid4()), "raw_value": {"synthetic": True}}}
        response = s.client.patch(f"/api/v1/objects/{identifier}", headers=s.headers(revision),
            json={"bundle_id": str(s.bundle.id), "patch": {"resource_id": str(identifier),
                "expected_revision": revision, "additions": [value]}})
        assert response.status_code == 200, response.text
        return value
    s.edge = edge
    def withdraw(value):
        identifier = value["resource_id"]
        revision = s.client.get(f"/api/v1/objects/{identifier}").json()["revision"]
        response = s.client.patch(f"/api/v1/objects/{identifier}", headers=s.headers(revision),
            json={"bundle_id": str(s.bundle.id), "patch": {"resource_id": identifier,
                "expected_revision": revision, "withdraw_support_ids": [value["support"]["support_id"]]}})
        assert response.status_code == 200, response.text
    s.withdraw = withdraw
    def drain():
        runner = Runner(s.app, s.config)
        for _ in range(100):
            if not runner.run_once():
                return
        pytest.fail("bounded queue did not drain")
    s.drain = drain
    def recompute():
        from lzo.semantic.inference_jobs import recompute_inference
        from lzo.governance.models import platform_watermarks
        drain()
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            uow.job_settings = s.config
            watermark = ProjectionService(uow, None).advance_contiguous()
            s.bundle = current_execution_bundle(uow, s.actor("alice"))
            try:
                job = recompute_inference(s.actor("alice"), s.bundle, watermark)
            except Exception:
                print("\n".join(s.worker_errors))
                raise
            uow.commit()
        drain()
        if s.worker_errors:
            print("\n".join(s.worker_errors))
        return job
    s.recompute = recompute
    def current():
        from lzo.semantic.proofs import current_conclusions
        with s.transaction() as uow, permission_context(uow):
            return current_conclusions(uow, s.actor("alice"), s.config)
    s.current = current
    return s


def test_real_cycle_multiple_support_retraction_and_immutable_proofs(inference_case):
    from lzo.semantic.rdf import Triple, RdfTerm
    s = inference_case
    for name in ("a", "b", "c"):
        s.create_node(name)
    ab = s.edge("a", "b")
    duplicate = s.edge("a", "b", assertion_id=ab["assertion_id"])
    bc, ca = s.edge("b", "c"), s.edge("c", "a")
    wanted = Triple(f"urn:lzo:object:{s.ids['a']}", "urn:connects",
                    RdfTerm("iri", f"urn:lzo:object:{s.ids['c']}"))
    s.recompute()
    assert wanted in s.current()
    from sqlalchemy import select
    from lzo.worker.models import jobs
    from lzo.semantic.inference_jobs import components
    from lzo.semantic.inference import derived_assertion_id
    from lzo.semantic.proofs import historical_proof
    from lzo.auth.permissions import permission_context
    with s.transaction() as uow:
        # Object triggers coalesce. The catalog/profile trigger has a distinct
        # protected dependency footprint; explicit recompute is a third job.
        assert len(uow.session.execute(select(jobs.c.id).where(
            jobs.c.kind == "inference.compute")).scalars().all()) <= 3
        saved = next(row["id"] for row in uow.session.execute(select(components)).mappings()
            if str(derived_assertion_id(wanted)) in row["payload"]["assertion_ids"])
    s.withdraw(ab)
    s.recompute()
    assert wanted in s.current()
    s.withdraw(duplicate)
    s.withdraw(bc)
    s.withdraw(ca)
    final_job = s.recompute()
    assert not [t for t in s.current() if t.predicate_iri == "urn:connects"]
    with s.transaction() as uow, permission_context(uow):
        from lzo.worker.service import JobService
        from lzo.semantic.inference_jobs import _manifest
        from lzo.semantic.projection import inference_is_current
        assert JobService(uow, s.config).get(s.actor("alice"), final_job.id).job.status == "succeeded"
        assert _manifest(uow)[1]["state"] == "current" and inference_is_current(uow)
        proof = historical_proof(uow, s.actor("alice"), derived_assertion_id(wanted), saved)
        assert proof.alternatives and proof.conclusion == wanted
    assert s.client.get(f"/api/v1/graph/proofs/{derived_assertion_id(wanted)}").status_code == 404


def test_failed_budget_after_independently_approved_deactivation_hides_old_type(inference_case):
    from sqlalchemy import insert, select, update
    from lzo.auth import models
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic.inference import derived_assertion_id
    from lzo.semantic.inference_jobs import components, current
    from lzo.semantic.proofs import historical_proof
    from lzo.semantic.rdf import Triple, RdfTerm
    from lzo.semantic.schemas import DeactivateObjectRequest
    from lzo.semantic.facts import deactivate_object
    from lzo.worker.service import JobService
    from rdflib.namespace import RDF
    s = inference_case
    target = s.create_node("target", "urn:Special")
    s.create_node("unrelated", "urn:Special")  # nonempty inference remains after deactivation
    s.recompute()
    wanted = Triple(f"urn:lzo:object:{target}", str(RDF.type), RdfTerm("iri", "urn:Node"))
    assert wanted in s.current()
    with s.transaction() as uow:
        saved = next(row["id"] for row in uow.session.execute(select(components)).mappings()
            if str(derived_assertion_id(wanted)) in row["payload"]["assertion_ids"])
        complete = uow.session.execute(select(current.c.complete_manifest_id)).scalar_one()
        role = uuid4()
        uow.session.execute(insert(models.roles).values(id=role, name="inference-retirement-review",
            actions=["object.review"], revision=1))
        uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=s.users["admin"],
            role_id=role, scope_id=s.scopes["farm_a"], security_class="data", revision=1))
        uow.session.execute(update(models.authorization_state).values(
            version=models.authorization_state.c.version + 1))
        uow.commit()
    command = {"operation": "object.deactivate", "resource_id": str(target),
               "expected_revision": 1, "reason": "Independently approved sole type-root lifecycle removal"}
    with s.transaction() as uow, permission_context(uow):
        service = ApprovalService(uow)
        change = service.preview(s.actor("alice"), "object.deactivate", [target], command)
        approval = service.submit(s.actor("alice"), change.kind, list(change.target_ids),
            change.change_digest, change.baseline, {"command": change.command})
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "independent lifecycle review")
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        deactivate_object(uow, s.actor("alice"), target, 1,
            DeactivateObjectRequest(reason=command["reason"], approval_id=approval.id))
        uow.commit()
    assert wanted not in s.current()
    s.config = s.config.model_copy(update={"inference_max_triples": 0})
    job = s.recompute()
    with s.transaction() as uow, permission_context(uow):
        assert JobService(uow, s.config).get(s.actor("alice"), job.id).job.status == "failed"
        assert uow.session.execute(select(current.c.complete_manifest_id)).scalar_one() == complete
        assert historical_proof(uow, s.actor("alice"), derived_assertion_id(wanted), saved).alternatives
    assert wanted not in s.current()


def test_disconnected_assemblies_keep_fence_dirty_until_all_components_current(inference_case):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.semantic.inference_jobs import runs, manifests, _manifest, current
    from lzo.semantic.projection import inference_is_current
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService
    s = inference_case
    a, b = s.create_node("a", "urn:Special"), s.create_node("b", "urn:Special")
    s.recompute()
    with s.transaction() as uow:
        complete = uow.session.execute(select(current.c.complete_manifest_id)).scalar_one()
    s.edge("a", "a")
    s.edge("b", "b")
    paused = None
    runner = Runner(s.app, s.config)
    for _ in range(100):
        busy = runner.run_once()
        with s.transaction() as uow, permission_context(uow):
            for job in uow.session.execute(select(jobs).where(
                    jobs.c.kind == "inference.compute", jobs.c.status == "queued")).mappings():
                run = uow.session.execute(select(runs.c.payload).where(
                    runs.c.id == UUID(job["payload"]["run_id"]))).scalar_one()
                members = {ref["resource_id"] for ref in run["scope"]["members"]}
                if members == {str(b)} and paused is None:
                    service = JobService(uow, s.config)
                    view = service.get(s.actor("alice"), job["id"])
                    service.set_state(s.actor("alice"), job["id"], "paused", view.revision)
                    paused = job["id"]
            uow.commit()
        if not busy:
            break
    assert paused is not None
    with s.transaction() as uow:
        _, assembly = _manifest(uow)
        assert assembly["state"] == "assembling" and not inference_is_current(uow)
        assert uow.session.execute(select(current.c.complete_manifest_id)).scalar_one() == complete
        a_component = next(key for key, part in assembly["components"].items() if part["members"] == [str(a)])
        assert a_component not in assembly["stale_component_ids"]
    assert s.current() == ()
    # A changes again while the older disjoint B job is paused. B must retain A
    # as stale until the new A result arrives, never mark it globally current.
    s.edge("a", "a")
    with s.transaction() as uow, permission_context(uow):
        service = JobService(uow, s.config)
        view = service.get(s.actor("alice"), paused)
        service.set_state(s.actor("alice"), paused, "queued", view.revision)
        uow.commit()
    s.drain()
    with s.transaction() as uow:
        _, assembly = _manifest(uow)
        assert assembly["state"] == "current" and inference_is_current(uow)
        assert any(a_component in value["stale_component_ids"] for value in
            uow.session.execute(select(manifests.c.payload)).scalars())
    assert s.current()


def test_cancel_stale_snapshot_and_revocation_do_not_publish_or_retag_jobs(inference_case):
    from sqlalchemy import select, func
    from lzo.auth.permissions import permission_context
    from lzo.semantic.inference_jobs import recompute_inference, current, components
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.proofs import current_conclusions
    from lzo.worker.models import jobs
    from lzo.worker.service import JobService
    s = inference_case
    s.create_node("first", "urn:Special")
    s.recompute()
    def enqueue_current():
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            uow.job_settings = s.config
            watermark = ProjectionService(uow, None).advance_contiguous()
            job = recompute_inference(s.actor("alice"), s.bundle, watermark)
            uow.commit()
            return job
    with s.transaction() as uow:
        initial = uow.session.execute(select(current.c.assembly_id)).scalar_one()
        count = uow.session.execute(select(func.count()).select_from(components)).scalar_one()
    cancelled = enqueue_current()
    with s.transaction() as uow, permission_context(uow):
        service = JobService(uow, s.config)
        service.set_state(s.actor("alice"), cancelled.id, "cancelled",
                          service.get(s.actor("alice"), cancelled.id).revision)
        uow.commit()
    s.drain()
    with s.transaction() as uow:
        assert uow.session.execute(select(current.c.assembly_id)).scalar_one() == initial
        assert uow.session.execute(select(func.count()).select_from(components)).scalar_one() == count
    obsolete = enqueue_current()
    s.create_node("second", "urn:Special")
    s.drain()
    with s.transaction() as uow:
        assert uow.session.execute(select(jobs.c.status).where(jobs.c.id == obsolete.id)).scalar_one() == "failed"
        before_denial = uow.session.execute(select(current.c.assembly_id)).scalar_one()
    denied = enqueue_current()
    s.revoke(s.data_grant)
    s.drain()
    with s.transaction() as uow, permission_context(uow):
        record = uow.session.execute(select(jobs).where(jobs.c.id == denied.id)).mappings().one()
        assert record["status"] == "failed" and record["error_code"] == "AUTHORIZATION_REVOKED"
        assert record["initiator_id"] == s.users["alice"]
        assert uow.session.execute(select(current.c.assembly_id)).scalar_one() == before_denial
        assert current_conclusions(uow, s.actor("admin"), s.config)


def test_existing_schedule_replay_rechecks_profile_after_competing_activation(inference_case, monkeypatch):
    from threading import Event, Thread
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic import inference, inference_jobs
    from lzo.semantic.facts import revision_ref
    from lzo.worker.models import jobs
    s = inference_case
    identifier = s.create_node("a", "urn:Special")
    s.recompute()
    s.edge("a", "a")
    s.drain()
    original_actor, original_bundle = s.actor("alice"), s.bundle
    with s.transaction() as uow, permission_context(uow):
        trigger = revision_ref(uow, identifier)
        scheduled = uow.session.execute(select(inference_jobs.schedules.c.job_id).where(
            inference_jobs.schedules.c.event_id == trigger.event_id,
            inference_jobs.schedules.c.bundle_id == original_bundle.id)).scalar_one()
        change = inference.preview_inference_profile(uow, original_actor)
        approval = ApprovalService(uow).submit(original_actor, change.kind,
            list(change.target_ids), change.change_digest, change.baseline, {"command": change.command})
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "independent race")
        uow.commit()

    entered, release = Event(), Event()
    original_lock = inference_jobs.lock_ledger
    outcomes = []
    def paused_lock(uow):
        entered.set()
        assert release.wait(30), "competing activation did not release scheduler"
        return original_lock(uow)
    monkeypatch.setattr(inference_jobs, "lock_ledger", paused_lock)
    def replay():
        try:
            with s.transaction() as uow, permission_context(uow):
                uow.ontology_settings = uow.job_settings = s.config
                outcomes.append(inference_jobs.schedule_inference(
                    uow, original_actor, trigger, original_bundle))
        except Exception as error:
            outcomes.append(error)
    worker = Thread(target=replay)
    worker.start()
    try:
        assert entered.wait(10), "scheduler did not reach ledger acquisition"
        # A distinct real transaction wins the common catalog/ledger update
        # while replay is paused before its actual ledger acquisition.
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = uow.job_settings = s.config
            new_bundle = inference.activate_inference_profile(uow, original_actor, approval.id)
            uow.commit()
    finally:
        release.set()
        worker.join(30)
        assert not worker.is_alive(), "scheduler thread did not finish"
    with s.transaction() as uow, permission_context(uow):
        assert current_execution_bundle(uow, original_actor) == new_bundle
        assert new_bundle.id != original_bundle.id
        assert uow.session.execute(select(inference_jobs.schedules.c.job_id).where(
            inference_jobs.schedules.c.event_id == trigger.event_id,
            inference_jobs.schedules.c.bundle_id == original_bundle.id)).scalar_one() == scheduled
        assert uow.session.execute(select(jobs.c.initiator_id).where(
            jobs.c.id == scheduled)).scalar_one() == original_actor.subject_id
    assert len(outcomes) == 1 and isinstance(outcomes[0], ApiError)
    assert outcomes[0].code == "INFERENCE_INPUT_STALE"


def test_succeeded_compute_pending_output_is_not_new_coalesced_work(inference_case):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.governance.models import platform_watermarks
    from lzo.semantic import inference_jobs
    from lzo.semantic.facts import revision_ref
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    s = inference_case
    identifier = s.create_node("a", "urn:Special")
    s.recompute()
    s.edge("a", "a")
    runner = Runner(s.app, s.config)
    assert runner.run_once()  # Real input projection schedules computation.
    assert runner.run_once()  # Real computation succeeds; output remains queued.
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        actor = s.actor("alice")
        trigger = revision_ref(uow, identifier)
        scheduled = uow.session.execute(select(inference_jobs.schedules.c.job_id).where(
            inference_jobs.schedules.c.event_id == trigger.event_id,
            inference_jobs.schedules.c.bundle_id == s.bundle.id)).scalar_one()
        computation = uow.session.execute(select(jobs).where(jobs.c.id == scheduled)).mappings().one()
        assert computation["status"] == "succeeded"
        output_id = uow.session.execute(select(inference_jobs.components.c.id).where(
            inference_jobs.components.c.payload["run_id"].astext ==
            computation["payload"]["run_id"])).scalar_one()
        assert uow.session.execute(select(jobs.c.status).where(jobs.c.kind == "rdf.project",
            jobs.c.payload["resource_id"].astext == str(output_id))).scalar_one() == "queued"
        # Replay of the already-bound event stays truthful and idempotent.
        assert inference_jobs.schedule_inference(uow, actor, trigger, s.bundle).id == scheduled
        watermark = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
        payload = inference_jobs._prepare(uow, actor, s.bundle, watermark, trigger)
        # Even equivalent current evidence cannot turn completed computation
        # into a queued/running candidate for a new trigger binding.
        assert inference_jobs._coalesced_job(uow, actor, payload, trigger.event_id) is None


def test_schema_axiom_removal_uses_new_release_and_keeps_selected_profile(inference_case):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.ontology.releases import current_execution_bundle
    from lzo.semantic.inference_jobs import components
    from lzo.semantic.inference import derived_assertion_id
    from lzo.semantic.proofs import historical_proof
    from lzo.semantic.rdf import Triple, RdfTerm
    from rdflib.namespace import RDF
    s = inference_case
    special = s.create_node("special", "urn:Special")
    for name in ("a", "b", "c"):
        s.create_node(name)
    s.edge("a", "b")
    s.edge("b", "c")
    s.recompute()
    derived_type = Triple(f"urn:lzo:object:{special}", str(RDF.type), RdfTerm("iri", "urn:Node"))
    transitive = Triple(f"urn:lzo:object:{s.ids['a']}", "urn:connects",
                       RdfTerm("iri", f"urn:lzo:object:{s.ids['c']}"))
    assert derived_type in s.current() and transitive in s.current()
    with s.transaction() as uow:
        old_component = next(row["id"] for row in uow.session.execute(select(components)).mappings()
            if str(derived_assertion_id(derived_type)) in row["payload"]["assertion_ids"])
    old_bundle = s.bundle
    document = {**s.document, "classes": [{"iri": "urn:Node", "label": "Node"},
        {"iri": "urn:Special", "label": "Special"}],
        "relations": [{**s.document["relations"][0], "transitive": False}]}
    draft = s.create(document, base=str(old_bundle.schema_release_id))
    response = s.publish(draft, s.approve(draft))
    assert response.status_code == 202, response.text
    final_job = s.recompute()
    with s.transaction() as uow, permission_context(uow):
        selected = current_execution_bundle(uow, s.actor("alice"))
        assert selected.schema_release_id != old_bundle.schema_release_id
        assert selected.inference_version_id == old_bundle.inference_version_id
        assert historical_proof(uow, s.actor("alice"), derived_assertion_id(derived_type),
                                old_component).alternatives
        from lzo.worker.service import JobService
        from lzo.semantic.inference_jobs import _manifest
        from lzo.semantic.projection import inference_is_current
        assert JobService(uow, s.config).get(s.actor("alice"), final_job.id).job.status == "succeeded"
        assert _manifest(uow)[1]["state"] == "current" and inference_is_current(uow)
    assert derived_type not in s.current() and transitive not in s.current()


def test_scoped_history_does_not_reveal_opaque_retained_members(inference_case):
    import json
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.governance.history import HistoryService
    from lzo.semantic.inference_jobs import runs, _manifest
    from lzo.worker.models import jobs
    from lzo.worker.service import JobService
    s = inference_case
    wider_grant = s.grant("alice", "editor", scope="farm_b")
    a = s.create_node("a", "urn:Special")
    hidden = s.create_node("hidden", "urn:Special", scope="farm_b")
    s.recompute()
    with s.transaction() as uow:
        _, before = _manifest(uow)
        hidden_component = next(key for key, value in before["components"].items()
                                if value["members"] == [str(hidden)])
    s.revoke(wider_grant)
    s.edge("a", "a")
    s.drain()
    with s.transaction() as uow, permission_context(uow):
        local = next(row for row in uow.session.execute(select(runs)).mappings()
            if row["payload"]["scope"]["mode"] == "affected"
            and [p["resource_id"] for p in row["payload"]["scope"]["members"]] == [str(a)])
        # The trusted internal inventory is complete, while public history is scoped.
        assert str(hidden) in local["payload"]["inventory"]["heads"]
        for revision in (1, 2):
            rendered = json.dumps(HistoryService(uow).get(s.actor("alice"),
                local["id"], revision).payload, default=str)
            assert str(hidden) not in rendered and hidden_component not in rendered
        job_id = uow.session.execute(select(jobs.c.id).where(
            jobs.c.payload["run_id"].astext == str(local["id"]))).scalar_one()
        view = JobService(uow, s.config).get(s.actor("alice"), job_id)
        assert str(hidden) not in view.model_dump_json()
        assert hidden_component not in view.model_dump_json()
        _, after = _manifest(uow)
        assert hidden_component in after["components"] and after["state"] == "current"
    # A hidden incoming link is a true boundary, not an absent row.
    wider = s.grant("alice", "editor", scope="farm_b")
    s.edge("hidden", "a")
    s.drain()
    s.revoke(wider)
    from lzo.errors import ApiError
    from lzo.governance.models import platform_watermarks
    from lzo.semantic.inference_jobs import _prepare
    from lzo.semantic.facts import revision_ref
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        frontier = uow.session.execute(select(platform_watermarks.c.projection_watermark)).scalar_one()
        with pytest.raises(ApiError) as denied:
            _prepare(uow, s.actor("alice"), s.bundle, frontier, revision_ref(uow, a))
        assert denied.value.code in ("NOT_FOUND", "FORBIDDEN")
        assert str(hidden) not in str(denied.value)


def test_expired_input_hides_derived_without_new_event_or_worker(inference_case):
    from datetime import timedelta
    import time
    from sqlalchemy import select, func
    from lzo.governance.models import platform_watermarks
    from lzo.semantic.rdf import Triple, RdfTerm
    s = inference_case
    for name in ("a", "b", "c"):
        s.create_node(name)
    unrelated = s.create_node("unrelated", "urn:Special")
    s.edge("b", "c")
    s.recompute()
    with s.transaction() as uow:
        expiry = uow.session.execute(select(func.clock_timestamp())).scalar_one() + timedelta(seconds=60)
    s.edge("a", "b", valid_to=expiry.isoformat())
    s.recompute()  # Full run includes both the expiring and unrelated components.
    from lzo.semantic.inference_jobs import _manifest
    with s.transaction() as uow:
        _, assembly = _manifest(uow)
        unrelated_id, unrelated_entry = next((key, value) for key, value in assembly["components"].items()
            if str(unrelated) in value["members"])
        assert unrelated_entry["next_boundary"] is None
    wanted = Triple(f"urn:lzo:object:{s.ids['a']}", "urn:connects",
                    RdfTerm("iri", f"urn:lzo:object:{s.ids['c']}"))
    assert wanted in s.current(), "The short-lived input must first publish a complete current proof"
    with s.transaction() as uow:
        watermarks = dict(uow.session.execute(select(platform_watermarks)).mappings().one())
    while True:
        with s.transaction() as uow:
            if uow.session.execute(select(func.clock_timestamp())).scalar_one() >= expiry:
                break
        time.sleep(.1)
    assert wanted not in s.current()
    with s.transaction() as uow:
        assert dict(uow.session.execute(select(platform_watermarks)).mappings().one()) == watermarks
    s.drain()
    from rdflib.namespace import RDF
    assert Triple(f"urn:lzo:object:{unrelated}", str(RDF.type), RdfTerm("iri", "urn:Node")) in s.current()
    with s.transaction() as uow:
        _, refreshed = _manifest(uow)
        assert refreshed["state"] == "current"
        assert refreshed["components"][unrelated_id] == unrelated_entry


def test_original_actor_dispatch_survives_different_actor_closing_frontier_gap(inference_case):
    import time
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.semantic.models import inference_requests
    from lzo.semantic.inference_jobs import schedules, schedule_inference, _manifest
    from lzo.semantic.facts import revision_ref
    from lzo.worker.models import jobs
    from lzo.worker.service import JobService
    from lzo.worker.runner import Runner
    s = inference_case
    a, b = s.create_node("a", "urn:Special"), s.create_node("b", "urn:Special")
    s.recompute()
    s.grant("admin", "editor", scope="farm_a")
    s.config = s.config.model_copy(update={"job_max_attempts": 3})
    s.client.app.state.settings = s.config
    s.edge("a", "a")
    s.login("admin")
    s.edge("b", "b")
    s.login()
    with s.transaction() as uow, permission_context(uow):
        a_ref = revision_ref(uow, a)
        b_job = uow.session.execute(select(jobs.c.id).where(jobs.c.kind == "rdf.project",
            jobs.c.payload["resource_id"].astext == str(b), jobs.c.status == "queued")).scalar_one()
        service = JobService(uow, s.config)
        service.set_state(s.actor("admin"), b_job, "paused", service.get(s.actor("admin"), b_job).revision)
        uow.commit()
    runner = Runner(s.app, s.config)
    assert runner.run_once()  # A base projection succeeds, global frontier remains behind B.
    with s.transaction() as uow:
        request = uow.session.execute(select(inference_requests).where(
            inference_requests.c.event_id == a_ref.event_id)).mappings().one()
        assert request["state"] == "pending" and request["error_code"] == "INFERENCE_PROJECTION_PENDING"
        dispatch = uow.session.execute(select(jobs).where(jobs.c.kind == "inference.dispatch",
            jobs.c.payload["event_id"].astext == str(a_ref.event_id))).mappings().one()
        assert dispatch["initiator_id"] == s.users["alice"] and dispatch["bundle_id"] == s.bundle.id
        assert dispatch["max_attempts"] == s.config.job_max_attempts
    assert runner.run_once()  # Genuine delayed retry, without fabricating a computation.
    with s.transaction() as uow, permission_context(uow):
        service = JobService(uow, s.config)
        service.set_state(s.actor("admin"), b_job, "queued", service.get(s.actor("admin"), b_job).revision)
        uow.commit()
    s.drain()
    # Existing F05 backoff is real time; no mutation of availability or host clock.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        s.drain()
        with s.transaction() as uow:
            if uow.session.execute(select(inference_requests.c.state).where(
                    inference_requests.c.event_id == a_ref.event_id)).scalar_one() == "resolved":
                break
        time.sleep(.1)
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        scheduled = uow.session.execute(select(schedules.c.job_id).where(
            schedules.c.event_id == a_ref.event_id, schedules.c.bundle_id == s.bundle.id)).scalar_one()
        computation = uow.session.execute(select(jobs).where(jobs.c.id == scheduled)).mappings().one()
        assert computation["initiator_id"] == s.users["alice"] and computation["status"] == "succeeded"
        assert computation["max_attempts"] == s.config.job_max_attempts
        assert schedule_inference(uow, s.actor("alice"), a_ref, s.bundle).id == scheduled
        _, assembly = _manifest(uow)
        assert assembly["state"] == "current"


def test_lost_derived_remote_ack_keeps_manifest_and_recovers_same_output(inference_case, monkeypatch):
    import httpx
    from sqlalchemy import select, func
    from lzo.auth.permissions import permission_context
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.inference_jobs import recompute_inference, components, current
    from lzo.semantic.projection import ProjectionService
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService
    s = inference_case
    s.create_node("a", "urn:Special")
    s.recompute()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        before = uow.session.execute(select(current.c.assembly_id)).scalar_one()
        computation = recompute_inference(s.actor("alice"), s.bundle,
            ProjectionService(uow, None).advance_contiguous())
        uow.commit()
    runner = Runner(s.app, s.config)
    assert runner.run_once()
    with s.transaction() as uow:
        run_id = uow.session.execute(select(jobs.c.payload).where(
            jobs.c.id == computation.id)).scalar_one()["run_id"]
        output = uow.session.execute(select(components.c.id).where(
            components.c.payload["run_id"].astext == run_id)).scalar_one()
        output_job = uow.session.execute(select(jobs.c.id).where(
            jobs.c.kind == "rdf.project", jobs.c.payload["resource_id"].astext == str(output))).scalar_one()
        assert uow.session.execute(select(jobs.c.max_attempts).where(
            jobs.c.id == output_job)).scalar_one() == s.config.job_max_attempts
    actual_publish = FusekiRepository.publish_revision
    lost = False
    def lose_once(repository, resource_id, *args, **kwargs):
        nonlocal lost
        receipt = actual_publish(repository, resource_id, *args, **kwargs)
        if resource_id == output and not lost:
            lost = True
            raise httpx.ReadError("Simulated lost acknowledgement after actual remote write")
        return receipt
    monkeypatch.setattr(FusekiRepository, "publish_revision", lose_once)
    assert runner.run_once() and lost
    assert FusekiRepository(s.config.fuseki_url).current_revision(output)["revision"] == 1
    with s.transaction() as uow, permission_context(uow):
        assert uow.session.execute(select(current.c.assembly_id)).scalar_one() == before
        service = JobService(uow, s.config)
        view = service.get(s.actor("alice"), output_job)
        assert view.job.status == "failed"
        service.set_state(s.actor("alice"), output_job, "queued", view.revision)
        uow.commit()
    assert runner.run_once()
    with s.transaction() as uow:
        assert uow.session.execute(select(current.c.assembly_id)).scalar_one() != before
        assert uow.session.execute(select(func.count()).select_from(components).where(
            components.c.payload["run_id"].astext == run_id)).scalar_one() == 1
    assert s.current()


def test_new_disconnected_member_after_compute_preserves_sync_and_requires_recompute(inference_case):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.semantic import inference_jobs as inference
    from lzo.semantic.facts import revision_ref
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.models import projection_events, inference_requests
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    s = inference_case
    a = s.create_node("a", "urn:Special")
    s.recompute()
    s.edge("a", "a")
    runner = Runner(s.app, s.config)
    assert runner.run_once()  # input projection, affected computation queued
    assert runner.run_once()  # immutable output prepared
    with s.transaction() as uow:
        trigger = revision_ref(uow, a)
        publication = uow.session.execute(select(jobs).where(
            jobs.c.kind == "rdf.project", jobs.c.status == "queued")).mappings().one()
        output_id = UUID(publication["payload"]["resource_id"])
        run_id = UUID(uow.session.execute(select(inference.components.c.payload).where(
            inference.components.c.id == output_id)).scalar_one()["run_id"])
        before = uow.session.execute(select(inference.current.c.assembly_id)).scalar_one()
        covered = set(uow.session.execute(select(inference.coverage.c.event_id)).scalars())
    s.create_node("new-disconnected", "urn:Special")
    assert runner.run_once()  # A really synchronizes before B's base projection.
    assert FusekiRepository(s.config.fuseki_url).current_revision(output_id)["revision"] == 1
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        assert uow.session.execute(select(projection_events.c.state).where(
            projection_events.c.resource_id == output_id)).scalar_one() == "synced"
        assert inference._terminal_state(uow, run_id) == "superseded"
        assert uow.session.execute(select(inference.current.c.assembly_id)).scalar_one() == before
        assert set(uow.session.execute(select(inference.coverage.c.event_id)).scalars()) == covered
        request = uow.session.execute(select(inference_requests).where(
            inference_requests.c.event_id == trigger.event_id)).mappings().one()
        assert request["state"] == "pending" and request["error_code"] == "INFERENCE_RECOMPUTE_REQUIRED"
        with pytest.raises(ApiError) as terminal:
            inference.schedule_inference(uow, s.actor("alice"), trigger, s.bundle)
        assert terminal.value.code == "INFERENCE_RECOMPUTE_REQUIRED"
    fresh = s.recompute()
    with s.transaction() as uow:
        assert uow.session.execute(select(jobs.c.status).where(jobs.c.id == fresh.id)).scalar_one() == "succeeded"
        assert inference._manifest(uow)[1]["state"] == "current"
    assert s.current()


def test_revoked_actor_cannot_retire_but_authorized_operator_can_recover(inference_case):
    from sqlalchemy import insert, select, update
    from lzo.auth import models
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.semantic import inference_jobs
    from lzo.semantic.models import inference_fence, projection_events
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.facts import revision_ref
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService
    s = inference_case
    assert hasattr(inference_jobs, "retire_inference_run"), "Explicit protected output retirement is missing"
    a = s.create_node("a", "urn:Special")
    s.recompute()
    s.grant("admin", "editor", scope="farm_a")
    with s.transaction() as uow:
        role = uuid4()
        uow.session.execute(insert(models.roles).values(id=role, name="operator-recompute",
            actions=["view", "schema.publish"], revision=1))
        uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=s.users["admin"],
            role_id=role, security_class="schema", global_scope=True, revision=1))
        uow.session.execute(update(models.authorization_state).values(
            version=models.authorization_state.c.version + 1))
        uow.commit()
    s.edge("a", "a")
    runner = Runner(s.app, s.config)
    assert runner.run_once()  # base projection
    assert runner.run_once()  # complete immutable computation, output not published
    with s.transaction() as uow:
        output_job = uow.session.execute(select(jobs).where(
            jobs.c.kind == "rdf.project", jobs.c.status == "queued")).mappings().one()
        output_id = UUID(output_job["payload"]["resource_id"])
        run_id = UUID(uow.session.execute(select(inference_jobs.components.c.payload).where(
            inference_jobs.components.c.id == output_id)).scalar_one()["run_id"])
        expected_revision = revision_ref(uow, run_id).revision
        fence = dict(uow.session.execute(select(inference_fence)).mappings().one())
        manifest_before = uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one()
        covered_before = set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars())
    s.revoke(s.data_grant)
    runner.run_once()  # denial may be terminalized during claim, with no returned work
    with s.transaction() as uow:
        rejected = uow.session.execute(select(jobs).where(jobs.c.id == output_job["id"])).mappings().one()
        assert rejected["status"] == "failed" and rejected["error_code"] == "AUTHORIZATION_REVOKED"
        assert rejected["initiator_id"] == s.users["alice"]
        assert dict(uow.session.execute(select(inference_fence)).mappings().one()) == fence
        assert uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one() == manifest_before
        assert set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars()) == covered_before
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        with pytest.raises(ApiError):
            inference_jobs.retire_inference_run(uow, s.actor("alice"), run_id, expected_revision)
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        with pytest.raises(ApiError) as valid:
            inference_jobs.retire_inference_run(uow, s.actor("admin"), run_id, expected_revision)
        assert valid.value.code == "INFERENCE_RUN_NOT_OBSOLETE"
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        service = JobService(uow, s.config)
        view = service.get(s.actor("admin"), output_job["id"])
        service.set_state(s.actor("admin"), output_job["id"], "cancelled", view.revision)
        inference_jobs.retire_inference_run(uow, s.actor("admin"), run_id, expected_revision)
        assert dict(uow.session.execute(select(inference_fence)).mappings().one()) == fence
        assert uow.session.execute(select(projection_events.c.state).where(
            projection_events.c.resource_id == output_id)).scalar_one() != "synced"
        assert uow.session.execute(select(jobs.c.initiator_id).where(
            jobs.c.id == output_job["id"])).scalar_one() == s.users["alice"]
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        fresh = inference_jobs.recompute_inference(s.actor("admin"), s.bundle,
            ProjectionService(uow, None).advance_contiguous())
        uow.commit()
    s.drain()
    with s.transaction() as uow, permission_context(uow):
        from lzo.semantic.proofs import current_conclusions
        assert JobService(uow, s.config).get(s.actor("admin"), fresh.id).job.status == "succeeded"
        assert current_conclusions(uow, s.actor("admin"), s.config)


def test_run_triple_budget_counts_disconnected_roots_and_shared_schema_once(inference_case):
    from sqlalchemy import select
    from rdflib.namespace import RDF
    from lzo.auth.permissions import permission_context
    from lzo.semantic import inference_jobs
    from lzo.semantic.inference import InferenceInput, InferenceBudget, derive, derived_assertion_id
    from lzo.semantic.proofs import historical_proof
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.rdf import Triple, RdfTerm
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService
    s = inference_case
    a = s.create_node("a", "urn:Special")
    s.recompute()
    wanted = Triple(f"urn:lzo:object:{a}", str(RDF.type), RdfTerm("iri", "urn:Node"))
    assert wanted in s.current()
    with s.transaction() as uow:
        historical = next(row for row in uow.session.execute(select(inference_jobs.components)).mappings()
            if str(derived_assertion_id(wanted)) in row["payload"]["assertion_ids"])
        schema = set(InferenceInput.model_validate(historical["payload"]["input"]).schema_triples)
    assert schema
    b = s.create_node("b", "urn:Special")
    assert Runner(s.app, s.config).run_once()  # Project B, without computing it.
    s.config = s.config.model_copy(update={"inference_max_triples": len(schema) + 3})
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        service = JobService(uow, s.config)
        automatic = uow.session.execute(select(jobs.c.id).where(
            jobs.c.kind == "inference.compute", jobs.c.status == "queued")).scalar_one()
        service.set_state(s.actor("alice"), automatic, "paused", service.get(s.actor("alice"), automatic).revision)
        job = inference_jobs.recompute_inference(s.actor("alice"), s.bundle,
            ProjectionService(uow, None).advance_contiguous())
        computation = uow.session.execute(select(jobs).where(jobs.c.id == job.id)).mappings().one()
        payload = uow.session.execute(select(inference_jobs.runs.c.payload).where(
            inference_jobs.runs.c.id == UUID(computation["payload"]["run_id"]))).scalar_one()
        assert len(payload["components"]) == 2
        # Each component has the same schema, one explicit Special type and
        # one propagated Node type. The exact global union is schema + four.
        for part in payload["components"]:
            inputs = InferenceInput.model_validate(part["input"])
            assert set(inputs.schema_triples) == schema and inputs.assertions == ()
            assert len(inputs.type_declarations) == 1
            assert inputs.type_declarations[0].class_iris == ("urn:Special",)
            assert derive(inputs, InferenceBudget(max_triples=len(schema) + 2,
                max_proofs=10000, timeout_ms=5000)).complete
        saved_components = set(uow.session.execute(select(inference_jobs.components.c.id)).scalars())
        saved_manifest = uow.session.execute(select(inference_jobs.current)).mappings().one()
        saved_coverage = set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars())
        uow.commit()
    assert not s.current()
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow, permission_context(uow):
        service = JobService(uow, s.config)
        failed = service.get(s.actor("alice"), job.id)
        assert failed.job.status == "failed"
        assert set(uow.session.execute(select(inference_jobs.components.c.id)).scalars()) == saved_components
        assert dict(uow.session.execute(select(inference_jobs.current)).mappings().one()) == dict(saved_manifest)
        assert set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars()) == saved_coverage
        assert historical_proof(uow, s.actor("alice"), derived_assertion_id(wanted), historical["id"]).alternatives
        service.set_state(s.actor("alice"), job.id, "queued", failed.revision)
        uow.commit()
    assert not s.current()
    # Shared schema is charged only once: this exact union bound must succeed.
    s.config = s.config.model_copy(update={"inference_max_triples": len(schema) + 4})
    s.drain()
    with s.transaction() as uow, permission_context(uow):
        assert JobService(uow, s.config).get(s.actor("alice"), job.id).job.status == "succeeded"
    assert {wanted, Triple(f"urn:lzo:object:{b}", str(RDF.type), RdfTerm("iri", "urn:Node"))} <= set(s.current())


def test_cancelled_compute_after_batch_requires_each_publication_cancelled(inference_case):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.semantic import inference_jobs
    from lzo.semantic.facts import revision_ref
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.models import inference_output_retirements, projection_events
    from lzo.semantic.projection import ProjectionService
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService, _execution, handlers
    s = inference_case
    s.create_node("a", "urn:Special")
    s.create_node("b", "urn:Special")
    s.recompute()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        job = inference_jobs.recompute_inference(s.actor("alice"), s.bundle,
            ProjectionService(uow, None).advance_contiguous())
        manifest = uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one()
        covered = set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars())
        uow.commit()
    runner = Runner(s.app, s.config)
    ctx = runner.claim()
    assert ctx.job_id == job.id
    token = _execution.set(ctx)
    try:
        handlers["inference.compute"].call(ctx)  # Real F05 batch commits before finish.
    finally:
        _execution.reset(token)
    run_id = UUID(ctx.payload["run_id"])
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        compute = uow.session.execute(select(jobs).where(jobs.c.id == job.id)).mappings().one()
        assert compute["status"] == "running" and compute["cursor"]["computed"]
        outputs = list(uow.session.execute(select(inference_jobs.components.c.id).where(
            inference_jobs.components.c.payload["run_id"].astext == str(run_id))).scalars())
        assert len(outputs) == 2
        publications = list(uow.session.execute(select(jobs.c.id).where(jobs.c.kind == "rdf.project",
            jobs.c.payload["resource_id"].astext.in_([str(i) for i in outputs]))).scalars())
        assert len(publications) == 2
        expected = revision_ref(uow, run_id).revision
        service = JobService(uow, s.config)
        service.set_state(s.actor("alice"), job.id, "cancelled", service.get(s.actor("alice"), job.id).revision)
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        with pytest.raises(ApiError) as blocked:
            inference_jobs.retire_inference_run(uow, s.actor("alice"), run_id, expected)
        assert blocked.value.code == "INFERENCE_RUN_NOT_OBSOLETE"
    # Actual publication executes _before_output; it cannot automatically
    # bypass the same explicit-publication-cancellation condition.
    assert runner.run_once()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        assert inference_jobs._terminal_state(uow, run_id) is None
        assert not uow.session.execute(select(inference_output_retirements).where(
            inference_output_retirements.c.run_id == run_id)).first()
        assert "INFERENCE_RUN_NOT_OBSOLETE" in uow.session.execute(select(projection_events.c.error_code).where(
            projection_events.c.resource_id.in_(outputs))).scalars().all()
        service = JobService(uow, s.config)
        service.set_state(s.actor("alice"), publications[0], "cancelled",
            service.get(s.actor("alice"), publications[0]).revision)
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        with pytest.raises(ApiError) as one_pending:
            inference_jobs.retire_inference_run(uow, s.actor("alice"), run_id, expected)
        assert one_pending.value.code == "INFERENCE_RUN_NOT_OBSOLETE"
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        service = JobService(uow, s.config)
        service.set_state(s.actor("alice"), publications[1], "cancelled",
            service.get(s.actor("alice"), publications[1]).revision)
        inference_jobs.retire_inference_run(uow, s.actor("alice"), run_id, expected)
        assert inference_jobs._terminal_state(uow, run_id) == "retired"
        assert len(uow.session.execute(select(inference_output_retirements).where(
            inference_output_retirements.c.run_id == run_id)).all()) == 2
        uow.commit()
    with pytest.raises(ApiError) as finish:
        runner.finish(ctx)
    assert finish.value.code == "LEASE_EXPIRED"
    with pytest.raises(ApiError) as old_execution:
        inference_jobs._compute(ctx)
    assert old_execution.value.code == "LEASE_EXPIRED"
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        service = JobService(uow, s.config)
        with pytest.raises(ApiError) as retry:
            service.set_state(s.actor("alice"), job.id, "queued", service.get(s.actor("alice"), job.id).revision)
        assert retry.value.code == "JOB_STATE_CONFLICT"
        repository = FusekiRepository(s.config.fuseki_url)
        projection = ProjectionService(uow, repository, actor=s.actor("alice"))
        for output in outputs:
            assert projection.project_revision(revision_ref(uow, output)).state == "retired"
            assert repository.current_revision(output) is None
        assert uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one() == manifest
        assert set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars()) == covered
        assert set(uow.session.execute(select(inference_jobs.components.c.id).where(
            inference_jobs.components.c.payload["run_id"].astext == str(run_id))).scalars()) == set(outputs)


@pytest.mark.parametrize("reason", ["cancelled", "obsolete"])
def test_zero_output_retirement_fences_old_execution(inference_case, reason):
    from sqlalchemy import select
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.semantic import inference_jobs
    from lzo.semantic.facts import revision_ref
    from lzo.semantic.projection import ProjectionService
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    from lzo.worker.service import JobService, _execution, handlers
    s = inference_case
    s.create_node("a", "urn:Special")
    s.recompute()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = uow.job_settings = s.config
        job = inference_jobs.recompute_inference(s.actor("alice"), s.bundle,
            ProjectionService(uow, None).advance_contiguous())
        manifest = uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one()
        covered = set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars())
        uow.commit()
    runner = Runner(s.app, s.config)
    ctx = runner.claim()
    assert ctx.job_id == job.id
    run_id = UUID(ctx.payload["run_id"])
    if reason == "obsolete":
        s.edge("a", "a")  # Actual changed input; the old compute lease stays live.
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        if reason == "cancelled":
            service = JobService(uow, s.config)
            service.set_state(s.actor("alice"), job.id, "cancelled", service.get(s.actor("alice"), job.id).revision)
        inference_jobs.retire_inference_run(uow, s.actor("alice"), run_id, revision_ref(uow, run_id).revision)
        assert inference_jobs._terminal_state(uow, run_id) == "retired"
        uow.commit()
    execution_marker = _execution.set(ctx)
    try:
        if reason == "cancelled":
            with pytest.raises(ApiError) as fenced:
                handlers["inference.compute"].call(ctx)
            assert fenced.value.code == "LEASE_EXPIRED"
        else:
            # This context still owns a genuine live F05 lease. The durable
            # run terminal, not lease loss or a fabricated cursor, stops work.
            handlers["inference.compute"].call(ctx)
            runner.finish(ctx)
    finally:
        _execution.reset(execution_marker)
    with s.transaction() as uow:
        assert not uow.session.execute(select(inference_jobs.components.c.id).where(
            inference_jobs.components.c.payload["run_id"].astext == str(run_id))).first()
        assert uow.session.execute(select(inference_jobs.current.c.assembly_id)).scalar_one() == manifest
        assert set(uow.session.execute(select(inference_jobs.coverage.c.event_id)).scalars()) == covered
        row = uow.session.execute(select(jobs).where(jobs.c.id == job.id)).mappings().one()
        if reason == "obsolete":
            assert row["status"] == "succeeded" and row["cursor"]["terminal"] == "retired"
            assert row["cursor"]["publication_pending"] is False
        else:
            assert row["status"] == "cancelled"
