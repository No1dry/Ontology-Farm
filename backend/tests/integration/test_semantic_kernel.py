"""S06 versioned packages and real API/PG/Fuseki/worker kernel acceptance."""
from importlib.util import find_spec
from pathlib import Path
import json
import hashlib
import time
from uuid import UUID, uuid4, uuid5
from dataclasses import dataclass
from copy import deepcopy

import pytest

ROOT = Path(__file__).resolve().parents[3]
PACKAGES = ROOT / "packages/ontology"
NS = "https://w3id.org/lzo/ontology/"


def bootstrap_module():
    assert find_spec("lzo.ontology.bootstrap"), "S06 governed bootstrap is missing"
    from lzo.ontology import bootstrap
    return bootstrap


def test_versioned_package_inventory_and_public_fixture_contracts():
    from lzo.semantic.schemas import CreateObjectInput, FactPatch, GeometryInput
    index_path = PACKAGES / "releases/synthetic-v1.json"
    assert index_path.is_file(), "S06 versioned package index is missing"
    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["namespace"] == NS
    assert index["data_nature"] == "synthetic"
    for item in index["artifacts"]:
        data = (PACKAGES / item["path"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == item["sha256"]
        assert len(data) == item["size_bytes"]
    shared = []
    for scene in ("field", "pond", "orchard"):
        fixture = json.loads((ROOT / f"backend/tests/fixtures/semantic/{scene}.json").read_text())
        assert fixture["seed"] == 20261020 and fixture["data_nature"] == "synthetic"
        objects = [CreateObjectInput.model_validate(value) for value in fixture["objects"]]
        assert all(value.data_nature == "synthetic" for value in objects)
        for patch in fixture["patches"]:
            FactPatch.model_validate(patch)
        GeometryInput.model_validate(fixture["geometry"])
        shared.append(fixture["shared_device_id"])
        assert len({cls for obj in objects for cls in obj.class_iris if cls.startswith(NS) and cls.endswith(("Sensor", "Camera", "Pump"))}) >= 2
    assert len(set(shared)) == 1


def test_bootstrap_requires_normal_approval_and_replays_actual_job(schema_case):
    module = bootstrap_module()
    from lzo.errors import ApiError
    from lzo.worker.runner import Runner
    s = schema_case
    prepared = prepare(s)
    with pytest.raises(ApiError):
        execute(s, prepared.manifest, uuid4())
    approval = s.approve(prepared.draft.model_dump(mode="json"))
    job = execute(s, prepared.manifest, UUID(approval["id"]))
    assert job.status == "queued"
    assert Runner(s.app, s.config).run_once()
    replay = execute(s, prepared.manifest, UUID(approval["id"]))
    assert replay.id == job.id and replay.status == "succeeded"
    retried = prepare(s)
    assert retried.draft.id == prepared.draft.id
    assert retried.manifest == prepared.manifest


def prepare(s, *, index=None, register_only=False, artifact_owner=None):
    import io
    from lzo.auth.permissions import permission_context
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    module = bootstrap_module()
    s.package_holder = artifact_owner or UUID(s.create({"classes": []})["id"])
    index = index or json.loads((PACKAGES / "releases/synthetic-v1.json").read_text())
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        store = store_for(uow)
        def register(data, media, filename):
            blob = store.put(io.BytesIO(data), media)
            ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.package_holder,
                blob, filename=filename, metadata={"data_nature": "synthetic"})
            return blob
        artifacts = {i["path"]: register((PACKAGES / i["path"]).read_bytes(), i["media_type"], Path(i["path"]).name)
                     for i in index["artifacts"]}
        index_blob = register(json.dumps(index, sort_keys=True).encode(), "application/json", "synthetic-v1.json")
        descriptor = module.BootstrapPreparation(index_blob, artifacts)
        result = descriptor if register_only else module.prepare_bootstrap(uow, s.actor("alice"), descriptor)
        uow.commit()
        return result


def test_bootstrap_reservation_survives_metadata_edit_and_forgery(schema_case):
    from lzo.errors import ApiError
    s = schema_case
    prepared = prepare(s)
    document = prepared.draft.document.model_dump(mode="json")
    # Generic schema metadata cannot create a trusted reservation.
    forged = s.create(document)
    assert forged["id"] != str(prepared.draft.id)
    document["ui_metadata"] = {}
    response = s.client.patch(f"/api/v1/schema/drafts/{prepared.draft.id}", headers=s.headers(prepared.draft.revision),
        json={"document": document})
    assert response.status_code == 200, response.text
    with pytest.raises(ApiError) as stale:
        prepare(s)
    assert stale.value.code == "BOOTSTRAP_DRAFT_CHANGED"
    index = json.loads((PACKAGES / "releases/synthetic-v1.json").read_text())
    index["artifacts"][0]["role"] = "shapes"
    with pytest.raises(ApiError) as conflict:
        prepare(s, index=index)
    assert conflict.value.code == "BOOTSTRAP_VERSION_CONFLICT"


def test_bootstrap_concurrent_first_preparation_is_one_immutable_association(schema_case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from lzo.auth.permissions import permission_context
    from lzo.governance.models import revisions
    from sqlalchemy import select, func
    s = schema_case
    descriptor = prepare(s, register_only=True)
    barrier = Barrier(2)
    def attempt():
        actor = s.actor("alice")
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            barrier.wait(timeout=10)
            value = bootstrap_module().prepare_bootstrap(uow, actor, descriptor)
            uow.commit()
            return value
    with ThreadPoolExecutor(max_workers=2) as pool:
        a, b = list(pool.map(lambda _: attempt(), range(2)))
    assert a.manifest == b.manifest and a.draft.id == b.draft.id and a.association_id == b.association_id
    with s.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(revisions).where(
            revisions.c.event_type == "bootstrap.prepared")).scalar_one() == 1


def test_bootstrap_noop_draft_edit_still_invalidates_prepared_revision(schema_case):
    from lzo.errors import ApiError
    s = schema_case
    prepared = prepare(s)
    response = s.client.patch(f"/api/v1/schema/drafts/{prepared.draft.id}", headers=s.headers(prepared.draft.revision),
        json={"document": prepared.draft.document.model_dump(mode="json")})
    assert response.status_code == 200
    with pytest.raises(ApiError) as stale:
        prepare(s)
    assert stale.value.code == "BOOTSTRAP_DRAFT_CHANGED"


def test_bootstrap_rejects_missing_extra_duplicate_and_unsafe_inventory(schema_case):
    import io
    from lzo.errors import ApiError
    from lzo.auth.permissions import permission_context
    from lzo.ontology.drafts import store_for
    from lzo.artifacts.service import ArtifactService
    s = schema_case
    module = bootstrap_module()
    descriptor = prepare(s, register_only=True)
    def attempt(index, artifacts):
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            store = store_for(uow)
            blob = store.put(io.BytesIO(json.dumps(index).encode()), "application/json")
            ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.package_holder,
                blob, filename="invalid-index.json", metadata={"data_nature": "synthetic"})
            with pytest.raises(ApiError) as invalid:
                module.prepare_bootstrap(uow, s.actor("alice"), module.BootstrapPreparation(blob, artifacts))
            assert invalid.value.code == "BOOTSTRAP_INVALID"
    original = json.loads((PACKAGES / "releases/synthetic-v1.json").read_text())
    missing = dict(descriptor.artifacts)
    missing.pop(next(iter(missing)))
    attempt(original, missing)
    attempt(original, descriptor.artifacts | {"extra.ttl": next(iter(descriptor.artifacts.values()))})
    duplicate = deepcopy(original)
    duplicate["artifacts"].append(duplicate["artifacts"][0])
    attempt(duplicate, descriptor.artifacts)
    for path in ("../outside.ttl", "/absolute.ttl", "core\\unsafe.ttl"):
        unsafe = deepcopy(original)
        unsafe["artifacts"][0]["path"] = path
        attempt(unsafe, descriptor.artifacts)


