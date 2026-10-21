"""Real PostgreSQL + Fuseki replay, monotonicity, fences and worker integration."""
from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text


@pytest.fixture
def projection(scope_scenario, rdf_repo):
    from lzo.semantic.projection import (ProjectionService, ProjectionContent,
        register_projection, register_nonsemantic)
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.auth.permissions import validate_actor
    from lzo.governance.revisions import append_revision
    import lzo.worker.handlers
    s = scope_scenario
    s.grant("alice", "editor", scope="farm_a")
    kind = "test.semantic." + uuid4().hex
    for name in ("role.fixture_installed", "grant.fixture_installed", "object.fixture_created"):
        register_nonsemantic(name, "test-fixture-v1")

    def resolver(uow, row):
        # Deliberately uses the supplied immutable revision, not the head.
        return ProjectionContent({"accepted": (Triple("urn:s", "urn:p",
            RdfTerm("literal", row["payload"]["value"])),)}, (row["resource_id"],))

    register_projection(kind, resolver, version="test-v1", dirty_kind="facts")

    class Fixture:
        repo = rdf_repo
        scenario = s
        resource_id = s.ids_by_name["object_a"]

        def append(self, value, *, resource_id=None, expected=None, commit=True, job_settings=None):
            target = resource_id or self.resource_id
            with s.transaction() as uow:
                if job_settings is not None:
                    uow.job_settings = job_settings
                actor = s.actor("alice")
                validate_actor(uow, actor)
                current = uow.session.execute(text("SELECT revision FROM resource_heads WHERE resource_id=:r"),
                                              {"r": target}).scalar_one()
                ref = append_revision(uow, actor, target, expected if expected is not None else current,
                                      {"value": value}, kind)
                ProjectionService(uow, rdf_repo).schedule(actor, ref)
                if commit:
                    uow.commit()
                return ref

        def project(self, revision, *, repo=None, commit=True):
            with s.transaction() as uow:
                receipt = ProjectionService(uow, repo or rdf_repo).project_revision(revision)
                if commit:
                    uow.commit()
                return receipt

        def row(self, ref):
            from lzo.semantic.models import projection_events
            with s.transaction() as uow:
                return uow.session.execute(select(projection_events).where(
                    projection_events.c.event_id == ref.event_id)).mappings().one()

    return Fixture()


def test_new_old_duplicate_revisions_never_regress(projection):
    from lzo.semantic.projection import graph_iri
    old = projection.append("old")
    new = projection.append("new")
    receipt = projection.project(new)
    assert receipt.state == "synced"
    assert projection.project(old).state == "superseded"
    assert projection.project(new) == receipt
    assert [t.object.value for t in projection.repo.read_graph(graph_iri(new.resource_id, "accepted"))] == ["new"]
    assert projection.repo.read_graph(graph_iri(new.resource_id, "candidate")) == ()
    with projection.scenario.transaction() as uow:
        assert uow.session.execute(text("SELECT count(*) FROM projection_events")).scalar_one() == 2


def test_failed_remote_is_pending_not_synced_and_retryable(projection):
    from lzo.semantic.fuseki import FusekiRepository
    ref = projection.append("retry")
    assert projection.row(ref)["state"] == "pending"
    failed = projection.project(ref, repo=FusekiRepository("http://127.0.0.1:1/lzo", timeout_seconds=0.2))
    assert failed.state == "failed"
    assert projection.row(ref)["state"] == "failed"
    assert projection.project(ref).state == "synced"


def test_remote_success_pg_rollback_reconciles(projection):
    ref = projection.append("crash")
    receipt = projection.project(ref, commit=False)
    assert receipt.state == "synced"
    assert projection.row(ref)["state"] == "pending"
    assert projection.project(ref) == receipt


