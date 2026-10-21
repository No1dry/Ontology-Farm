"""S02 authoritative preview and F05 exact publication-result owner boundaries."""
from copy import deepcopy
from uuid import UUID

import pytest
from sqlalchemy import select, update


def _validate(s, draft):
    response = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/validate",
                             headers=s.headers(draft["revision"]))
    assert response.status_code == 200
    return response.json()


def _publish(s, draft):
    response = s.publish(draft, s.approve(draft))
    assert response.status_code == 202
    return response.json()["id"]


def _job(s, job_id):
    response = s.client.get(f"/api/v1/jobs/{job_id}")
    assert response.status_code == 200
    return response.json()


def _original_release(s, job_id):
    from lzo.ontology.models import installations
    with s.transaction() as uow:
        return str(uow.session.execute(select(installations.c.release_id).where(
            installations.c.job_id == UUID(job_id))).scalar_one())


def _replay(s, job_id):
    """A fresh trusted delivery of the same immutable registered revision."""
    from lzo.worker.models import jobs
    from lzo.worker.service import enqueue
    with s.transaction() as uow:
        payload = uow.session.execute(select(jobs.c.payload).where(
            jobs.c.id == UUID(job_id))).scalar_one()
        replay = enqueue(uow, s.actor("alice"), "rdf.project", payload, None)
        uow.commit()
    return str(replay.id)


def test_preview_risk_reasons_follow_actual_active_release_not_draft_base(schema_case):
    from lzo.worker.runner import Runner
    s = schema_case
    first = s.create()
    impact = _validate(s, first)
    assert impact["risk"] == "high"
    assert impact["reasons"] == ["system_minimum_semantic_or_policy_change"]
    assert impact["active_release_id"] is None
    first_job = _publish(s, first)
    assert Runner(s.app, s.config).run_once()
    first_release = _original_release(s, first_job)
    document = deepcopy(first["document"])
    document["classes"][0]["label"] = "Sensor revised"
    second = s.create(document, base=first_release)
    impact = _validate(s, second)
    assert impact["risk"] == "low"
    assert impact["reasons"] == ["nonsemantic_metadata_only"]
    assert impact["active_release_id"] == first_release
    second_job = _publish(s, second)
    assert Runner(s.app, s.config).run_once()
    second_release = _original_release(s, second_job)
    # This draft explicitly names an old base while the descriptor uses the
    # newer active release. Its structural addition remains high risk.
    document["classes"].append({"iri": "urn:NewSensor", "label": "New"})
    candidate = s.create(document, base=first_release)
    impact = _validate(s, candidate)
    assert candidate["base_release_id"] == first_release
    assert impact["active_release_id"] == second_release
    assert impact["risk"] == "high"
    assert impact["reasons"] == ["system_minimum_semantic_or_policy_change"]
    approval = s.submit(candidate)
    assert approval["risk"] == impact["risk"]


