"""F05: durable effects, current authority and fenced recovery on real PostgreSQL."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select, text, update

from lzo.errors import ApiError


def test_worker_exposes_durable_protocol():
    import lzo.worker as worker
    assert hasattr(worker, "register_handler"), "F05 durable handler protocol is absent"


def test_rollback_does_not_enqueue(jobs):
    from lzo.worker.models import jobs as table
    with jobs.transaction() as uow:
        jobs.enqueue(uow)
    with jobs.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(table)).scalar_one() == 0


def test_expired_worker_cannot_commit_checkpoint(jobs):
    from lzo.worker.service import checkpoint
    job = jobs.create()
    old = jobs.runner("old").claim()
    assert jobs.runner("other").claim() is None
    jobs.expire(job.id)
    new = jobs.runner("new").claim()
    assert new.lease_token != old.lease_token
    with pytest.raises(ApiError, match="LEASE_EXPIRED"):
        with old.batch():
            checkpoint(old.job_id, old.lease_token, {"offset": 999})
    with new.batch():
        jobs.effect(new, 1)
        checkpoint(new.job_id, new.lease_token, {"offset": 1})
    jobs.pause_resume(job.id)
    assert jobs.runner("last").run_once()
    assert jobs.effects() == [1, 2]
    assert jobs.view(job.id).job.status == "succeeded"


def test_batch_rollback_and_replay_share_checkpoint(jobs):
    from lzo.worker.service import checkpoint
    job = jobs.create()
    ctx = jobs.runner().claim()
    with pytest.raises(RuntimeError):
        with ctx.batch():
            jobs.effect(ctx, 1)
            checkpoint(ctx.job_id, ctx.lease_token, {"offset": 1})
            raise RuntimeError("rollback domain effect")
    assert jobs.effects() == []
    jobs.expire(job.id)
    assert jobs.runner("restart").run_once()
    assert jobs.effects() == [1, 2]
    assert jobs.view(job.id).progress == {"offset": 2}


def test_repeated_outbox_delivery_enqueues_once(jobs):
    from lzo.governance.models import outbox_events
    from lzo.worker.service import enqueue_outbox
    with jobs.transaction() as uow:
        event = uow.session.execute(select(outbox_events).where(
            outbox_events.c.payload["resource_id"].astext == str(jobs.resource_id))).mappings().first()
        first = enqueue_outbox(uow, jobs.actor(), jobs.kind, event["id"], jobs.payload, None)
        second = enqueue_outbox(uow, jobs.actor(), jobs.kind, event["id"], jobs.payload, None)
        assert first.id == second.id
        uow.commit()
    assert jobs.runner().run_once()
    assert jobs.runner().run_once() is False
    assert jobs.effects() == [1, 2]


def test_revoked_initiator_cannot_continue(jobs):
    job = jobs.create()
    ctx = jobs.runner().claim()
    jobs.scenario.revoke(jobs.grant)
    with pytest.raises(ApiError):
        with ctx.batch():
            jobs.effect(ctx, 1)
    jobs.expire(job.id)
    jobs.runner("new").run_once()
    assert jobs.effects() == []
    with jobs.transaction() as uow:
        from lzo.worker.models import jobs as table
        row = uow.session.execute(select(table).where(table.c.id == job.id)).mappings().one()
        assert row["status"] == "failed" and row["error_code"] == "AUTHORIZATION_REVOKED"


def test_two_workers_claim_distinct_jobs(jobs):
    jobs.create()
    jobs.create()
    with ThreadPoolExecutor(max_workers=2) as pool:
        contexts = list(pool.map(lambda name: jobs.runner(name).claim(), ("a", "b")))
    assert len({ctx.job_id for ctx in contexts}) == 2
    assert len({ctx.lease_token for ctx in contexts}) == 2


def test_retry_backoff_is_bounded_and_audited(jobs):
    from lzo.worker import register_handler
    from lzo.worker.models import job_attempts
    def fail(ctx):
        raise RuntimeError("password=DO_NOT_PERSIST")
    register_handler(jobs.kind, fail)
    job = jobs.create()
    runner = jobs.runner()
    for attempt in range(3):
        assert runner.run_once()
        assert not runner.run_once()
        jobs.make_available(job.id)
    view = jobs.view(job.id)
    assert view.job.status == "failed" and view.error_code == "HANDLER_FAILED"
    with jobs.transaction() as uow:
        records = uow.session.execute(select(job_attempts).where(job_attempts.c.job_id == job.id)).mappings().all()
        assert [row["attempt"] for row in records if row["phase"] == "started"] == [1, 2, 3]
        assert [row["phase"] for row in records if row["phase"] != "started"] == ["queued", "queued", "failed"]
        assert all("DO_NOT_PERSIST" not in str(row) for row in records)
    jobs.retry(job.id)
    assert jobs.view(job.id).job.status == "queued"


def test_visibility_filters_before_page_and_history(jobs):
    from lzo.governance.history import HistoryService, HistoryQuery
    from lzo.worker.service import JobService
    hidden = jobs.create()
    jobs.scenario.grant("alice", "editor", scope="farm_b")
    visible = jobs.create(resource_id=jobs.scenario.ids_by_name["object_b"])
    jobs.scenario.revoke(jobs.grant)
    with jobs.transaction() as uow:
        service = JobService(uow, jobs.settings)
        page = service.list(jobs.actor())
        assert [item.job.id for item in page.items] == [visible.id]
        assert page.next_cursor is None
        with pytest.raises(ApiError) as error:
            service.get(jobs.actor(), hidden.id)
        assert error.value.status == 404
        history = HistoryService(uow).list(jobs.actor(), HistoryQuery(resource_kind="job"), None)
        assert all(item.resource_id != hidden.id for item in history.items)


def test_new_result_reference_is_rechecked_on_list_and_history(jobs):
    from lzo.worker.service import JobResultRef, JobService, register_result_kind
    from lzo.governance.history import HistoryService
    register_result_kind("test.object", "/api/v1/objects/{resource_id}")
    grant = jobs.scenario.grant("alice", "viewer", scope="farm_b")
    job = jobs.create()
    ctx = jobs.runner().claim()
    target = jobs.scenario.ids_by_name["object_b"]
    with ctx.batch():
        ctx.set_result(JobResultRef(kind="test.object", resource_id=target,
                                   api_path=f"/api/v1/objects/{target}"))
    revision = jobs.view(job.id).revision
    jobs.scenario.revoke(grant)
    with jobs.transaction() as uow:
        assert JobService(uow, jobs.settings).list(jobs.actor()).items == []
        with pytest.raises(ApiError) as error:
            HistoryService(uow).get(jobs.actor(), job.id, revision)
        assert error.value.status == 404


def test_expired_checkpoint_rolls_back_effect_even_without_checkpoint_call(jobs):
    job = jobs.create()
    ctx = jobs.runner().claim()
    with pytest.raises(ApiError, match="LEASE_EXPIRED"):
        with ctx.batch():
            jobs.effect(ctx, 1)
            ctx.uow.session.execute(text("UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": job.id})
    assert jobs.effects() == []


def test_pause_stale_revision_and_heartbeat_fencing(jobs):
    from lzo.worker.service import JobService
    job = jobs.create()
    old_revision = jobs.view(job.id).revision
    ctx = jobs.runner().claim()
    ctx.heartbeat()
    with jobs.transaction() as uow:
        with pytest.raises(ApiError, match="REVISION_CONFLICT"):
            JobService(uow, jobs.settings).set_state(jobs.actor(), job.id, "paused", old_revision)
    jobs.pause_resume(job.id)
    with pytest.raises(ApiError, match="LEASE_EXPIRED"):
        ctx.heartbeat()


@pytest.mark.parametrize(("maximum", "prior_failures", "pauses"),
                         [(1, 0, 1), (1, 0, 4), (3, 2, 4)])
def test_live_pause_preserves_failure_allowance_and_attempt_history(
        jobs, maximum, prior_failures, pauses):
    from lzo.worker.models import jobs as table, job_attempts
    from lzo.worker.service import checkpoint
    with jobs.transaction() as uow:
        uow.job_settings = jobs.settings.model_copy(update={"job_max_attempts": maximum})
        job = jobs.enqueue(uow)
        uow.commit()
    runner = jobs.runner()
    for _ in range(prior_failures):
        failed = runner.claim()
        runner.finish(failed, RuntimeError("synthetic failed attempt"))
        jobs.make_available(job.id)
    for index in range(pauses):
        ctx = runner.claim()
        assert ctx is not None, "A deliberate pause must leave a resumable attempt"
        if index == 0:
            with ctx.batch():
                jobs.effect(ctx, 1)
                checkpoint(ctx.job_id, ctx.lease_token, {"offset": 1})
        jobs.pause_resume(job.id)
        with pytest.raises(ApiError, match="LEASE_EXPIRED"):
            ctx.heartbeat()
    assert runner.run_once()
    assert jobs.view(job.id).job.status == "succeeded"
    assert jobs.effects() == [1, 2]
    with jobs.transaction() as uow:
        row = uow.session.execute(select(table).where(table.c.id == job.id)).mappings().one()
        assert row["attempts"] == prior_failures + pauses + 1
        assert row["retry_count"] == prior_failures + 1
        records = uow.session.execute(select(job_attempts).where(
            job_attempts.c.job_id == job.id)).mappings().all()
        assert sum(row["phase"] == "paused" for row in records) == pauses
        assert sum(row["phase"] == "started" for row in records) == prior_failures + pauses + 1


def test_pausing_failed_queued_retry_does_not_refund_failure(jobs):
    from lzo.worker.models import jobs as table, job_attempts
    with jobs.transaction() as uow:
        uow.job_settings = jobs.settings.model_copy(update={"job_max_attempts": 2})
        job = jobs.enqueue(uow)
        uow.commit()
    runner = jobs.runner()
    runner.finish(runner.claim(), RuntimeError("first failure"))
    assert jobs.view(job.id).job.status == "queued"
    for _ in range(3):
        jobs.pause_resume(job.id)
    runner.finish(runner.claim(), RuntimeError("final failure"))
    assert jobs.view(job.id).job.status == "failed"
    assert not runner.run_once()
    with jobs.transaction() as uow:
        row = uow.session.execute(select(table).where(table.c.id == job.id)).mappings().one()
        assert row["attempts"] == 2 and row["retry_count"] == 2
        records = uow.session.execute(select(job_attempts).where(
            job_attempts.c.job_id == job.id)).mappings().all()
        assert sum(row["error_code"] == "HANDLER_FAILED" for row in records) == 2


def test_pausing_expired_running_attempt_preserves_crash_allowance(jobs):
    from lzo.worker.models import jobs as table, job_attempts
    with jobs.transaction() as uow:
        uow.job_settings = jobs.settings.model_copy(update={"job_max_attempts": 1})
        job = jobs.enqueue(uow)
        uow.commit()
    runner = jobs.runner()
    crashed = runner.claim()
    jobs.expire(job.id)
    jobs.pause_resume(job.id)
    assert not runner.run_once()
    assert jobs.view(job.id).job.status == "failed"
    assert jobs.view(job.id).error_code == "ATTEMPTS_EXHAUSTED"
    with jobs.transaction() as uow:
        row = uow.session.execute(select(table).where(table.c.id == job.id)).mappings().one()
        assert row["attempts"] == 1 and row["retry_count"] == 1
        records = uow.session.execute(select(job_attempts).where(
            job_attempts.c.job_id == job.id, job_attempts.c.phase == "expired")).mappings().all()
        assert len(records) == 1
        assert records[0]["lease_token"] == crashed.lease_token
        assert records[0]["error_code"] == "LEASE_EXPIRED"


def test_runtime_cannot_rewrite_attempts(jobs):
    from sqlalchemy.exc import DBAPIError
    jobs.create()
    assert jobs.runner().run_once()
    for statement in ("UPDATE job_attempts SET phase='forged'", "DELETE FROM job_attempts"):
        with pytest.raises(DBAPIError):
            with jobs.transaction() as uow:
                uow.session.execute(text(statement))


def test_unregistered_handler_cannot_fake_success(jobs):
    from lzo.worker.service import enqueue
    with jobs.transaction() as uow:
        with pytest.raises(ApiError, match="JOB_HANDLER_UNREGISTERED"):
            enqueue(uow, jobs.actor(), "test.unregistered.operation", jobs.payload, None)


def test_registered_handler_reference_changes_are_rejected(jobs):
    from lzo.worker import register_handler
    job = jobs.create()
    register_handler(jobs.kind, lambda ctx: None, references=lambda payload: (
        jobs.resource_id, jobs.scenario.ids_by_name["object_b"]))
    jobs.runner().run_once()
    assert jobs.view(job.id).job.status == "failed"
    assert jobs.view(job.id).error_code == "JOB_POLICY_CHANGED"


def test_same_uow_revision_then_enqueue_does_not_invert_worker_locks(jobs):
    from threading import Event
    from lzo.auth.permissions import validate_actor
    from lzo.governance.revisions import append_revision
    job = jobs.create()
    ctx = jobs.runner().claim()
    started = Event()
    def worker():
        started.set()
        with ctx.batch():
            jobs.effect(ctx, 1)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with jobs.transaction() as uow:
            actor = jobs.actor()
            validate_actor(uow, actor)
            append_revision(uow, actor, jobs.resource_id, 1, {"value": "composed"}, "object.edited")
            future = pool.submit(worker)
            assert started.wait(5)
            jobs.enqueue(uow)
            uow.commit()
        future.result(timeout=10)
    assert jobs.effects() == [1]


def test_actual_worker_kill_restart_recovers_committed_cursor(jobs, tmp_path):
    """Terminate only the child with verified PID/creation/command identity."""
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    from lzo.worker.models import jobs as table

    job = jobs.create()
    module = tmp_path / "f05_process_handler.py"
    marker = tmp_path / "committed"
    module.write_text(
        "from pathlib import Path\n"
        "from threading import Event\n"
        "from sqlalchemy import text\n"
        "from lzo.worker import register_handler\n"
        "from lzo.worker.service import checkpoint\n"
        "def handler(ctx):\n"
        "    while ctx.cursor.get('offset', 0) < 2:\n"
        "        with ctx.batch():\n"
        "            offset = ctx.cursor.get('offset', 0) + 1\n"
        "            ctx.uow.session.execute(text('INSERT INTO job_test_effects VALUES (:id,:n)'), {'id':ctx.job_id,'n':offset})\n"
        "            checkpoint(ctx.job_id, ctx.lease_token, {'offset':offset})\n"
        "        if offset == 1:\n"
        f"            Path({str(marker)!r}).write_text('committed', encoding='utf-8')\n"
        "            Event().wait()\n"
        f"register_handler({jobs.kind!r}, handler)\n", encoding="utf-8")
    sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "scripts"))
    from native_dev import runtime_environment
    env = runtime_environment(dict(os.environ))
    env.update(PYTHONPATH=str(tmp_path) + os.pathsep + str(Path(__file__).resolve().parents[2] / "src"),
        PGOPTIONS=f"-csearch_path={jobs.schema}", LZO_WORKER_HANDLER_MODULES="f05_process_handler",
        LZO_JOB_POLL_SECONDS="0.05", LZO_JOB_LEASE_SECONDS="2")
    command = [sys.executable, "-m", "lzo.worker"]
    children = []

    def identity(pid):
        if os.name == "nt":
            script = (f"$p=Get-CimInstance Win32_Process -Filter 'ProcessId = {pid}'; "
                      "$p | Select-Object ProcessId,CreationDate,CommandLine | ConvertTo-Json -Compress")
            import json
            return json.loads(subprocess.check_output(["powershell", "-NoProfile", "-Command", script],
                                                      text=True, creationflags=subprocess.CREATE_NO_WINDOW))
        return {"pid": pid, "start": Path(f"/proc/{pid}/stat").read_text().split()[21],
                "command": Path(f"/proc/{pid}/cmdline").read_bytes().decode()}

    def start():
        proc = subprocess.Popen(command, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        saved = identity(proc.pid)
        assert "lzo.worker" in str(saved)
        children.append((proc, saved))
        return proc, saved

    def stop(child):
        proc, saved = child
        if proc.poll() is None:
            assert identity(proc.pid) == saved, "Refusing to kill a changed process identity"
            proc.kill()
            proc.wait(timeout=10)

    try:
        first = start()
        deadline = time.monotonic() + 20
        while not marker.exists() and time.monotonic() < deadline:
            assert first[0].poll() is None, "Worker unexpectedly exited"
            time.sleep(0.05)
        assert marker.exists(), "Worker did not commit its first real batch"
        assert jobs.effects() == [1]
        stop(first)
        # Expiry is controlled by the database clock rather than waiting in tests.
        jobs.expire(job.id)
        second = start()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if jobs.view(job.id).job.status == "succeeded":
                break
            assert second[0].poll() is None
            time.sleep(0.05)
        assert jobs.view(job.id).job.status == "succeeded"
        assert jobs.effects() == [1, 2]
        with jobs.transaction() as uow:
            assert uow.session.execute(select(table.c.attempts).where(table.c.id == job.id)).scalar_one() == 2
    finally:
        for child in children:
            stop(child)


def test_public_checkpoint_can_commit_a_standalone_progress_boundary(jobs):
    from lzo.worker import register_handler
    from lzo.worker.service import checkpoint
    def handler(ctx):
        checkpoint(ctx.job_id, ctx.lease_token, {"stage": "complete"})
    register_handler(jobs.kind, handler)
    job = jobs.create()
    assert jobs.runner().run_once()
    assert jobs.view(job.id).job.status == "succeeded"
    assert jobs.view(job.id).progress == {"stage": "complete"}


def test_batch_budget_yields_without_consuming_failure_retries(jobs):
    jobs.settings = jobs.settings.model_copy(update={"job_max_batches_per_run": 1})
    job = jobs.create()
    assert jobs.runner().run_once()
    assert jobs.view(job.id).job.status == "queued"
    assert jobs.effects() == [1]
    assert jobs.runner().run_once()
    assert jobs.view(job.id).job.status == "succeeded"
    assert jobs.effects() == [1, 2]


def test_payload_and_queue_budgets_roll_back_without_partial_revisions(jobs):
    from lzo.worker.service import enqueue
    from lzo.worker.models import jobs as table
    with jobs.transaction() as uow:
        uow.job_settings = jobs.settings.model_copy(update={"job_max_json_bytes": 256, "job_queue_limit": 1})
        with pytest.raises(ApiError, match="INVALID_JOB_DATA"):
            enqueue(uow, jobs.actor(), jobs.kind, {**jobs.payload, "large": "x" * 300}, None)
        jobs.enqueue(uow)
        with pytest.raises(ApiError, match="JOB_QUEUE_FULL"):
            jobs.enqueue(uow)
        uow.commit()
    with jobs.transaction() as uow:
        assert uow.session.execute(select(func.count()).select_from(table)).scalar_one() == 1


@pytest.fixture
def job_client(jobs):
    from cryptography.fernet import Fernet
    from fastapi.testclient import TestClient
    from lzo.auth.session import SessionService, SESSION_COOKIE
    from lzo.main import create_app
    config = jobs.settings.model_copy(update={"session_encryption_key": Fernet.generate_key().decode(),
                                              "public_origin": "https://testserver"})
    app = create_app(config)
    app.state.engine.dispose()
    app.state.engine = jobs.scenario.app
    with TestClient(app, base_url="https://testserver") as client:
        def login(name):
            with jobs.transaction() as uow:
                cookie = SessionService(uow, config).create(jobs.scenario.users[name], {"synthetic": True})
                uow.commit()
            client.cookies.set(SESSION_COOKIE, cookie)
            return client.get("/api/v1/session").json()["csrf_token"]
        client.login_as = login
        yield client


def test_http_current_scope_preconditions_idempotency_and_health(jobs, job_client):
    job = jobs.create()
    client = job_client
    csrf = client.login_as("alice")
    url = f"/api/v1/jobs/{job.id}"
    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["etag"] == '"1"'
    assert response.json()["job"] == {"id": str(job.id), "status": "queued"}
    assert client.post(url + "/pause").status_code == 403
    headers = {"X-CSRF-Token": csrf, "Idempotency-Key": "pause"}
    assert client.post(url + "/pause", headers=headers).status_code == 428
    headers["If-Match"] = '"1"'
    paused = client.post(url + "/pause", headers=headers)
    assert paused.status_code == 200 and paused.json()["status"] == "paused"
    assert client.post(url + "/pause", headers=headers).json() == paused.json()
    headers["Idempotency-Key"] = "stale-resume"
    assert client.post(url + "/resume", headers=headers).status_code == 409
    headers.update({"Idempotency-Key": "resume", "If-Match": '"2"'})
    assert client.post(url + "/resume", headers=headers).json()["status"] == "queued"
    assert client.get("/api/v1/health/overview").status_code == 403
    jobs.scenario.revoke(jobs.grant)
    assert client.get(url).status_code == 404
    assert client.get("/api/v1/jobs").json() == {"items": [], "next_cursor": None}
    headers.update({"Idempotency-Key": "pause", "If-Match": '"1"'})
    assert client.post(url + "/pause", headers=headers).status_code == 404


def test_revocation_waiting_on_batch_cannot_deadlock_ledger(jobs):
    from threading import Event
    from lzo.worker.service import checkpoint
    job = jobs.create()
    ctx = jobs.runner().claim()
    revoking = Event()
    def revoke():
        revoking.set()
        jobs.scenario.revoke(jobs.grant)
    with ThreadPoolExecutor(max_workers=1) as pool:
        with ctx.batch():
            future = pool.submit(revoke)
            assert revoking.wait(5)
            jobs.effect(ctx, 1)
            checkpoint(ctx.job_id, ctx.lease_token, {"offset": 1})
        future.result(timeout=10)
    with pytest.raises(ApiError):
        with ctx.batch():
            jobs.effect(ctx, 2)
    assert jobs.effects() == [1]


def test_heartbeat_inside_batch_uses_the_same_transaction(jobs):
    from sqlalchemy import create_engine
    from lzo.worker.runner import Runner
    jobs.create()
    # A real server lock timeout makes accidental nested-session deadlock fail
    # promptly, without terminating another process or mocking the database.
    engine = create_engine(jobs.settings.database_url, connect_args={
        "options": f"-csearch_path={jobs.schema} -clock_timeout=500ms"})
    try:
        ctx = Runner(engine, jobs.settings).claim()
        with ctx.batch():
            jobs.effect(ctx, 1)
            ctx.heartbeat()
        assert jobs.effects() == [1]
    finally:
        engine.dispose()


def test_batch_installs_database_time_and_idle_budgets(jobs):
    jobs.settings = jobs.settings.model_copy(update={"job_batch_timeout_seconds": 2})
    jobs.create()
    ctx = jobs.runner().claim()
    with ctx.batch():
        values = ctx.uow.session.execute(text(
            "SELECT current_setting('statement_timeout'), current_setting('idle_in_transaction_session_timeout')")).one()
        assert values == ("2s", "2s")


def test_result_reference_requires_registered_route_and_current_target_view(jobs):
    from lzo.worker.service import JobResultRef, register_result_kind
    register_result_kind("test.object", "/api/v1/objects/{resource_id}")
    job = jobs.create()
    ctx = jobs.runner().claim()
    with pytest.raises(ApiError, match="INVALID_RESULT_REF"):
        with ctx.batch():
            ctx.set_result(JobResultRef(kind="test.object", resource_id=jobs.resource_id,
                                       api_path="https://example.invalid/steal"))
    with ctx.batch():
        ctx.set_result(JobResultRef(kind="test.object", resource_id=jobs.resource_id,
                                   api_path=f"/api/v1/objects/{jobs.resource_id}"))
    assert jobs.view(job.id).result_ref.resource_id == jobs.resource_id
    jobs.scenario.revoke(jobs.grant)
    with pytest.raises(ApiError) as error:
        jobs.view(job.id)
    assert error.value.status == 404


def test_checkpoint_is_fenced_again_at_end_of_batch(jobs):
    from lzo.worker.service import checkpoint
    job = jobs.create()
    ctx = jobs.runner().claim()
    with pytest.raises(ApiError, match="LEASE_EXPIRED"):
        with ctx.batch():
            jobs.effect(ctx, 1)
            ctx.uow.session.execute(text("UPDATE jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=:id"), {"id": job.id})
            checkpoint(ctx.job_id, ctx.lease_token, {"offset": 1})
    assert jobs.effects() == []


def test_optional_source_failure_does_not_change_core_ready(jobs, api_client):
    from lzo.worker.health import overview, record_source_health
    from lzo.auth.models import grants, roles
    role = uuid4()
    with jobs.transaction() as uow:
        uow.session.execute(roles.insert().values(id=role, name="ops", actions=["operations.view"], revision=1))
        uow.session.execute(grants.insert().values(id=uuid4(), user_id=jobs.scenario.users["alice"],
            role_id=role, security_class="operations", global_scope=True, revision=1))
        record_source_health(uow, uuid4(), "offline", "SOURCE_UNAVAILABLE")
        uow.commit()
    with jobs.transaction() as uow:
        health = overview(uow, jobs.actor(), jobs.settings)
        assert health.failed_sources == 1
        assert health.failed_source_details[0].error_code == "SOURCE_UNAVAILABLE"
        assert health.failed_source_details[0].status == "offline"
        assert health.projection_watermark == 0
    assert api_client.get("/health/ready").status_code == 200