def test_contiguous_prefix_blocks_gap_and_unknown(projection):
    from lzo.semantic.projection import ProjectionService
    from lzo.governance.revisions import append_revision
    from lzo.auth.permissions import validate_actor
    first = projection.append("first")
    later = projection.append("later")
    assert projection.project(later).state == "synced"
    with projection.scenario.transaction() as uow:
        watermark = uow.session.execute(text("SELECT projection_watermark FROM platform_watermarks")).scalar_one()
        assert watermark < projection.row(first)["position"]
    assert projection.project(first).state == "superseded"
    with projection.scenario.transaction() as uow:
        service = ProjectionService(uow, projection.repo)
        completed = service.advance_contiguous()
        actor = projection.scenario.actor("alice")
        validate_actor(uow, actor)
        unknown = append_revision(uow, actor, uuid4(), 0, {}, "unregistered.semantic")
        assert service.advance_contiguous() == completed
        uow.commit()


def test_dirty_fence_is_transactional_and_not_cleared_by_projection(projection):
    from lzo.semantic.models import inference_fence
    ref = projection.append("dirty")
    with projection.scenario.transaction() as uow:
        fence = uow.session.execute(select(inference_fence)).mappings().one()
        assert fence["expected_position"] == projection.row(ref)["position"]
        assert fence["resolved_position"] < fence["expected_position"]
    projection.project(ref)
    with projection.scenario.transaction() as uow:
        fence = uow.session.execute(select(inference_fence)).mappings().one()
        assert fence["resolved_position"] < fence["expected_position"]
        count = uow.session.execute(text("SELECT count(*) FROM projection_events")).scalar_one()
    projection.append("rollback", commit=False)
    with projection.scenario.transaction() as uow:
        assert uow.session.execute(text("SELECT count(*) FROM projection_events")).scalar_one() == count


def test_generation_conflict_and_missing_pair_cannot_publish(projection):
    from lzo.semantic.projection import graph_iri
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.semantic.fuseki import RepresentationError
    ref = projection.append("one")
    projection.project(ref)
    with pytest.raises(RepresentationError):
        projection.repo.publish_revision(ref.resource_id, ref.revision, projection.row(ref)["position"],
            {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "different")),)})
    assert projection.repo.read_graph(graph_iri(ref.resource_id, "accepted"))[0].object.value == "one"


def test_worker_failure_is_not_success(projection, settings):
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    settings = settings.model_copy(update={"fuseki_url": "http://127.0.0.1:1/lzo", "job_max_attempts": 1})
    projection.append("failure", job_settings=settings)
    runner = Runner(projection.scenario.app, settings)
    assert runner.run_once()
    with projection.scenario.transaction() as uow:
        row = uow.session.execute(select(jobs).where(jobs.c.kind == "rdf.project")).mappings().one()
        assert row["status"] == "failed"
        assert row["error_code"] == "HANDLER_FAILED"


def test_dynamic_worker_action_is_frozen_and_controls_job(jobs):
    from lzo.worker.service import register_handler, enqueue, JobService
    from lzo.errors import ApiError
    s = jobs.scenario
    kind = "test.dynamic." + uuid4().hex
    # Trusted resolver takes the caller UoW and does not accept payload action.
    register_handler(kind, lambda ctx: None, action=lambda uow, payload: "edit")
    with s.transaction() as uow:
        ref = enqueue(uow, jobs.actor(), kind, jobs.payload, None)
        uow.commit()
    s.grant("nobody", "viewer", scope="farm_a")
    with s.transaction() as uow:
        service = JobService(uow, jobs.settings)
        view = service.get(s.actor("nobody"), ref.id)
        with pytest.raises(ApiError):
            service.set_state(s.actor("nobody"), ref.id, "paused", view.revision)
    register_handler(kind, lambda ctx: None, action=lambda uow, payload: "schema.publish")
    assert jobs.runner().run_once() is False
    with s.transaction() as uow:
        from lzo.worker.models import jobs as job_table
        row = uow.session.execute(select(job_table).where(job_table.c.id == ref.id)).mappings().one()
        assert row["status"] == "failed" and row["error_code"] == "JOB_POLICY_CHANGED"