def test_bootstrap_changed_input_bytes_and_current_rights_fail_closed(schema_case):
    from lzo.errors import ApiError
    from lzo.auth.permissions import permission_context
    from lzo.ontology.drafts import store_for
    s = schema_case
    prepared = prepare(s)
    approval = s.approve(prepared.draft.model_dump(mode="json"))
    with pytest.raises(ApiError):
        execute(s, prepared.manifest, UUID(approval["id"]), name="nobody")
    blob = prepared.manifest.package_artifacts[1]
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        path = store_for(uow)._path(blob.sha256)
    original = path.read_bytes()
    path.write_bytes(b"X" + original[1:])
    try:
        with pytest.raises(ApiError) as corrupt:
            execute(s, prepared.manifest, UUID(approval["id"]))
        assert corrupt.value.code == "BOOTSTRAP_INVALID"
    finally:
        path.write_bytes(original)


def test_explicit_reprepare_preserves_reservation_and_requires_fresh_approval(schema_case):
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.worker.runner import Runner
    s = schema_case
    module = bootstrap_module()
    original = prepare(s)
    old_approval = s.approve(original.draft.model_dump(mode="json"))
    response = s.client.patch(f"/api/v1/schema/drafts/{original.draft.id}", headers=s.headers(original.draft.revision),
        json={"document": original.draft.document.model_dump(mode="json")})
    assert response.status_code == 200
    with pytest.raises(ApiError):
        execute(s, original.manifest, UUID(old_approval["id"]))
    assert hasattr(module, "reprepare_bootstrap"), "Explicit governed pre-install recovery is missing"
    def recover(expected):
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = s.config
            result = module.reprepare_bootstrap(uow, s.actor("alice"), original.manifest, expected)
            uow.commit()
            return result
    with pytest.raises(ApiError) as stale:
        recover(0)
    assert stale.value.code == "REVISION_CONFLICT"
    recovered = recover(original.association_revision)
    assert recovered.association_id == original.association_id
    assert recovered.association_revision == original.association_revision + 1
    assert recovered.manifest == original.manifest and recovered.draft.id != original.draft.id
    with pytest.raises(ApiError) as stale:
        recover(original.association_revision)
    assert stale.value.code == "REVISION_CONFLICT"
    with pytest.raises(ApiError):
        execute(s, recovered.manifest, UUID(old_approval["id"]))
    approved = s.approve(recovered.draft.model_dump(mode="json"))
    job = execute(s, recovered.manifest, UUID(approved["id"]))
    with pytest.raises(ApiError) as installed:
        recover(recovered.association_revision)
    assert installed.value.code == "BOOTSTRAP_INSTALLATION_EXISTS"
    assert Runner(s.app, s.config).run_once()
    replay = execute(s, recovered.manifest, UUID(approved["id"]))
    assert replay.id == job.id and replay.status == "succeeded"
    print(f"S06 real schema JobRef id={job.id} status={replay.status} association={recovered.association_id} revision={recovered.association_revision}")