def test_exact_release_result_after_real_install_replay_and_supersession(schema_case):
    from lzo.worker.runner import Runner
    from lzo.governance.models import revisions
    s = schema_case
    first = s.create()
    first_job = _publish(s, first)
    release_id = _original_release(s, first_job)
    assert _job(s, first_job)["result_ref"] is None
    assert s.client.get(f"/api/v1/schema/releases/{release_id}").json()["state"] == "installing"
    from lzo.ontology.models import installations
    from lzo.semantic.projection import ProjectionReceipt, producers
    # The owner hook must not turn a receipt into completion before its domain
    # publication transaction, or accept a different install-event identity.
    with s.transaction() as uow:
        event = uow.session.execute(select(revisions).join(installations,
            installations.c.event_id == revisions.c.event_id)).mappings().one()
        resolver = producers["schema.install"].result_resolver
        receipt = ProjectionReceipt(event["resource_id"], event["revision"], "synced", "test")
        assert resolver(uow, s.actor("alice"), event, receipt) is None
    assert Runner(s.app, s.config).run_once()
    expected = {"kind": "schema.release", "resource_id": release_id,
                "api_path": f"/api/v1/schema/releases/{release_id}"}
    completed = _job(s, first_job)
    assert completed["job"]["status"] == "succeeded"
    assert completed["result_ref"] == expected
    assert s.client.get(expected["api_path"]).json()["state"] == "published"
    with s.transaction() as uow:
        for state in ("failed", "superseded", "retired"):
            assert resolver(uow, s.actor("alice"), event,
                ProjectionReceipt(event["resource_id"], event["revision"], state, "test")) is None
        from uuid import uuid4
        assert resolver(uow, s.actor("alice"), dict(event) | {"event_id": uuid4()}, receipt) is None
    replay = _replay(s, first_job)
    assert Runner(s.app, s.config).run_once()
    assert _job(s, replay)["result_ref"] == expected
    document = deepcopy(first["document"])
    document["classes"][0]["label"] = "New current sensor"
    second = s.create(document, base=release_id)
    second_job = _publish(s, second)
    assert Runner(s.app, s.config).run_once()
    assert _original_release(s, second_job) != release_id
    assert s.client.get(expected["api_path"]).json()["state"] == "deprecated"
    superseded_replay = _replay(s, first_job)
    assert Runner(s.app, s.config).run_once()
    assert _job(s, superseded_replay)["result_ref"] == expected
    assert _job(s, first_job)["result_ref"] == expected
    with s.transaction() as uow:
        assert len(uow.session.execute(select(revisions.c.event_id).where(
            revisions.c.event_type == "schema.catalog.activated")).all()) == 2


@pytest.mark.parametrize("failure", ["remote", "reviewer_revoked"])
def test_failed_install_cannot_advertise_release_result(schema_case, failure, monkeypatch):
    from lzo.auth.models import grants, authorization_state
    from lzo.worker.runner import Runner
    s = schema_case
    # The queue snapshots its own Settings() at enqueue; bound this fixture's
    # attempts there, independently of the API/Runner configuration objects.
    monkeypatch.setenv("LZO_JOB_MAX_ATTEMPTS", "1")
    draft = s.create()
    job_id = _publish(s, draft)
    release_id = _original_release(s, job_id)
    from lzo.worker.models import jobs
    with s.transaction() as uow:
        assert uow.session.execute(select(jobs.c.max_attempts).where(
            jobs.c.id == UUID(job_id))).scalar_one() == 1
    if failure == "remote":
        config = s.config.model_copy(update={"fuseki_url": "http://127.0.0.1:1/lzo"})
    else:
        with s.transaction() as uow:
            uow.session.execute(update(grants).where(grants.c.id == s.schema_review_grant).values(active=False))
            uow.session.execute(update(authorization_state).values(version=authorization_state.c.version + 1))
            uow.commit()
        config = s.config
    assert Runner(s.app, config).run_once()
    s.login()
    job = _job(s, job_id)
    assert job["job"]["status"] == "failed"
    assert job["result_ref"] is None
    assert s.client.get(f"/api/v1/schema/releases/{release_id}").json()["state"] == "failed"


def test_completed_result_and_exact_release_hidden_after_source_revocation(schema_case):
    import io
    from dataclasses import asdict
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.worker.runner import Runner
    s = schema_case
    grant = s.grant("alice", "editor", scope="farm_a")
    s.grant("admin", "viewer", scope="farm_a")
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        store = store_for(uow)
        blob = store.put(io.BytesIO(b"U02 owned source"), "text/plain")
        ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.ids_by_name["object_a"],
            blob, filename="source.txt", metadata={"synthetic": True})
        uow.commit()
    draft = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
                      "dependencies": [{**asdict(blob), "version_id": str(blob.version_id)}]})
    job_id = _publish(s, draft)
    assert Runner(s.app, s.config).run_once()
    result = _job(s, job_id)["result_ref"]
    assert result is not None
    assert s.client.get(result["api_path"]).status_code == 200
    s.revoke(grant)
    s.login()
    assert s.client.get(f"/api/v1/jobs/{job_id}").status_code == 404
    assert s.client.get(result["api_path"]).status_code == 404