def test_schema_publish_only_worker_and_viewer_controls(scope_scenario, rdf_repo, settings):
    from sqlalchemy import insert
    from lzo.auth.models import roles, resource_scopes
    from lzo.auth.permissions import validate_actor
    from lzo.governance.revisions import append_revision
    from lzo.semantic.projection import ProjectionService, ProjectionContent, register_projection
    from lzo.semantic.rdf import RdfTerm, Triple
    from lzo.worker.models import jobs
    from lzo.worker.service import JobService, enqueue
    from lzo.worker.runner import Runner
    from lzo.errors import ApiError
    import lzo.worker.handlers
    s = scope_scenario
    s.roles["publisher"] = uuid4()
    with s.transaction() as uow:
        uow.session.execute(insert(roles).values(id=s.roles["publisher"], name="publisher",
                                                actions=["view", "schema.publish"], revision=1))
        uow.session.execute(insert(resource_scopes).values(resource_id=s.roles["publisher"],
            resource_kind="role", security_class="identity"))
        append_revision(uow, s.admin_actor(), s.roles["publisher"], 0, {}, "role.fixture_installed")
        uow.commit()
    grant = s.grant("alice", "publisher", scope="farm_a")
    s.grant("nobody", "viewer", scope="farm_a")
    kind = "test.schema." + uuid4().hex
    register_projection(kind, lambda uow, row: ProjectionContent(
        {"schema": (Triple("urn:Class", "urn:label", RdfTerm("literal", row["payload"]["label"])),)},
        (row["resource_id"],)), version="test-v1", dirty_kind="schema", action="schema.publish")
    with s.transaction() as uow:
        validate_actor(uow, s.actor("alice"))
        ref = append_revision(uow, s.actor("alice"), s.ids_by_name["object_a"], 1, {"label": "Schema"}, kind)
        job = ProjectionService(uow, rdf_repo).schedule(s.actor("alice"), ref)
        uow.commit()
    with s.transaction() as uow:
        row = uow.session.execute(select(jobs).where(jobs.c.id == job.id)).mappings().one()
        assert row["action"] == "schema.publish"
        with pytest.raises(ApiError):
            enqueue(uow, s.actor("nobody"), "rdf.project", row["payload"], None)
        service = JobService(uow, settings)
        visible = service.get(s.actor("nobody"), job.id)
        with pytest.raises(ApiError):
            service.set_state(s.actor("nobody"), job.id, "paused", visible.revision)
    runner = Runner(s.app, settings)
    assert runner.run_once()
    with s.transaction() as uow:
        assert uow.session.execute(select(jobs.c.status).where(jobs.c.id == job.id)).scalar_one() == "succeeded"
        assert JobService(uow, settings).get(s.actor("alice"), job.id).result_ref is None


def test_actual_remote_stale_publisher_cannot_overtake(rdf_repo, settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    from lzo.semantic.rdf import RdfTerm, Triple
    staged, release = Event(), Event()
    resource_id = uuid4()
    class DelayedPublisher(FusekiRepository):
        def update(self, query):
            if query.lstrip().startswith("DELETE"):
                staged.set()
                assert release.wait(30)
            return super().update(query)
    old = DelayedPublisher(settings.fuseki_url)
    first = {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "old")),)}
    second = {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "new")),)}
    with ThreadPoolExecutor(max_workers=1) as pool:
        task = pool.submit(old.publish_revision, resource_id, 1, 1, first)
        try:
            assert staged.wait(30)
            assert rdf_repo.read_graph(graph_iri(resource_id, "accepted")) == ()
            assert rdf_repo.publish_revision(resource_id, 2, 2, second)[0] == "synced"
        finally:
            release.set()
        assert task.result()[0] == "superseded"
    assert rdf_repo.read_graph(graph_iri(resource_id, "accepted"))[0].object.value == "new"