def execute(s, manifest, approval_id, name="alice"):
    from lzo.auth.permissions import permission_context
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        result = bootstrap_module().bootstrap_packages(uow, s.actor(name), manifest, approval_id)
        uow.commit()
        return result


@dataclass
class KernelSceneResult:
    data_nature: str
    validation: object
    same_object_in_registry_graph_geometry: bool
    inferred_device_type: bool
    proof_has_rule_and_source_versions: bool
    unauthorized_neighbor_count: int
    scene: str
    support: dict
    conclusion: object
    component_id: UUID
    original_proof: object


@pytest.fixture
def kernel_case(schema_case, monkeypatch, tmp_path):
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.semantic import inference
    from lzo.semantic.projection import register_nonsemantic, ProjectionService
    from lzo.worker.runner import Runner
    from lzo.ontology.releases import current_execution_bundle
    from sqlalchemy import select
    s = schema_case
    diagnostics = []
    finish = Runner.finish
    def observed_finish(runner, ctx, error=None):
        if error is not None:
            import traceback
            diagnostics.append({'kind': ctx._row['kind'], 'job_id': str(ctx.job_id),
                'type': type(error).__name__, 'code': getattr(error, 'code', None),
                'traceback': ''.join(traceback.format_exception(error))})
            (tmp_path/'kernel-worker-diagnostics.json').write_text(json.dumps(diagnostics, indent=2))
        return finish(runner, ctx, error)
    monkeypatch.setattr(Runner, 'finish', observed_finish)
    # Explicit S06 workstation correctness budget; default 10s request / 15s
    # server timed out on this complete package. D04 owns latency acceptance.
    s.config = s.config.model_copy(update={"query_timeout_ms": 60000})
    s.client.app.state.settings = s.config
    s.grant("alice", "editor", scope="farm_a")
    for event in ("role.fixture_installed", "grant.fixture_installed", "object.fixture_created"):
        register_nonsemantic(event, "test-inference-fixture-v1")
    prepared = prepare(s)
    approval = s.approve(prepared.draft.model_dump(mode="json"))
    s.bootstrap_job = execute(s, prepared.manifest, UUID(approval["id"]))
    s.prepared = prepared
    def drain():
        runner = Runner(s.app, s.config)
        for _ in range(100):
            if not runner.run_once():
                break
        else:
            pytest.fail("Kernel queue did not drain")
        from lzo.worker.models import jobs
        with s.transaction() as uow:
            failed = uow.session.execute(select(jobs.c.kind, jobs.c.status).where(jobs.c.status == "failed")).all()
            if failed:
                from sqlalchemy import text
                rows = list(uow.session.execute(select(jobs).where(jobs.c.status == 'failed')).mappings())
                authority = []
                for job in rows:
                    try:
                        runner.authorize(uow, job)
                        state = 'current'
                    except Exception as error:
                        state = getattr(error, 'code', type(error).__name__)
                    authority.append({'job_id': str(job['id']), 'kind': job['kind'],
                        'error_code': job['error_code'], 'current_authority': state, 'payload': job['payload']})
                deliveries = [dict(row) for row in uow.session.execute(text(
                    'SELECT event_id,entity_id,aggregate_revision,target,status,attempts,error FROM store_deliveries')).mappings()]
                (tmp_path/'kernel-failed-jobs.json').write_text(json.dumps(
                    {'jobs': authority, 'deliveries': deliveries}, indent=2, default=str))
            assert not failed, (failed, [(item['kind'], item['type'], item['code']) for item in diagnostics])
            assert set(uow.session.execute(select(jobs.c.kind)).scalars()) <= {
                "rdf.project", "inference.compute", "inference.dispatch", "facts.validity"}
    s.drain = drain
    drain()
    with s.transaction() as uow, permission_context(uow):
        change = inference.preview_inference_profile(uow, s.actor("alice"))
        approval = ApprovalService(uow).submit(s.actor("alice"), change.kind,
            list(change.target_ids), change.change_digest, change.baseline, {"command": change.command})
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        ApprovalService(uow).decide(s.actor("admin"), approval.id, "approve", "Independent kernel profile review")
        uow.commit()
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        s.bundle = inference.activate_inference_profile(uow, s.actor("alice"), approval.id)
        uow.commit()
    drain()
    def recompute():
        from lzo.semantic.inference_jobs import recompute_inference
        drain()
        with s.transaction() as uow, permission_context(uow):
            uow.ontology_settings = uow.job_settings = s.config
            watermark = ProjectionService(uow, None).advance_contiguous()
            s.bundle = current_execution_bundle(uow, s.actor("alice"))
            result = recompute_inference(s.actor("alice"), s.bundle, watermark)
            uow.commit()
        drain()
        return result
    s.recompute = recompute
    def query(query):
        started = time.monotonic()
        result = s.client.post("/api/v1/query/sparql", headers=s.headers(), json={"query": query, "timeout_ms": 60000})
        print(f"S06 query duration_seconds={time.monotonic()-started:.3f} status={result.status_code} budget_ms=60000")
        assert result.status_code == 200, result.text
        body = result.json()
        print(f"S06 query input_watermark={body['input_watermark']}")
        return body
    s.query = query
    s.loaded = set()
    # Isolated PG schemas share the native Fuseki endpoint. Namespace fixture
    # UUIDs per test while preserving shared identity across scenes in a test.
    fixture_namespace = uuid4()
    def fixture_for(scene):
        raw = (ROOT / f"backend/tests/fixtures/semantic/{scene}.json").read_text()
        import re
        raw = re.sub(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                     lambda m: str(uuid5(fixture_namespace, m.group())), raw)
        return json.loads(raw)
    s.scene_fixture = fixture_for
    def create(value):
        value = deepcopy(value)
        value["owner_scope_id"] = str(s.scopes["farm_a"])
        value["related_scope_ids"] = [str(s.scopes["farm_b"])]
        response = s.client.post("/api/v1/objects", headers=s.headers(),
            json={"create": value, "bundle_id": str(s.bundle.id)})
        assert response.status_code == 201, response.text
        s.loaded.add(value["resource_id"])
    s.create_kernel_object = create
    def patch(value):
        value = deepcopy(value)
        revision = s.client.get(f"/api/v1/objects/{value['resource_id']}").json()["revision"]
        value["expected_revision"] = revision
        response = s.client.patch(f"/api/v1/objects/{value['resource_id']}", headers=s.headers(revision),
            json={"patch": value, "bundle_id": str(s.bundle.id)})
        assert response.status_code == 200, response.text
    s.patch_kernel = patch
    def load_scene(scene):
        from lzo.semantic.rdf import Triple, RdfTerm
        from lzo.semantic.inference import derived_assertion_id
        from lzo.semantic.proofs import historical_proof
        from lzo.semantic.inference_jobs import components
        fixture = fixture_for(scene)
        s.login()
        for value in fixture["objects"]:
            if value["resource_id"] not in s.loaded:
                create(value)
        for value in fixture["patches"]:
            patch(value)
        geo = fixture["geometry"]
        response = s.client.put(f"/api/v1/geometry/{geo['resource_id']}", headers=s.headers(1),
            json={"geometry": geo, "bundle_id": str(s.bundle.id)})
        assert response.status_code == 200, response.text
        zone, sensor = fixture["objects"][2], fixture["objects"][4]
        farm = deepcopy(zone)
        farm.update(resource_id=str(uuid4()), class_iris=[NS+"Farm"], display_name="Synthetic farm", assertions=[])
        create(farm)
        def part(source, target):
            value = {"assertion_id": str(uuid4()), "resource_id": source, "predicate_iri": NS+"partOf",
                "object": {"kind": "iri", "value": "urn:lzo:object:"+target}, "state": "accepted",
                "data_nature": "synthetic", "valid_from": "2026-01-01T00:00:00Z",
                "support": {"support_id": str(uuid4()), "source_version": "synthetic-v1", "raw_value": {"seed": 20261020}}}
            patch({"resource_id": source, "additions": [value]})
            return value
        support = part(sensor["resource_id"], zone["resource_id"])
        part(zone["resource_id"], farm["resource_id"])
        recompute()
        registry = s.client.get(f"/api/v1/objects/{zone['resource_id']}").json()
        geometry = s.client.get(f"/api/v1/geometry/{zone['resource_id']}").json()["features"][0]
        identity = query(f'ASK {{ <{registry["iri"]}> a <{zone["class_iris"][0]}> }}')["boolean"]
        inferred = query(f'ASK {{ <urn:lzo:object:{sensor["resource_id"]}> a <{NS}Device> }}')["boolean"]
        conclusion = Triple("urn:lzo:object:"+sensor["resource_id"], NS+"partOf", RdfTerm("iri", "urn:lzo:object:"+farm["resource_id"]))
        assertion_id = derived_assertion_id(conclusion)
        response = s.client.get(f"/api/v1/graph/proofs/{assertion_id}")
        assert response.status_code == 200, response.text
        public_proof = response.json()
        assertion_response = s.client.get(f"/api/v1/objects/{sensor['resource_id']}/assertions")
        assert assertion_response.status_code == 200, assertion_response.text
        selected_source = next(a for a in assertion_response.json()["items"] if a["id"] == support["assertion_id"])
        with s.transaction() as uow, permission_context(uow):
            component = next(row["id"] for row in uow.session.execute(select(components)).mappings()
                             if str(assertion_id) in row["payload"]["assertion_ids"])
            proof = historical_proof(uow, s.actor("alice"), assertion_id, component)
        s.ids_by_name["kernel-visible"] = UUID(sensor["resource_id"])
        grant = s.grant("nobody", "viewer", resource="kernel-visible")
        s.login("nobody")
        neighborhood = s.client.get("/api/v1/graph/neighborhood", params={"resource_id": sensor["resource_id"], "hops": 2})
        assert neighborhood.status_code == 200, neighborhood.text
        hidden = {zone["resource_id"], farm["resource_id"], fixture["objects"][0]["resource_id"]}
        leaked = sum(n["id"] in hidden for n in neighborhood.json()["nodes"])
        s.login()
        from lzo.semantic.validation import ValidationReport
        from pydantic import TypeAdapter
        return KernelSceneResult(registry["data_nature"], TypeAdapter(ValidationReport).validate_python(registry["validation_report"]),
            identity and str(geometry["id"]) == registry["id"], inferred,
            bool(public_proof["alternatives"]) and all(p.rule_iri and p.schema_axiom_iris and p.bundle_id == s.bundle.id for p in proof.alternatives)
                and support["assertion_id"] in json.dumps(public_proof)
                and any(p["source_version"] == "synthetic-v1" for p in selected_source["supports"]),
            leaked, scene, support, conclusion, component, proof)
    s.load_scene = load_scene
    def withdraw(result):
        patch({"resource_id": result.support["resource_id"], "withdraw_support_ids": [result.support["support"]["support_id"]]})
        recompute()
    s.withdraw_last_support = withdraw
    def current(result):
        t = result.conclusion
        return query(f'ASK {{ <{t.subject_iri}> <{t.predicate_iri}> <{t.object.value}> }}')["boolean"]
    s.current_inference = current
    def history(result):
        from lzo.semantic.proofs import historical_proof
        from lzo.semantic.inference import derived_assertion_id
        with s.transaction() as uow, permission_context(uow):
            return historical_proof(uow, s.actor("alice"), derived_assertion_id(result.conclusion), result.component_id) == result.original_proof
    s.history_contains_original_proof = history
    return s


