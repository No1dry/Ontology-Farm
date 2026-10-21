"""Real HTTP, PostgreSQL, file artifacts and Fuseki S02 governance acceptance."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import insert, select, update


def test_review_read_uses_stored_server_digest_for_cross_runtime_metadata(schema_case):
    import json
    from pathlib import Path
    s = schema_case
    fixtures = json.loads((Path(__file__).resolve().parents[3] /
        "frontend/src/features/studio/fixtures/approval-documents.json").read_text(encoding="utf-8"))
    for fixture in fixtures:
        draft = s.create(fixture["document"])
        approval = s.submit(draft)
        s.login("admin")  # Has view/review, never schema.edit.
        command = s.client.get(f"/api/v1/approvals/{approval['id']}").json()["command"]
        response = s.client.get(f"/api/v1/schema/drafts/{draft['id']}")
        assert response.status_code == 200, response.text
        read = response.json()
        assert read["id"] == command["draft_id"] and read["revision"] == command["revision"]
        assert read["document_digest"] == command["document_digest"] == fixture["digest"]
        assert read["current_baseline"] == {"active_release_id": None}


def test_review_read_distinguishes_captured_first_baseline_from_current_activation(schema_case):
    from lzo.worker.runner import Runner
    s = schema_case
    pending = s.create()
    approval = s.submit(pending)
    s.login("admin")
    captured = s.client.get(f"/api/v1/approvals/{approval['id']}").json()
    assert captured["impact"]["active_release_id"] is None
    before = s.client.get(f"/api/v1/schema/drafts/{pending['id']}").json()
    assert before["current_baseline"] == {"active_release_id": None}
    first = s.create()
    approved = s.approve(first)
    job = s.publish(first, approved)
    assert job.status_code == 202, job.text
    assert Runner(s.app, s.config).run_once()
    active_id = s.client.get(f"/api/v1/jobs/{job.json()['id']}").json()["result_ref"]["resource_id"]
    s.login("admin")
    after = s.client.get(f"/api/v1/schema/drafts/{pending['id']}").json()
    assert after["revision"] == before["revision"]
    assert after["document_digest"] == before["document_digest"]
    assert after["current_baseline"] == {"active_release_id": active_id}
    assert s.client.get(f"/api/v1/approvals/{approval['id']}").json()["impact"]["active_release_id"] is None
    denied = s.client.post(f"/api/v1/approvals/{approval['id']}/decisions", headers=s.headers(captured["revision"]),
        json={"decision": "approve", "comment": "Changed active baseline"})
    assert denied.status_code == 409 and denied.json()["code"] == "APPROVAL_STALE"


def test_review_read_unavailable_catalog_is_unknown_and_draft_dependencies_stay_all(schema_case):
    from lzo.auth import models
    from lzo.ontology.models import catalog
    s = schema_case
    draft = s.create()
    # Give a reviewer only this draft. Absence of catalog view cannot mean first release.
    with s.transaction() as uow:
        uow.session.execute(update(models.grants).where(models.grants.c.id == s.schema_review_grant).values(active=False))
        role = uuid4()
        uow.session.execute(insert(models.roles).values(id=role, name="bounded-review-view", actions=["view"], revision=1))
        uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=s.users["admin"], role_id=role,
            resource_id=UUID(draft["id"]), security_class="schema", revision=1))
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1))
        uow.commit()
    s.login("admin")
    response = s.client.get(f"/api/v1/schema/drafts/{draft['id']}")
    assert response.status_code == 200, response.text
    assert response.json()["current_baseline"] is None
    # Existing ALL dependency checks still run before any digest is disclosed.
    from lzo.ontology.drafts import bind_dependencies
    with s.transaction() as uow:
        bind_dependencies(uow, UUID(draft["id"]), [s.ids_by_name["object_b"]])
        uow.commit()
    denied = s.client.get(f"/api/v1/schema/drafts/{draft['id']}")
    assert denied.status_code == 404 and "document_digest" not in denied.text


@pytest.fixture
def schema_case(scope_scenario, settings, tmp_path):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from lzo.auth import models
    from lzo.auth.session import SessionService, SESSION_COOKIE
    from lzo.main import create_app
    s = scope_scenario
    s.config = settings.model_copy(update={"session_encryption_key": Fernet.generate_key().decode(),
        "public_origin": "https://testserver", "file_root": tmp_path / "files", "job_retry_base_seconds": 1,
        "job_batch_timeout_seconds": 120, "job_lease_seconds": 180})
    # Explicit owner-operated synthetic fixture grants; no production bypass.
    with s.transaction() as uow:
        for name, actions in (("alice", ["view", "schema.edit", "schema.publish"]),
                              ("admin", ["view", "schema.review"]),
                              ("nobody", ["view"])):
            role, grant = uuid4(), uuid4()
            uow.session.execute(insert(models.roles).values(id=role, name=name+"-schema", actions=actions, revision=1))
            uow.session.execute(insert(models.grants).values(id=grant, user_id=s.users[name], role_id=role,
                security_class="schema", global_scope=True, revision=1))
            if name == "admin":
                s.schema_review_grant = grant
        uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1))
        uow.commit()
    app = create_app(s.config)
    app.state.engine.dispose()
    app.state.engine = s.app
    with TestClient(app, base_url="https://testserver") as client:
        s.client = client
        def login(name="alice"):
            with s.transaction() as uow:
                cookie = SessionService(uow, s.config).create(s.users[name], {"synthetic": True})
                uow.commit()
            client.cookies.set(SESSION_COOKIE, cookie)
            s.csrf = client.get("/api/v1/session").json()["csrf_token"]
        s.login = login
        s.headers = lambda revision=None: {"X-CSRF-Token": s.csrf, "Idempotency-Key": str(uuid4()),
            **({"If-Match": f'"{revision}"'} if revision is not None else {})}
        def create(document=None, base=None):
            s.login()
            response = client.post("/api/v1/schema/drafts", headers=s.headers(), json={
                "base_release_id": base, "document": document or {
                    "classes": [{"iri": "urn:Sensor", "label": "Sensor"},
                                {"iri": "urn:AmmoniaSensor", "label": "Ammonia", "parents": ["urn:Sensor"]}]}})
            assert response.status_code == 201, response.text
            return response.json()
        s.create = create
        def submit(draft):
            s.login()
            response = client.post(f"/api/v1/schema/drafts/{draft['id']}/validate", headers=s.headers(draft["revision"]))
            assert response.status_code == 200, response.text
            impact = response.json()
            response = client.post(f"/api/v1/schema/drafts/{draft['id']}/submit",
                headers=s.headers(draft["revision"]), json={"impact_digest": impact["digest"]})
            assert response.status_code == 201, response.text
            return response.json()
        s.submit = submit
        def approve(draft):
            approval = s.submit(draft)
            s.login("admin")
            approval = client.get(f"/api/v1/approvals/{approval['id']}").json()
            result = client.post(f"/api/v1/approvals/{approval['id']}/decisions",
                headers=s.headers(approval["revision"]), json={"decision": "approve", "comment": "Independent synthetic review"})
            assert result.status_code == 200, result.text
            s.login()
            return result.json()
        s.approve = approve
        def publish(draft, approval):
            return client.post("/api/v1/schema/releases", headers=s.headers(draft["revision"]),
                json={"draft_id": draft["id"], "approval_id": approval["id"]})
        s.publish = publish
        yield s


def test_schema_self_review_and_stale_approval(schema_case):
    s = schema_case
    draft = s.create()
    approval = s.submit(draft)
    response = s.client.post(f"/api/v1/approvals/{approval['id']}/decisions",
        headers=s.headers(1), json={"decision": "approve", "comment": "Self review"})
    assert response.status_code == 403
    approval = s.approve(draft)
    assert s.client.get(f"/api/v1/schema/drafts/{draft['id']}").json()["state"] == "approved"
    document = draft["document"]
    document["classes"][1]["label"] = "Ammonia sensor revised"
    changed = s.client.patch(f"/api/v1/schema/drafts/{draft['id']}",
        headers=s.headers(draft["revision"]), json={"document": document})
    assert changed.status_code == 200, changed.text
    response = s.publish(changed.json(), approval)
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_STALE"


def test_publish_installs_verified_graph_and_persistent_genesis_bundle(schema_case):
    from lzo.worker.runner import Runner
    from lzo.ontology.releases import get_execution_bundle
    from lzo.auth.permissions import permission_context
    s = schema_case
    draft = s.create()
    approval = s.approve(draft)
    response = s.publish(draft, approval)
    assert response.status_code == 202, response.text
    assert Runner(s.app, s.config).run_once()
    releases = s.client.get("/api/v1/schema/releases").json()["items"]
    assert len(releases) == 1 and releases[0]["state"] == "published"
    release = releases[0]
    from lzo.ontology.models import bundles, catalog
    with s.transaction() as uow:
        active = uow.session.execute(select(catalog)).mappings().one()
        assert str(active["active_release_id"]) == release["id"]
        bundle_id = uow.session.execute(select(bundles.c.id)).scalar_one()
        with permission_context(uow):
            bundle = get_execution_bundle(s.actor("alice"), bundle_id)
        assert str(bundle.schema_release_id) == release["id"]
        from lzo.governance.models import revisions
        for version in (bundle.authority_version_id, bundle.ruling_policy_version_id, bundle.inference_version_id):
            assert uow.session.execute(select(revisions.c.event_id).where(revisions.c.resource_id == version)).scalar_one()
    # Fresh UoW read, immutable identities and actual logical remote graph.
    with s.transaction() as uow, permission_context(uow):
        assert get_execution_bundle(s.actor("alice"), bundle_id) == bundle
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    triples = FusekiRepository(s.config.fuseki_url).read_graph(graph_iri(UUID(release["id"]), "schema"))
    assert any(t.subject_iri == "urn:AmmoniaSensor" and t.object.value == "urn:Sensor" for t in triples)
    assert len(release["artifacts"]) >= 3
    for blob in release["artifacts"]:
        response = s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content")
        assert response.status_code == 200
    from lzo.ontology.releases import create_execution_bundle
    from lzo.errors import ApiError
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        with pytest.raises(ApiError, match="SHAPES_DIGEST_MISMATCH"):
            create_execution_bundle(uow, s.actor("alice"), UUID(release["id"]), None,
                bundle.authority_version_id, bundle.ruling_policy_version_id, bundle.inference_version_id, "0"*64)


def test_http_version_preconditions_and_independent_review_only(schema_case):
    s = schema_case
    draft = s.create()
    url = f"/api/v1/schema/drafts/{draft['id']}"
    assert s.client.patch(url, headers=s.headers(), json={"document": draft["document"]}).status_code == 428
    assert s.client.patch(url, headers=s.headers(0), json={"document": draft["document"]}).status_code == 409
    s.login("admin")
    assert s.client.get(url).status_code == 200
    assert s.client.patch(url, headers=s.headers(1), json={"document": draft["document"]}).status_code == 403


def test_preview_digest_binds_current_catalog_baseline(schema_case):
    from lzo.worker.runner import Runner
    s = schema_case
    first, second = s.create(), s.create()
    old = s.client.post(f"/api/v1/schema/drafts/{second['id']}/validate", headers=s.headers(1)).json()
    approved = s.approve(first)
    assert s.publish(first, approved).status_code == 202
    assert Runner(s.app, s.config).run_once()
    response = s.client.post(f"/api/v1/schema/drafts/{second['id']}/submit",
        headers=s.headers(1), json={"impact_digest": old["digest"]})
    assert response.status_code == 409
    assert response.json()["code"] == "APPROVAL_STALE"


def test_remote_success_pg_rollback_replays_activation_once(schema_case):
    from lzo.ontology.models import installations, catalog
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    from lzo.governance.models import revisions
    from lzo.contracts import RevisionRef
    from lzo.worker.runner import Runner
    s = schema_case
    draft = s.create()
    approval = s.approve(draft)
    response = s.publish(draft, approval)
    assert response.status_code == 202
    job_id = response.json()["id"]
    # Genuine remote publication succeeds; intentionally roll back the PG UoW.
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        row = uow.session.execute(select(revisions).join(installations,
            installations.c.event_id == revisions.c.event_id)).mappings().one()
        ref = RevisionRef(row["resource_id"], row["revision"], row["event_id"])
        receipt = ProjectionService(uow, FusekiRepository(s.config.fuseki_url),
                                    actor=s.actor("alice")).project_revision(ref)
        assert receipt.state == "synced"
    with s.transaction() as uow:
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() is None
    assert s.client.get(f"/api/v1/jobs/{job_id}").json()["result_ref"] is None
    assert FusekiRepository(s.config.fuseki_url).read_graph(graph_iri(ref.resource_id, "schema"))
    response = s.client.get(f"/api/v1/schema/releases/{ref.resource_id}/form-schema",
                            params={"class_iri": "urn:Sensor"})
    assert response.status_code == 409
    assert response.json()["code"] == "SCHEMA_NOT_PUBLISHED"
    from lzo.ontology.releases import get_released_form_schema
    from lzo.errors import ApiError
    with s.transaction() as uow:
        with pytest.raises(ApiError, match="SCHEMA_NOT_PUBLISHED"):
            get_released_form_schema(uow, s.actor("alice"), ref.resource_id, "urn:Sensor")
    assert Runner(s.app, s.config).run_once()
    result = s.client.get(f"/api/v1/jobs/{job_id}").json()["result_ref"]
    assert result == {"kind": "schema.release", "resource_id": str(ref.resource_id),
                      "api_path": f"/api/v1/schema/releases/{ref.resource_id}"}
    assert s.client.get(f"/api/v1/schema/releases/{ref.resource_id}/form-schema",
                        params={"class_iri": "urn:Sensor"}).status_code == 200
    with s.transaction() as uow:
        assert get_released_form_schema(uow, s.actor("alice"), ref.resource_id, "urn:Sensor").release_id == ref.resource_id
    with s.transaction() as uow:
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() == ref.resource_id
        assert len(uow.session.execute(select(revisions).where(
            revisions.c.event_type == "schema.catalog.activated")).all()) == 1
        uow.ontology_settings = s.config
        replay = ProjectionService(uow, FusekiRepository(s.config.fuseki_url),
                                    actor=s.actor("alice")).project_revision(ref)
        assert replay.state == "synced"
        uow.commit()
    with s.transaction() as uow:
        assert len(uow.session.execute(select(revisions).where(
            revisions.c.event_type == "schema.catalog.activated")).all()) == 1


def test_reviewer_revocation_blocks_install_and_releases_edit_lock(schema_case):
    from lzo.auth.models import grants, authorization_state
    from lzo.ontology.models import catalog
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s = schema_case
    draft = s.create()
    approval = s.approve(draft)
    response = s.publish(draft, approval)
    assert response.status_code == 202, response.text
    with s.transaction() as uow:
        uow.session.execute(update(grants).where(grants.c.id == s.schema_review_grant).values(active=False))
        uow.session.execute(update(authorization_state).values(version=authorization_state.c.version+1))
        uow.commit()
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow:
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() is None
        assert uow.session.execute(select(jobs.c.error_code).where(
            jobs.c.id == UUID(response.json()["id"]))).scalar_one() == "HANDLER_FAILED"
    s.login()
    assert s.client.get("/api/v1/schema/releases").json()["items"][0]["state"] == "failed"
    # Explicit cancellation ends queued retries before a new draft edit.
    job_id = response.json()["id"]
    job = s.client.get(f"/api/v1/jobs/{job_id}").json()
    response = s.client.post(f"/api/v1/jobs/{job_id}/pause", headers=s.headers(job["revision"]))
    assert response.status_code == 200
    response = s.client.patch(f"/api/v1/schema/drafts/{draft['id']}", headers=s.headers(1),
        json={"document": draft["document"]})
    assert response.status_code == 200


def test_generated_artifact_cannot_target_arbitrary_resource(schema_case):
    import io
    from lzo.errors import ApiError
    from lzo.ontology.releases import register_schema_artifact
    from lzo.ontology.drafts import store_for
    s = schema_case
    draft = s.create()
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        blob = store_for(uow).put(io.BytesIO(b"synthetic"), "text/plain")
        with pytest.raises(ApiError, match="SCHEMA_ARTIFACT_TARGET_INVALID"):
            register_schema_artifact(uow, s.actor("alice"), UUID(draft["id"]), blob, "invalid.txt")
        # Even an incorrectly classified trusted producer event is insufficient:
        # the authoritative target resource must itself be a Schema release.
        from lzo.governance.revisions import append_revision
        append_revision(uow, s.actor("alice"), UUID(draft["id"]), 1, {}, "schema.release.prepared")
        with pytest.raises(ApiError, match="SCHEMA_ARTIFACT_TARGET_INVALID"):
            register_schema_artifact(uow, s.actor("alice"), UUID(draft["id"]), blob, "invalid.txt")
    s.login("nobody")
    response = s.client.post("/api/v1/schema/releases", headers=s.headers(1),
        json={"draft_id": draft["id"], "approval_id": str(uuid4())})
    assert response.status_code == 403


def test_source_revocation_hides_release_artifacts_bundle_and_preparation_history(schema_case):
    import io
    from dataclasses import asdict
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.worker.runner import Runner
    from lzo.ontology.models import bundles
    from lzo.ontology.releases import get_execution_bundle
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    s = schema_case
    grant = s.grant("alice", "editor", scope="farm_a")
    s.grant("admin", "viewer", scope="farm_a")
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        store = store_for(uow)
        blob = store.put(io.BytesIO(b"synthetic dependency"), "text/plain")
        ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.ids_by_name["object_a"],
            blob, filename="dependency.txt", metadata={"synthetic": True})
        uow.commit()
    draft = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
                      "dependencies": [{**asdict(blob), "version_id": str(blob.version_id)}]})
    approval = s.approve(draft)
    assert s.publish(draft, approval).status_code == 202
    assert Runner(s.app, s.config).run_once()
    release = s.client.get("/api/v1/schema/releases").json()["items"][0]
    with s.transaction() as uow, permission_context(uow):
        bundle_id = uow.session.execute(select(bundles.c.id)).scalar_one()
        existing = get_execution_bundle(s.actor("alice"), bundle_id)
        from lzo.ontology.releases import create_execution_bundle
        additional = create_execution_bundle(uow, s.actor("alice"), existing.schema_release_id, None,
            existing.authority_version_id, existing.ruling_policy_version_id,
            existing.inference_version_id, existing.shapes_digest)
        uow.commit()
    s.revoke(grant)
    s.login()
    assert s.client.get(f"/api/v1/schema/releases/{release['id']}").status_code == 404
    for blob in release["artifacts"]:
        assert s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content").status_code == 404
    assert s.client.get(f"/api/v1/history/{release['id']}/revisions/1").status_code == 404
    with s.transaction() as uow, permission_context(uow):
        with pytest.raises(ApiError, match="NOT_FOUND"):
            get_execution_bundle(s.actor("alice"), bundle_id)
        with pytest.raises(ApiError, match="NOT_FOUND"):
            get_execution_bundle(s.actor("alice"), additional.id)


def test_hidden_pending_input_blocks_complete_impact_without_names(schema_case):
    from lzo.semantic.projection import ProjectionContent, ProjectionService, register_projection
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.rdf import Triple, RdfTerm
    from lzo.governance.revisions import append_revision
    s = schema_case
    s.grant("admin", "editor", scope="farm_b")
    resource = s.ids_by_name["object_b"]
    kind = "test.s02.secret." + uuid4().hex
    def resolver(uow, row):
        return ProjectionContent({"accepted": (Triple("urn:secret-object-name",
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", RdfTerm("iri", "urn:Sensor")),)}, (resource,))
    register_projection(kind, resolver, version="test-v1", dirty_kind="facts")
    with s.transaction() as uow:
        ref = append_revision(uow, s.actor("admin"), resource, 1, {"synthetic": True}, kind)
        ProjectionService(uow, FusekiRepository(s.config.fuseki_url)).schedule(s.actor("admin"), ref)
        uow.commit()
    draft = s.create()
    response = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/validate", headers=s.headers(1))
    assert response.status_code == 404
    assert "secret-object-name" not in response.text
    assert "affected_ids" not in response.text


def test_region_scoped_consumer_reads_definitions_without_private_impact(schema_case):
    from dataclasses import asdict
    import json
    from lzo.auth.permissions import permission_context
    from lzo.governance.models import revisions
    from lzo.governance.revisions import append_revision
    from lzo.ontology.models import releases as release_rows
    from lzo.ontology.releases import get_execution_bundle, create_execution_bundle
    from lzo.semantic.projection import ProjectionContent, ProjectionService, register_projection
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.rdf import Triple, RdfTerm
    from lzo.worker.runner import Runner
    s = schema_case
    s.grant("alice", "editor", scope="farm_a")
    s.grant("alice", "viewer", scope="farm_b")
    s.grant("admin", "viewer", scope="farm_a")
    s.grant("admin", "editor", scope="farm_b")
    s.grant("nobody", "viewer", scope="farm_a")
    private_id = str(s.ids_by_name["object_b"])
    private_iri = "urn:s02:private-farm-b-object"
    for name, actor, iri in (("object_a", "alice", "urn:s02:farm-a-object"),
                             ("object_b", "admin", private_iri)):
        resource = s.ids_by_name[name]
        kind = "test.s02.regional." + uuid4().hex
        def resolver(uow, row):
            return ProjectionContent({"accepted": (Triple(row["payload"]["iri"],
                "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", RdfTerm("iri", "urn:Sensor")),)},
                (row["resource_id"],))
        register_projection(kind, resolver, version="test-v1", dirty_kind="facts")
        with s.transaction() as uow:
            ref = append_revision(uow, s.actor(actor), resource, 1, {"iri": iri}, kind)
            ProjectionService(uow, FusekiRepository(s.config.fuseki_url)).schedule(s.actor(actor), ref)
            uow.commit()
    draft = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "properties": [{"iri": "urn:value", "label": "Value",
                        "datatype_iri": "http://www.w3.org/2001/XMLSchema#integer",
                        "owner_policy_iri": "urn:manual"}],
        "shapes": '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:shape> sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:value>].'})
    approval = s.approve(draft)
    assert private_id in approval["baseline"]
    job = s.publish(draft, approval)
    assert job.status_code == 202
    runner = Runner(s.app, s.config)
    for _ in range(3):
        assert runner.run_once()
    release = s.client.get("/api/v1/schema/releases").json()["items"][0]
    with s.transaction() as uow, permission_context(uow):
        bundle_id = uow.session.execute(select(release_rows.c.bundle_id).where(
            release_rows.c.id == UUID(release["id"]))).scalar_one()
        bundle = get_execution_bundle(s.actor("alice"), bundle_id)
        additional = create_execution_bundle(uow, s.actor("alice"), bundle.schema_release_id, None,
            bundle.authority_version_id, bundle.ruling_policy_version_id, bundle.inference_version_id,
            bundle.shapes_digest)
        private_revisions = uow.session.execute(select(revisions.c.revision).where(
            revisions.c.resource_id == UUID(release["id"]),
            revisions.c.event_type.in_(("schema.install", "schema.release.prepared")))).scalars().all()
        uow.commit()
    s.login("nobody")
    def public(response):
        assert response.status_code == 200, response.text
        assert private_id not in response.text and private_iri not in response.text
        assert "affected_ids" not in response.text
        return response.json()
    public(s.client.get("/api/v1/schema/releases/" + release["id"]))
    assert len(public(s.client.get("/api/v1/schema/releases"))["items"]) == 1
    form = public(s.client.get(f"/api/v1/schema/releases/{release['id']}/form-schema",
                              params={"class_iri": "urn:Sensor"}))
    assert form["fields"][0]["iri"] == "urn:value"
    for blob in release["artifacts"]:
        public(s.client.get(f"/api/v1/artifacts/{blob['version_id']}"))
        content = s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content")
        assert content.status_code == 200
        assert private_id not in content.text and private_iri not in content.text
    with s.transaction() as uow, permission_context(uow):
        for identifier in (bundle_id, additional.id):
            visible_bundle = get_execution_bundle(s.actor("nobody"), identifier)
            assert private_id not in json.dumps(asdict(visible_bundle), default=str)
    hidden_urls = [f"/api/v1/approvals/{approval['id']}", f"/api/v1/jobs/{job.json()['id']}",
        f"/api/v1/schema/releases/{release['id']}/diff?base_release_id={release['id']}"]
    hidden_urls += [f"/api/v1/history/{release['id']}/revisions/{revision}" for revision in private_revisions]
    hidden_urls += [f"/api/v1/history/{release['id']}/diff?from_revision={private_revisions[-1]}&to_revision={release['revision']}"]
    for url in hidden_urls:
        response = s.client.get(url)
        assert response.status_code == 404, response.text
        assert private_id not in response.text and private_iri not in response.text
        assert "affected_ids" not in response.text
    history = public(s.client.get("/api/v1/history", params={"resource_id": release["id"]}))
    assert not set(private_revisions) & {row["revision"] for row in history["items"]}


def test_competing_approved_installs_keep_first_active_snapshot(schema_case):
    from lzo.ontology.models import catalog, releases as release_rows, installations
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    from lzo.worker.runner import Runner
    s = schema_case
    first, second = s.create(), s.create()
    first_approval, second_approval = s.approve(first), s.approve(second)
    with s.transaction() as uow:
        baseline = uow.session.execute(select(catalog)).mappings().one()
    key = str(baseline["resource_id"])
    assert first_approval["id"] != second_approval["id"]
    assert first_approval["baseline"][key] == second_approval["baseline"][key] == baseline["revision"]
    first_job, second_job = s.publish(first, first_approval), s.publish(second, second_approval)
    assert first_job.status_code == second_job.status_code == 202
    with s.transaction() as uow:
        identifiers = {str(row["draft_id"]): row["id"] for row in
            uow.session.execute(select(release_rows)).mappings()}
    runner = Runner(s.app, s.config)
    assert runner.run_once()
    first_id, second_id = identifiers[first["id"]], identifiers[second["id"]]
    repo = FusekiRepository(s.config.fuseki_url)
    before = repo.read_graph(graph_iri(first_id, "schema"))
    assert before
    assert runner.run_once()
    with s.transaction() as uow:
        current = uow.session.execute(select(catalog)).mappings().one()
        states = dict(uow.session.execute(select(installations.c.release_id, installations.c.state)).all())
        assert current["active_release_id"] == first_id
        assert current["revision"] == baseline["revision"] + 1
        assert states[first_id] == "published" and states[second_id] == "failed"
    assert repo.read_graph(graph_iri(first_id, "schema")) == before
    assert repo.read_graph(graph_iri(second_id, "schema")) == ()


def test_pure_labels_are_low_risk_but_structural_metadata_bypass_fails(schema_case):
    from lzo.worker.runner import Runner
    s = schema_case
    draft = s.create()
    approval = s.approve(draft)
    assert s.publish(draft, approval).status_code == 202
    assert Runner(s.app, s.config).run_once()
    release = s.client.get("/api/v1/schema/releases").json()["items"][0]
    document = release["document"]
    document["classes"][0]["label"] = "Revised display name"
    label = s.create(document, release["id"])
    approval = s.submit(label)
    assert approval["risk"] == "low"
    assert s.publish(label, approval).status_code == 202
    assert Runner(s.app, s.config).run_once()
    document["classes"].append({"iri": "urn:New", "label": "New"})
    structural = s.create(document, release["id"])
    structural_approval = s.submit(structural)
    assert structural_approval["risk"] == "high"
    detail = s.client.get(f"/api/v1/approvals/{structural_approval['id']}").json()
    response = s.client.post("/api/v1/approvals/preview", headers=s.headers(), json={
        "kind": "metadata.update", "target_ids": [structural["id"]], "command": detail["command"]})
    assert response.status_code == 422
    assert s.publish(structural, structural_approval).status_code == 409


def test_schema_edit_only_upload_and_import_have_one_revision_source(schema_case):
    s = schema_case
    draft = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}]})
    raw = b'@prefix owl: <http://www.w3.org/2002/07/owl#>. <urn:Imported> a owl:Class.'
    response = s.client.post("/api/v1/artifacts", params={"resource_id": draft["id"], "filename": "schema.ttl"},
        content=raw, headers={**s.headers(1), "Content-Type": "text/turtle"})
    assert response.status_code == 201, response.text
    blob = response.json()
    body = {"blob": {k: blob[k] for k in ("sha256", "size_bytes", "media_type", "version_id")},
            "format": "turtle", "kind": "schema", "base_release_id": None, "target_draft_id": draft["id"]}
    # F04 evidence is a ledger revision of the draft; S02 must honor that head.
    latest = s.client.get(f"/api/v1/schema/drafts/{draft['id']}").json()
    assert latest["revision"] == 2
    assert s.client.post("/api/v1/schema/drafts/imports", json=body, headers=s.headers()).status_code == 428
    assert s.client.post("/api/v1/schema/drafts/imports", json={**body, "expected_revision": 1},
        headers=s.headers(2)).status_code == 422
    assert s.client.post("/api/v1/schema/drafts/imports", json=body, headers=s.headers(1)).status_code == 409
    imported = s.client.post("/api/v1/schema/drafts/imports", json=body, headers=s.headers(2))
    assert imported.status_code == 201, imported.text
    assert "urn:Imported" in {c["iri"] for c in imported.json()["document"]["classes"]}
    s.login("nobody")
    assert s.client.post("/api/v1/artifacts", params={"resource_id": draft["id"], "filename": "denied.ttl"},
        content=raw, headers={**s.headers(3), "Content-Type": "text/turtle"}).status_code == 403


def test_old_base_labels_require_review_for_current_active_structural_rollback(schema_case):
    from copy import deepcopy
    from lzo.worker.runner import Runner
    from lzo.ontology.models import catalog
    s = schema_case
    first = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}]})
    assert s.publish(first, s.approve(first)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    r1 = s.client.get("/api/v1/schema/releases").json()["items"][0]
    newer = deepcopy(r1["document"])
    newer["classes"].append({"iri": "urn:NewStructuralClass", "label": "New class"})
    newer["ui_metadata"] = {"fields": {"urn:value": {"read_only": True}}}
    second = s.create(newer, r1["id"])
    assert s.publish(second, s.approve(second)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow:
        r2 = uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
    old_labels = deepcopy(r1["document"])
    old_labels["classes"][0]["label"] = "Only an old-base label edit"
    rollback = s.create(old_labels, r1["id"])
    approval = s.submit(rollback)
    assert approval["risk"] == "high"
    impact = s.client.post(f"/api/v1/schema/drafts/{rollback['id']}/validate",
                          headers=s.headers(rollback["revision"])).json()
    assert impact["form_diff"]["removed_classes"] == ["urn:NewStructuralClass"]
    assert impact["permission_diff"]["changed"] is True
    assert s.publish(rollback, approval).status_code == 409
    with s.transaction() as uow:
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() == r2
    # A reviewed intentional rollback is supported; provenance remains R1.
    assert rollback["base_release_id"] == r1["id"]
    assert s.publish(rollback, s.approve(rollback)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow:
        final_id = uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
    final = s.client.get(f"/api/v1/schema/releases/{final_id}").json()
    assert final["state"] == "published"
    assert [value["iri"] for value in final["document"]["classes"]] == ["urn:Sensor"]


def test_baseless_release_does_not_inherit_previous_catalog_private_sources(schema_case):
    import io
    from dataclasses import asdict
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ontology.models import catalog, releases as release_rows
    from lzo.ontology.releases import get_execution_bundle, create_execution_bundle
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.worker.runner import Runner
    s = schema_case
    grant = s.grant("alice", "editor", scope="farm_b")
    s.grant("admin", "viewer", scope="farm_b")
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        store = store_for(uow)
        source = store.put(io.BytesIO(b"private Farm B schema source"), "text/plain")
        ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.ids_by_name["object_b"],
            source, filename="private-source.txt", metadata={"synthetic": True})
        uow.commit()
    first = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "dependencies": [{**asdict(source), "version_id": str(source.version_id)}]})
    assert s.publish(first, s.approve(first)).status_code == 202
    assert Runner(s.app, s.config).run_once()
    r1 = s.client.get("/api/v1/schema/releases").json()["items"][0]
    second = s.create({"classes": [{"iri": "urn:Sensor", "label": "Independent public Sensor"}]})
    assert second["base_release_id"] is None and second["document"]["dependencies"] == []
    approval = s.approve(second)
    assert str(s.ids_by_name["object_b"]) in approval["baseline"]
    job = s.publish(second, approval)
    assert job.status_code == 202
    assert Runner(s.app, s.config).run_once()
    with s.transaction() as uow, permission_context(uow):
        active_id = uow.session.execute(select(catalog.c.active_release_id)).scalar_one()
        rows = dict(uow.session.execute(select(release_rows.c.id, release_rows.c.bundle_id)).all())
        bundle = get_execution_bundle(s.actor("alice"), rows[active_id])
        extra = create_execution_bundle(uow, s.actor("alice"), active_id, None,
            bundle.authority_version_id, bundle.ruling_policy_version_id, bundle.inference_version_id,
            bundle.shapes_digest)
        uow.commit()
    s.login("nobody")
    response = s.client.get(f"/api/v1/schema/releases/{active_id}")
    assert response.status_code == 200, response.text
    r2 = response.json()
    assert {row["id"] for row in s.client.get("/api/v1/schema/releases").json()["items"]} == {r2["id"]}
    assert s.client.get(f"/api/v1/schema/releases/{active_id}/form-schema",
                        params={"class_iri": "urn:Sensor"}).status_code == 200
    for blob in r2["artifacts"]:
        assert s.client.get(f"/api/v1/artifacts/{blob['version_id']}").status_code == 200
        assert s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content").status_code == 200
    assert s.client.get(f"/api/v1/schema/releases/{r1['id']}").status_code == 404
    for blob in r1["artifacts"]:
        assert s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content").status_code == 404
    with s.transaction() as uow, permission_context(uow):
        for bundle_id in (rows[active_id], extra.id):
            assert get_execution_bundle(s.actor("nobody"), bundle_id).schema_release_id == active_id
        with pytest.raises(ApiError, match="NOT_FOUND"):
            get_execution_bundle(s.actor("nobody"), rows[UUID(r1["id"])])
    for url in (f"/api/v1/approvals/{approval['id']}", f"/api/v1/jobs/{job.json()['id']}",
                f"/api/v1/schema/releases/{active_id}/diff?base_release_id={r1['id']}"):
        denied = s.client.get(url)
        assert denied.status_code == 404
        assert str(source.version_id) not in denied.text and str(s.ids_by_name["object_b"]) not in denied.text
    # Revoke only the original source; the independent R2 must remain readable.
    s.revoke(grant)
    s.login()
    assert s.client.get(f"/api/v1/schema/releases/{active_id}").status_code == 200
    assert s.client.get(f"/api/v1/schema/releases/{r1['id']}").status_code == 404


def test_malformed_ui_metadata_http_rejects_before_persistence(schema_case):
    from sqlalchemy import func
    from lzo.governance.models import revisions
    s = schema_case
    draft = s.create({"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "shapes": '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:S> sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:p>].'})
    with s.transaction() as uow:
        before = uow.session.execute(select(func.count()).select_from(revisions)).scalar_one()
    for fields in (None, [], "invalid", {"urn:p": None}, {"urn:p": []}, {"urn:p": "invalid"}):
        document = {**draft["document"], "ui_metadata": {"fields": fields}}
        for response in (
            s.client.post("/api/v1/schema/drafts", headers=s.headers(), json={"document": document}),
            s.client.patch(f"/api/v1/schema/drafts/{draft['id']}", headers=s.headers(draft["revision"]),
                           json={"document": document}),
        ):
            assert response.status_code == 422, response.text
        with s.transaction() as uow:
            assert uow.session.execute(select(func.count()).select_from(revisions)).scalar_one() == before
    assert s.client.get(f"/api/v1/schema/drafts/{draft['id']}").json() == draft


def test_shape_impact_reports_violations_and_requires_actual_migration(schema_case):
    from lzo.semantic.projection import ProjectionContent, ProjectionService, register_projection
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.semantic.rdf import Triple, RdfTerm
    from lzo.governance.revisions import append_revision
    from lzo.worker.runner import Runner
    s = schema_case
    s.grant("alice", "editor", scope="farm_a")
    s.grant("admin", "viewer", scope="farm_a")
    resource = s.ids_by_name["object_a"]
    kind = "test.s02.migration." + uuid4().hex
    def resolver(uow, row):
        triples = [Triple("urn:instance", "http://www.w3.org/1999/02/22-rdf-syntax-ns#type", RdfTerm("iri", "urn:Sensor"))]
        if row["payload"]["migrated"]:
            triples.append(Triple("urn:instance", "urn:value",
                RdfTerm("literal", "01", "http://www.w3.org/2001/XMLSchema#integer")))
        return ProjectionContent({"accepted": tuple(triples)}, (resource,))
    register_projection(kind, resolver, version="test-v1", dirty_kind="facts")
    def input_revision(expected, migrated):
        with s.transaction() as uow:
            ref = append_revision(uow, s.actor("alice"), resource, expected, {"migrated": migrated}, kind)
            ProjectionService(uow, FusekiRepository(s.config.fuseki_url)).schedule(s.actor("alice"), ref)
            uow.commit()
    input_revision(1, False)
    document = {"classes": [{"iri": "urn:Sensor", "label": "Sensor"}],
        "properties": [{"iri": "urn:value", "label": "Value", "datatype_iri": "http://www.w3.org/2001/XMLSchema#integer",
                        "owner_policy_iri": "urn:manual"}],
        "shapes": '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:shape> sh:targetClass <urn:Sensor>; sh:property [sh:path <urn:value>; sh:minCount 1; sh:maxCount 1].'}
    draft = s.create(document)
    preview = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/validate", headers=s.headers(1))
    assert preview.status_code == 200, preview.text
    assert preview.json()["migration_required"] is True
    assert preview.json()["violations"][0]["focus_iri"] == "urn:instance"
    denied = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/submit", headers=s.headers(1),
        json={"impact_digest": preview.json()["digest"]})
    assert denied.status_code == 422 and denied.json()["code"] == "MIGRATION_PLAN_REQUIRED"
    document["ui_metadata"] = {"migration_plan": {"description": "Populate values", "validation": "SHACL", "rollback": "Retain old release"}}
    draft = s.client.patch(f"/api/v1/schema/drafts/{draft['id']}", headers=s.headers(1), json={"document": document}).json()
    preview = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/validate", headers=s.headers(2)).json()
    denied = s.client.post(f"/api/v1/schema/drafts/{draft['id']}/submit", headers=s.headers(2),
        json={"impact_digest": preview["digest"]})
    assert denied.status_code == 422 and denied.json()["code"] == "MIGRATION_NOT_APPLIED"
    input_revision(2, True)
    approval = s.approve(draft)
    assert s.publish(draft, approval).status_code == 202
    # Drain the real data projections ahead of schema publication.
    runner = Runner(s.app, s.config)
    for _ in range(3):
        assert runner.run_once()
    release = s.client.get("/api/v1/schema/releases").json()["items"][0]
    assert release["state"] == "published"
    form = s.client.get(f"/api/v1/schema/releases/{release['id']}/form-schema",
        params={"class_iri": "urn:Sensor"}).json()
    assert form["fields"][0]["iri"] == "urn:value" and form["fields"][0]["required"]


def test_unavailable_install_keeps_active_pointer_and_real_retry_recovers(schema_case):
    from lzo.ontology.models import installations, catalog
    from lzo.governance.models import revisions
    from lzo.contracts import RevisionRef
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.worker.runner import Runner
    s = schema_case
    draft = s.create()
    approval = s.approve(draft)
    assert s.publish(draft, approval).status_code == 202
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        row = uow.session.execute(select(revisions).join(installations,
            installations.c.event_id == revisions.c.event_id)).mappings().one()
        ref = RevisionRef(row["resource_id"], row["revision"], row["event_id"])
        failed = ProjectionService(uow, FusekiRepository("http://127.0.0.1:1/lzo", timeout_seconds=0.2),
                                    actor=s.actor("alice")).project_revision(ref)
        assert failed.state == "failed"
        uow.commit()
    assert s.client.get("/api/v1/schema/releases").json()["items"][0]["state"] == "failed"
    with s.transaction() as uow:
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() is None
    assert Runner(s.app, s.config).run_once()
    assert s.client.get("/api/v1/schema/releases").json()["items"][0]["state"] == "published"


def test_unsupported_import_retains_original_and_reports_source_location(schema_case):
    s = schema_case
    draft = s.create()
    raw = b'@prefix owl: <http://www.w3.org/2002/07/owl#>. <urn:Original> owl:equivalentClass <urn:Other>.'
    response = s.client.post("/api/v1/artifacts", params={"resource_id": draft["id"], "filename": "unsupported.ttl"},
        content=raw, headers={**s.headers(1), "Content-Type": "text/turtle"})
    assert response.status_code == 201
    value = response.json()
    blob = {k: value[k] for k in ("sha256", "size_bytes", "media_type", "version_id")}
    response = s.client.post("/api/v1/schema/drafts/imports", headers=s.headers(2),
        json={"blob": blob, "kind": "schema", "format": "turtle", "target_draft_id": draft["id"]})
    assert response.status_code == 422
    assert response.json()["details"]["support"]["locations"][0]["subject"] == "urn:Original"
    assert s.client.get(f"/api/v1/artifacts/{blob['version_id']}/content").content == raw
    assert s.client.get(f"/api/v1/schema/drafts/{draft['id']}").json()["revision"] == 2


def test_ordinary_artifact_parent_resource_grant_still_suffices(scope_scenario, settings, tmp_path):
    import io
    from lzo.artifacts.service import ArtifactService
    from lzo.artifacts.store import ArtifactStore
    s = scope_scenario
    s.grant("alice", "editor", resource="object_a")
    store = ArtifactStore(tmp_path / "ordinary", max_upload_bytes=1024)
    blob = store.put(io.BytesIO(b"ordinary evidence"), "text/plain")
    with s.transaction() as uow:
        service = ArtifactService(uow, store, settings)
        result = service.register_upload(s.actor("alice"), s.ids_by_name["object_a"], blob,
            filename="ordinary.txt", metadata={})
        assert service.get(s.actor("alice"), result.version_id).version_id == blob.version_id
        with service.content(s.actor("alice"), result.version_id) as stream:
            assert stream.read() == b"ordinary evidence"