def test_projection_history_and_worker_require_every_pinned_dependency(projection, settings):
    from lzo.contracts import RevisionRef
    from lzo.auth.permissions import validate_actor
    from lzo.governance.models import revisions
    from lzo.governance.revisions import append_revision
    from lzo.governance.history import HistoryService, HistoryQuery
    from lzo.semantic.projection import ProjectionService, ProjectionContent, register_projection
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    s = projection.scenario
    dependency_grant = s.grant("alice", "viewer", scope="farm_b")
    dependency_id = s.ids_by_name["object_b"]
    kind = "test.dependencies." + uuid4().hex
    with s.transaction() as uow:
        event_id = uow.session.execute(select(revisions.c.event_id).where(
            revisions.c.resource_id == dependency_id, revisions.c.revision == 1)).scalar_one()
    pinned = RevisionRef(dependency_id, 1, event_id)
    register_projection(kind, lambda uow, row: ProjectionContent({}, (row["resource_id"], dependency_id), (pinned,)),
                        version="test-v1", dirty_kind="facts")
    with s.transaction() as uow:
        validate_actor(uow, s.actor("alice"))
        ref = append_revision(uow, s.actor("alice"), projection.resource_id, 1,
                              {"dependency_event": str(event_id)}, kind)
        job = ProjectionService(uow, projection.repo).schedule(s.actor("alice"), ref)
        uow.commit()
    with s.transaction() as uow:
        assert ref in HistoryService(uow).list(s.actor("alice"), HistoryQuery(), None).items
    s.revoke(dependency_grant)
    with s.transaction() as uow:
        assert ref not in HistoryService(uow).list(s.actor("alice"), HistoryQuery(), None).items
    assert Runner(s.app, settings).run_once() is False
    with s.transaction() as uow:
        row = uow.session.execute(select(jobs).where(jobs.c.id == job.id)).mappings().one()
        assert row["status"] == "failed" and row["error_code"] == "AUTHORIZATION_REVOKED"
    assert projection.repo.current_revision(ref.resource_id) is None


def test_changed_pinned_dependency_cannot_relabel_old_revision(projection):
    from lzo.contracts import RevisionRef
    from lzo.auth.permissions import validate_actor
    from lzo.governance.models import revisions
    from lzo.governance.revisions import append_revision
    from lzo.semantic.projection import ProjectionService, ProjectionContent, register_projection
    s = projection.scenario
    s.grant("alice", "viewer", scope="farm_b")
    dependency_id = s.ids_by_name["object_b"]
    with s.transaction() as uow:
        event_id = uow.session.execute(select(revisions.c.event_id).where(
            revisions.c.resource_id == dependency_id, revisions.c.revision == 1)).scalar_one()
    pinned = [RevisionRef(dependency_id, 1, event_id)]
    kind = "test.pinning." + uuid4().hex
    register_projection(kind, lambda uow, row: ProjectionContent({}, (row["resource_id"], dependency_id), tuple(pinned)),
                        version="test-v1", dirty_kind="facts")
    with s.transaction() as uow:
        validate_actor(uow, s.actor("alice"))
        ref = append_revision(uow, s.actor("alice"), projection.resource_id, 1, {}, kind)
        ProjectionService(uow, projection.repo).schedule(s.actor("alice"), ref)
        uow.commit()
    with s.transaction() as uow:
        pinned[:] = [append_revision(uow, s.admin_actor(), dependency_id, 1, {}, "object.fixture_updated")]
        uow.commit()
    with pytest.raises(ValueError, match="contract changed"):
        projection.project(ref)
    assert projection.repo.current_revision(ref.resource_id) is None


def test_complete_unpublished_stage_retries_after_delivery_failure(projection, settings):
    import httpx
    from lzo.semantic.fuseki import FusekiRepository, graph_iri
    class InterruptedPublisher(FusekiRepository):
        def update(self, query):
            if query.lstrip().startswith("DELETE"):
                raise httpx.ReadError("interrupted after genuine atomic staging")
            return super().update(query)
    ref = projection.append("staged")
    assert projection.project(ref, repo=InterruptedPublisher(settings.fuseki_url)).state == "failed"
    assert projection.repo.read_graph(graph_iri(ref.resource_id, "accepted")) == ()
    assert projection.project(ref).state == "synced"
    assert projection.repo.read_graph(graph_iri(ref.resource_id, "accepted"))[0].object.value == "staged"


@pytest.mark.parametrize("missing", ["role", "generation"])
def test_replay_rejects_missing_live_role_pointer(projection, missing):
    from lzo.semantic.fuseki import CATALOG, V, graph_iri
    ref = projection.append("published")
    assert projection.project(ref).state == "synced"
    graph = graph_iri(ref.resource_id, "accepted") if missing == "role" else f"urn:lzo:resource:{ref.resource_id}"
    predicate = V.current if missing == "role" else V.generation
    projection.repo.update(f'DELETE WHERE {{ GRAPH <{CATALOG}> {{ <{graph}> <{predicate}> ?pointer }} }}')
    assert projection.project(ref).state == "failed"