@pytest.mark.parametrize("scene", ["field", "pond", "orchard"])
def test_scene_semantic_kernel(scene, kernel_case):
    result = kernel_case.load_scene(scene)
    assert result.data_nature == "synthetic"
    assert result.validation.conforms is True
    assert result.same_object_in_registry_graph_geometry is True
    assert result.inferred_device_type is True
    assert result.proof_has_rule_and_source_versions is True
    assert result.unauthorized_neighbor_count == 0
    assert kernel_case.current_inference(result) is True
    kernel_case.withdraw_last_support(result)
    assert kernel_case.current_inference(result) is False
    assert kernel_case.history_contains_original_proof(result) is True


def test_shared_identity_and_dynamic_extension(kernel_case):
    s = kernel_case
    # Only this slow two-scene correctness case uses the measured acceptance
    # profile. Original120s batches exhausted at about162s; production30/60
    # defaults remain unchanged and are not qualified by this larger budget.
    s.config = s.config.model_copy(update={'job_batch_timeout_seconds': 240, 'job_lease_seconds': 300})
    s.client.app.state.settings = s.config
    s.load_scene("field")
    s.load_scene("pond")
    field = s.scene_fixture("field")
    pond = s.scene_fixture("pond")
    before = s.client.get(f"/api/v1/objects/{field['shared_device_id']}").json()
    assert before["id"] == pond["shared_device_id"]
    assert len(before["properties"][NS+"managedBy"]) == 1
    assert len(before["properties"][NS+"serves"]) == 2
    document = s.prepared.draft.document.model_dump(mode="json")
    document["ui_metadata"] = {"migration_plan": {
        "description": "Add optional spectralBand only on new SpectralSensor; preserve existing scene instances.",
        "validation": "Run authoritative-data SHACL preview before independent review and validate the new sensor after publication.",
        "rollback": "Retain the previous release and device identities; withdraw new-class usage before choosing the previous schema."}}
    document["classes"].append({"iri": NS+"SpectralSensor", "label": "Spectral sensor", "parents": [NS+"Sensor"]})
    document["properties"].append({"iri": NS+"spectralBand", "label": "Spectral band",
        "datatype_iri": "http://www.w3.org/2001/XMLSchema#string", "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
    document["shapes"] += f'\n<{NS}SpectralShape> a <http://www.w3.org/ns/shacl#NodeShape>; <http://www.w3.org/ns/shacl#targetClass> <{NS}SpectralSensor>; <http://www.w3.org/ns/shacl#property> [ <http://www.w3.org/ns/shacl#path> <{NS}spectralBand>; <http://www.w3.org/ns/shacl#maxCount> 1 ].'
    # Independent schema reviewers must also see the current impact data.
    s.grant("admin", "viewer", scope="farm_a")
    draft = s.create(document, base=str(s.bundle.schema_release_id))
    approval = s.approve(draft)
    assert s.publish(draft, approval).status_code == 202
    s.recompute()
    form = s.client.get(f"/api/v1/schema/releases/{s.bundle.schema_release_id}/form-schema", params={"class_iri": NS+"SpectralSensor"})
    assert form.status_code == 200, form.text
    assert NS+"spectralBand" in {field["iri"] for field in form.json()["fields"]}
    value = deepcopy(field["objects"][4])
    identifier = str(uuid4())
    value.update(resource_id=identifier, class_iris=[NS+"SpectralSensor"], display_name="Published dynamic sensor")
    for assertion in value["assertions"]:
        assertion.update(resource_id=identifier, assertion_id=str(uuid4()))
        assertion["support"]["support_id"] = str(uuid4())
    s.create_kernel_object(value)
    after = s.client.get(f"/api/v1/objects/{pond['shared_device_id']}").json()
    assert after["id"] == before["id"] and after["owner_scope_id"] == before["owner_scope_id"]
    assert len(after["properties"][NS+"managedBy"]) == 1


def test_package_seven_device_families_and_inherited_conflict_rejection(schema_case):
    s = schema_case
    prepared = prepare(s)
    document = prepared.draft.document.model_dump(mode="json")
    by_iri = {item["iri"]: item for item in document["classes"]}
    for family in ("Sensor", "Camera", "WaterGate", "Pump", "IrrigationEquipment", "AgriculturalMachine", "Drone"):
        assert NS+"Device" in by_iri[NS+family]["parents"]
    document["classes"].extend([
        {"iri": NS+"SingleChannel", "label": "One channel", "parents": [NS+"Device"]},
        {"iri": NS+"DualChannel", "label": "Two channels", "parents": [NS+"Device"]}])
    document["properties"].append({"iri": NS+"channel", "label": "Channel",
        "datatype_iri": "http://www.w3.org/2001/XMLSchema#string", "owner_policy_iri": "urn:lzo:authority:ontology_owned"})
    for cls, count in (("SingleChannel", 1), ("DualChannel", 2)):
        document["shapes"] += f'\n<{NS}{cls}Shape> a <http://www.w3.org/ns/shacl#NodeShape>; <http://www.w3.org/ns/shacl#targetClass> <{NS}{cls}>; <http://www.w3.org/ns/shacl#property> [ <http://www.w3.org/ns/shacl#path> <{NS}channel>; <http://www.w3.org/ns/shacl#minCount> {count}; <http://www.w3.org/ns/shacl#maxCount> {count} ].'
    parents = s.client.post("/api/v1/schema/drafts", headers=s.headers(), json={"document": document})
    assert parents.status_code == 201, parents.text
    document["classes"].append({"iri": NS+"ConflictDevice", "label": "Conflict",
        "parents": [NS+"SingleChannel", NS+"DualChannel"]})
    response = s.client.post("/api/v1/schema/drafts", headers=s.headers(), json={"document": document})
    assert response.status_code == 422, response.text


def test_bootstrap_failed_publication_restarts_same_job(schema_case):
    from sqlalchemy import select
    from lzo.ontology.models import installations, catalog
    from lzo.governance.models import revisions
    from lzo.contracts import RevisionRef
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.worker.runner import Runner
    s = schema_case
    prepared = prepare(s)
    approval = s.approve(prepared.draft.model_dump(mode="json"))
    job = execute(s, prepared.manifest, UUID(approval["id"]))
    with s.transaction() as uow:
        uow.ontology_settings = s.config
        row = uow.session.execute(select(revisions).join(installations,
            installations.c.event_id == revisions.c.event_id)).mappings().one()
        failed = ProjectionService(uow, FusekiRepository("http://127.0.0.1:1/lzo", timeout_seconds=0.2),
            actor=s.actor("alice")).project_revision(RevisionRef(row["resource_id"], row["revision"], row["event_id"]))
        assert failed.state == "failed"
        assert uow.session.execute(select(catalog.c.active_release_id)).scalar_one() is None
        uow.commit()
    assert execute(s, prepared.manifest, UUID(approval["id"])).id == job.id
    assert Runner(s.app, s.config).run_once()
    assert execute(s, prepared.manifest, UUID(approval["id"])).status == "succeeded"


def test_component_rdf_backup_reconstruction_preserves_history(kernel_case, tmp_path):
    from lzo.semantic.fuseki import FusekiRepository, graph_iri, RepresentationError
    from lzo.semantic.inference import derived_assertion_id
    s = kernel_case
    result = s.load_scene("orchard")
    repository = FusekiRepository(s.config.fuseki_url)
    logical = graph_iri(result.component_id, "derived")
    pair = repository.resolve_graph(logical)
    original = repository.read_graph(logical)
    assert result.conclusion in original
    # Back up exactly the real test-owned physical graph. No generated fixture
    # triples enter RDF here; this restores bytes read from the real service.
    backup = tmp_path / "derived-physical.nt"
    backup.write_text(repository._get(pair.physical_iri).serialize(format="nt"), encoding="utf-8")
    checksum = hashlib.sha256(backup.read_bytes()).hexdigest()
    repository.update(f"DROP SILENT GRAPH <{pair.physical_iri}>")
    try:
        with pytest.raises(RepresentationError):
            repository.read_graph(logical)
    finally:
        assert hashlib.sha256(backup.read_bytes()).hexdigest() == checksum
        repository.update(f"INSERT DATA {{ GRAPH <{pair.physical_iri}> {{ {backup.read_text(encoding='utf-8')} }} }}")
    assert repository.resolve_graph(logical) == pair
    assert repository.read_graph(logical) == original
    assert s.current_inference(result) is True
    response = s.client.get(f"/api/v1/graph/proofs/{derived_assertion_id(result.conclusion)}")
    assert response.status_code == 200, response.text
    assert response.json()["alternatives"]
    assert s.history_contains_original_proof(result) is True


def test_bootstrap_concurrent_install_and_completed_replay_share_one_job(schema_case):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from sqlalchemy import select, func
    from lzo.ontology.models import installations
    from lzo.worker.models import jobs
    from lzo.worker.runner import Runner
    s = schema_case
    prepared = prepare(s)
    approval = s.approve(prepared.draft.model_dump(mode="json"))
    def concurrent_pair():
        barrier = Barrier(2)
        def attempt():
            barrier.wait(timeout=10)
            return execute(s, prepared.manifest, UUID(approval["id"]))
        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(lambda _: attempt(), range(2)))
    first, second = concurrent_pair()
    assert first.id == second.id and first.status == second.status == "queued"
    assert Runner(s.app, s.config).run_once()
    replay_a, replay_b = concurrent_pair()
    assert replay_a.id == replay_b.id == first.id
    assert replay_a.status == replay_b.status == "succeeded"
    with s.transaction() as uow:
        count = uow.session.execute(select(func.count()).select_from(installations)).scalar_one()
        job_ids = uow.session.execute(select(installations.c.job_id)).scalars().all()
        actual = uow.session.execute(select(jobs.c.status).where(jobs.c.id == first.id)).scalar_one()
    assert count == 1 and job_ids == [first.id] and actual == "succeeded"
    print(f"S06 concurrent installation_count={count} job_id={first.id} first_pair=queued replay_pair={actual}")


def assert_executable_approval(s, draft, approval):
    from lzo.auth.permissions import permission_context
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.releases import descriptor
    assert approval["status"] == "approved"
    assert approval["reviewer_id"] != approval["submitter_id"]
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        change = descriptor(uow, s.actor("alice"), UUID(draft["id"]))
        ApprovalService(uow).require_executable(s.actor("alice"), UUID(approval["id"]),
            change.change_digest, change.baseline)


def installation_snapshot(s):
    from sqlalchemy import select
    from lzo.ontology.models import installations, releases
    from lzo.worker.models import jobs
    with s.transaction() as uow:
        return [dict(row) for row in uow.session.execute(select(installations.c.release_id,
            installations.c.job_id, installations.c.state, releases.c.approval_id,
            jobs.c.status, jobs.c.revision, jobs.c.attempts, jobs.c.retry_count, jobs.c.error_code).join(
                releases, releases.c.id == installations.c.release_id).join(
                jobs, jobs.c.id == installations.c.job_id).order_by(installations.c.release_id)).mappings()]


@pytest.mark.parametrize("state", ["queued", "failed"])
def test_bootstrap_reservation_replays_original_job_with_valid_new_approval(schema_case, state):
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    from lzo.worker.runner import Runner
    s = schema_case
    prepared = prepare(s)
    draft = prepared.draft.model_dump(mode="json")
    approval_a = s.approve(draft)
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        uow.job_settings = s.config.model_copy(update={"job_max_attempts": 1})
        original = bootstrap_module().bootstrap_packages(uow, s.actor("alice"),
            prepared.manifest, UUID(approval_a["id"]))
        uow.commit()
    if state == "failed":
        # Actual worker attempt and unavailable HTTP service, not a forged job row.
        assert Runner(s.app, s.config.model_copy(update={"fuseki_url": "http://127.0.0.1:1/lzo"})).run_once()
    before = installation_snapshot(s)
    assert len(before) == 1 and before[0]["job_id"] == original.id
    assert before[0]["status"] == state
    assert before[0]["attempts"] == (1 if state == "failed" else 0)
    approval_b = s.approve(draft)
    assert approval_b["id"] != approval_a["id"]
    assert_executable_approval(s, draft, approval_b)
    with pytest.raises(ApiError) as superseded:
        assert_executable_approval(s, draft, approval_a)
    assert superseded.value.code == "APPROVAL_STALE"
    replay = execute(s, prepared.manifest, UUID(approval_b["id"]))
    assert replay.id == original.id and replay.status == state
    assert installation_snapshot(s) == before
    assert before[0]["approval_id"] == UUID(approval_a["id"])
    incompatible = prepared.manifest.model_copy(update={
        "package_artifacts": tuple(reversed(prepared.manifest.package_artifacts))})
    with pytest.raises(ApiError) as changed:
        execute(s, incompatible, UUID(approval_b["id"]))
    assert changed.value.code == "BOOTSTRAP_BINDINGS_CHANGED"
    assert installation_snapshot(s) == before
    print(f"S06 fix1 original_job={original.id} status={state} installations=1 attempts={before[0]['attempts']} B_valid=True A_superseded=True")


@pytest.mark.parametrize("history", ["unchanged", "changed", "ambiguous"])
def test_bootstrap_reservation_checks_all_historical_installations(schema_case, history):
    from lzo.auth.permissions import permission_context
    from lzo.errors import ApiError
    s = schema_case
    original = prepare(s)
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        current = bootstrap_module().reprepare_bootstrap(uow, s.actor("alice"), original.manifest,
            original.association_revision)
        uow.commit()
    s.login()
    response = s.client.get(f"/api/v1/schema/drafts/{original.draft.id}")
    assert response.status_code == 200, response.text
    old = response.json()
    assert old["revision"] > original.draft.revision
    assert old["document"] == original.draft.document.model_dump(mode="json")
    if history == "changed":
        old["document"]["classes"][0]["label"] = "Changed historical definition"
        response = s.client.patch(f"/api/v1/schema/drafts/{old['id']}", headers=s.headers(old["revision"]),
            json={"document": old["document"]})
        assert response.status_code == 200, response.text
        old = response.json()
    old_approval = s.approve(old)
    assert_executable_approval(s, old, old_approval)
    response = s.publish(old, old_approval)
    assert response.status_code == 202, response.text
    original_job = UUID(response.json()["id"])
    latest = current.draft.model_dump(mode="json")
    approval = s.approve(latest)
    assert_executable_approval(s, latest, approval)
    if history == "ambiguous":
        # Ordinary S02 remains independent; reproduce pre-existing ambiguity
        # through its real approved API, never by fabricating semantic rows.
        response = s.publish(latest, approval)
        assert response.status_code == 202, response.text
        assert UUID(response.json()["id"]) != original_job
    before = installation_snapshot(s)
    assert len(before) == (2 if history == "ambiguous" else 1)
    if history == "unchanged":
        replay = execute(s, current.manifest, UUID(approval["id"]))
        assert replay.id == original_job and replay.status == "queued"
    else:
        with pytest.raises(ApiError) as conflict:
            execute(s, current.manifest, UUID(approval["id"]))
        assert conflict.value.code == ("BOOTSTRAP_INSTALLATION_CONFLICT" if history == "ambiguous"
                                       else "BOOTSTRAP_INSTALLATION_CHANGED")
    assert installation_snapshot(s) == before
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        with pytest.raises(ApiError) as blocked:
            bootstrap_module().reprepare_bootstrap(uow, s.actor("alice"), current.manifest,
                current.association_revision)
        assert blocked.value.code == {"unchanged": "BOOTSTRAP_INSTALLATION_EXISTS",
            "changed": "BOOTSTRAP_INSTALLATION_CHANGED", "ambiguous": "BOOTSTRAP_INSTALLATION_CONFLICT"}[history]
    print(f"S06 fix1 historical={history} installations={len(before)} original_job={original_job}")


def test_bootstrap_reservation_replay_rechecks_revoked_original_dependency(schema_case):
    from lzo.errors import ApiError
    s = schema_case
    grant = s.grant("alice", "editor", scope="farm_a")
    s.grant("admin", "viewer", scope="farm_a")
    prepared = prepare(s, artifact_owner=s.ids_by_name["object_a"])
    draft = prepared.draft.model_dump(mode="json")
    first = s.approve(draft)
    execute(s, prepared.manifest, UUID(first["id"]))
    fresh = s.approve(draft)
    assert_executable_approval(s, draft, fresh)
    before = installation_snapshot(s)
    s.revoke(grant)
    with pytest.raises(ApiError) as hidden:
        execute(s, prepared.manifest, UUID(fresh["id"]))
    assert hidden.value.code == "NOT_FOUND"
    assert installation_snapshot(s) == before


@pytest.mark.parametrize("root", [None, 7, [{}]], ids=["null", "number", "object_array"])
def test_bootstrap_registered_index_rejects_nonobject_json_root(schema_case, root):
    import io
    from sqlalchemy import select, func
    from lzo.auth.permissions import permission_context
    from lzo.artifacts.service import ArtifactService
    from lzo.ontology.drafts import store_for
    from lzo.ontology.models import drafts, installations
    from lzo.semantic.models import objects
    from lzo.errors import ApiError
    s = schema_case
    descriptor = prepare(s, register_only=True)
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        store = store_for(uow)
        blob = store.put(io.BytesIO(json.dumps(root).encode()), "application/json")
        ArtifactService(uow, store, s.config).register_upload(s.actor("alice"), s.package_holder,
            blob, filename="invalid-root.json", metadata={"data_nature": "synthetic"})
        def counts():
            return tuple(uow.session.execute(select(func.count()).select_from(table)).scalar_one()
                         for table in (drafts, installations, objects))
        before = counts()
        with pytest.raises(ApiError) as invalid:
            bootstrap_module().prepare_bootstrap(uow, s.actor("alice"),
                bootstrap_module().BootstrapPreparation(blob, descriptor.artifacts))
        assert invalid.value.code == "BOOTSTRAP_INVALID"
        assert counts() == before