@pytest.mark.parametrize("damage", ["extra_position", "missing_position", "missing_manifest"])
def test_corrupt_staged_manifest_never_replaces_healthy_generation(rdf_repo, settings, damage):
    import httpx
    from lzo.semantic.fuseki import FusekiRepository, RepresentationError, V, ROLES, generation_digest, graph_iri
    from lzo.semantic.rdf import INTERNAL, RdfTerm, Triple
    resource_id = uuid4()
    old = {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "old")),)}
    new = {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "new")),)}
    rdf_repo.publish_revision(resource_id, 1, 1, old)
    before = rdf_repo.current_revision(resource_id)
    pointers = {role: rdf_repo.resolve_graph(graph_iri(resource_id, role)) for role in ROLES}
    generation = INTERNAL + f"generation:{resource_id}:2:{generation_digest(new)}"
    class Interrupted(FusekiRepository):
        def update(self, query):
            if query.lstrip().startswith("DELETE"):
                raise httpx.ReadError("stop after genuine atomic staging")
            super().update(query)
    with pytest.raises(httpx.ReadError):
        Interrupted(settings.fuseki_url).publish_revision(resource_id, 2, 2, new)
    if damage == "extra_position":
        rdf_repo.update(f"INSERT DATA {{ GRAPH <{generation}> {{ <{generation}> <{V.position}> 999 }} }}")
    elif damage == "missing_position":
        rdf_repo.update(f"DELETE WHERE {{ GRAPH <{generation}> {{ <{generation}> <{V.position}> ?p }} }}")
    else:
        rdf_repo.update(f"DROP GRAPH <{generation}>")
    damaged = set(rdf_repo._get(generation))
    with pytest.raises(RepresentationError):
        rdf_repo.publish_revision(resource_id, 2, 2, new)
    assert set(rdf_repo._get(generation)) == damaged
    assert rdf_repo.current_revision(resource_id) == before
    assert {role: rdf_repo.resolve_graph(graph_iri(resource_id, role)) for role in ROLES} == pointers


def test_same_generation_race_cannot_merge_conflicting_positions(rdf_repo, settings):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from lzo.semantic.fuseki import FusekiRepository, RepresentationError
    from lzo.semantic.rdf import RdfTerm, Triple
    resource_id = uuid4()
    rdf_repo.publish_revision(resource_id, 1, 1, {})
    graphs = {"accepted": (Triple("urn:s", "urn:p", RdfTerm("literal", "new")),)}
    waiting, release = Event(), Event()
    class DelayedStage(FusekiRepository):
        def update(self, query):
            if query.lstrip().startswith("INSERT"):
                waiting.set()
                assert release.wait(45)
            super().update(query)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(DelayedStage(settings.fuseki_url).publish_revision, resource_id, 2, 2, graphs)
        try:
            assert waiting.wait(30)
            assert rdf_repo.publish_revision(resource_id, 2, 999, graphs)[0] == "synced"
            winner = rdf_repo.current_revision(resource_id)
        finally:
            release.set()
        with pytest.raises(RepresentationError):
            first.result()
    assert rdf_repo.current_revision(resource_id) == winner
    assert rdf_repo.publish_revision(resource_id, 2, 999, graphs)[0] == "synced"


def test_new_stage_manifest_is_verified_before_pointer_publication(rdf_repo, settings):
    from lzo.semantic.fuseki import FusekiRepository, RepresentationError, V, generation_digest
    from lzo.semantic.rdf import INTERNAL
    resource_id = uuid4()
    rdf_repo.publish_revision(resource_id, 1, 1, {})
    before = rdf_repo.current_revision(resource_id)
    generation = INTERNAL + f"generation:{resource_id}:2:{generation_digest({})}"
    class CorruptNewStage(FusekiRepository):
        def update(self, query):
            super().update(query)
            if query.lstrip().startswith("INSERT"):
                super().update(f"INSERT DATA {{ GRAPH <{generation}> {{ <{generation}> <{V.position}> 999 }} }}")
    with pytest.raises(RepresentationError):
        CorruptNewStage(settings.fuseki_url).publish_revision(resource_id, 2, 2, {})
    assert rdf_repo.current_revision(resource_id) == before
