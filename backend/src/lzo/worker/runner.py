"""Bounded worker attempts and short atomic domain/checkpoint batches."""
from contextlib import contextmanager
from datetime import timedelta
import importlib
import logging
import signal
from threading import Event
import time
from uuid import uuid4

from sqlalchemy import and_, func, insert, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from lzo.auth.permissions import current_version, permission_context
from lzo.config import Settings
from lzo.contracts import ActorContext
from lzo.db import PgUnitOfWork, create_db_engine
from lzo.errors import ApiError, Conflict
from lzo.governance.revisions import lock_ledger
from lzo.worker.leases import fenced_row
from lzo.worker.models import job_dependencies, jobs
from lzo.worker.service import (_batch, _execution, append_job_event, check_execution, check_json,
                                handlers, record_attempt, resource, validate_result, resolve_action)
from lzo.auth.permissions import require_permission

logger = logging.getLogger(__name__)


class JobContext:
    """A handler may perform database effects only inside batch(), via uow.

    Each batch reloads the durable cursor and fresh initiator authority. External
    writes must use an idempotent adapter keyed by job/batch identity; a database
    lease alone cannot make a remote write atomic.
    """
    def __init__(self, runner, row, actor):
        self.runner = runner
        self.job_id, self.lease_token = row["id"], row["lease_token"]
        self.actor, self.bundle_id = actor, row["bundle_id"]
        self.payload, self.cursor = row["payload"], row["cursor"]
        self.uow = None
        self._row = row
        self._batches = 0

    @contextmanager
    def batch(self):
        if self.uow is not None:
            raise Conflict("JOB_BATCH_NESTED", "Job batches cannot be nested")
        if self._batches >= self.runner.settings.job_max_batches_per_run:
            raise ApiError("JOB_YIELD", 409, "Worker batch budget reached")
        previous_cursor = self.cursor
        with PgUnitOfWork(self.runner.engine) as uow:
            timeout = self.runner.settings.job_batch_timeout_seconds
            # Bound lock waits and release locks if a handler stalls between
            # statements; neither setting depends on a worker's local clock.
            uow.session.execute(text("""SELECT set_config('statement_timeout', :timeout, true),
                set_config('idle_in_transaction_session_timeout', :timeout, true)"""),
                {"timeout": str(timeout * 1000)})
            started = time.monotonic()
            self.actor = self.runner.authorize(uow, self._row)
            lock_ledger(uow)
            row = fenced_row(uow, self.job_id, self.lease_token)
            self._row = dict(row)
            self.cursor = row["cursor"]
            self.uow = uow
            token = _batch.set(self)
            try:
                with permission_context(uow):
                    yield self
                    fenced_row(uow, self.job_id, self.lease_token)
                    if time.monotonic() - started > timeout:
                        raise Conflict("JOB_BATCH_TIMEOUT", "Worker batch time budget exceeded")
                    uow.commit()
                self._batches += 1
            except BaseException:
                self.cursor = previous_cursor
                raise
            finally:
                self.uow = None
                _batch.reset(token)

    def checkpoint(self, cursor):
        if self.uow is None:
            raise Conflict("JOB_BATCH_REQUIRED", "Checkpoint needs an active batch")
        check_json(cursor, self.runner.settings)
        row = fenced_row(self.uow, self.job_id, self.lease_token)
        self._row = append_job_event(self.uow, self.actor, row, "job.checkpoint",
                                    cursor=cursor, heartbeat_at=func.clock_timestamp())
        self.cursor = cursor

    def set_result(self, result):
        if self.uow is None:
            raise Conflict("JOB_BATCH_REQUIRED", "Result needs an active batch")
        validate_result(result)
        require_permission(self.actor, "view", resource(self.uow, result.resource_id), uow=self.uow)
        self.uow.session.execute(pg_insert(job_dependencies).values(job_id=self.job_id,
            resource_id=result.resource_id).on_conflict_do_nothing())
        row = fenced_row(self.uow, self.job_id, self.lease_token)
        self._row = append_job_event(self.uow, self.actor, row, "job.result",
                                    result_ref=result.model_dump(mode="json"))

    def heartbeat(self):
        self.runner.heartbeat(self)


class Runner:
    def __init__(self, engine, settings, *, worker_id=None):
        self.engine, self.settings = engine, settings
        self.worker_id = worker_id or str(uuid4())
        if not self.worker_id or len(self.worker_id) > 128:
            raise ValueError("Invalid worker identity")

    def authorize(self, uow, row):
        actor = ActorContext(row["initiator_id"], f"job-{row['id']}",
                             current_version(uow, lock=True))
        check_execution(uow, actor, row)
        self.validate_policy(uow, row)
        return actor

    def validate_policy(self, uow, row):
        handler = handlers.get(row["kind"])
        if handler is None:
            raise ApiError("JOB_HANDLER_UNREGISTERED", 409, "Handler is not registered")
        refs = tuple(dict.fromkeys(handler.references(row["payload"])))
        if row["bundle_id"] is not None:
            refs = tuple(dict.fromkeys((*refs, row["bundle_id"])))
        if resolve_action(handler, uow, row["payload"]) != row["action"] or [str(ref) for ref in refs] != row["reference_ids"]:
            raise ApiError("JOB_POLICY_CHANGED", 409, "Handler scope changed")

    def claim(self):
        with PgUnitOfWork(self.engine) as uow:
            # Administration takes exclusive authorization_state then ledger.
            # All worker mutations follow that same ordering.
            version = current_version(uow, lock=True)
            lock_ledger(uow)
            now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
            candidates = uow.session.execute(select(jobs).where(or_(
                and_(jobs.c.status == "queued", jobs.c.available_at <= now),
                and_(jobs.c.status == "running", jobs.c.lease_expires_at <= now)))
                .order_by(jobs.c.available_at, jobs.c.created_at, jobs.c.id)
                .limit(self.settings.job_claim_scan_limit).with_for_update(skip_locked=True)).mappings().all()
            for row in candidates:
                actor = ActorContext(row["initiator_id"], f"job-{row['id']}", version)
                if row["status"] == "running":
                    record_attempt(uow, actor, row, "expired", "LEASE_EXPIRED")
                failure = None
                try:
                    check_execution(uow, actor, row)
                except ApiError as error:
                    failure = error.code if error.code in ("JOB_POLICY_CHANGED", "JOB_HANDLER_UNREGISTERED") else "AUTHORIZATION_REVOKED"
                if failure is None:
                    try:
                        self.validate_policy(uow, row)
                    except ApiError as error:
                        failure = error.code
                if failure is None and row["retry_count"] >= row["max_attempts"]:
                    failure = "ATTEMPTS_EXHAUSTED"
                if failure:
                    append_job_event(uow, actor, row, "job.failed", status="failed",
                                     error_code=failure, lease_token=None, lease_expires_at=None)
                    continue
                lease_token = uuid4()
                claimed = append_job_event(uow, actor, row, "job.started",
                    status="running", lease_token=lease_token, worker_id=self.worker_id,
                    lease_expires_at=now + timedelta(seconds=self.settings.job_lease_seconds),
                    heartbeat_at=now, attempts=row["attempts"] + 1,
                    retry_count=row["retry_count"] + 1, error_code=None)
                record_attempt(uow, actor, claimed, "started")
                uow.commit()
                return JobContext(self, claimed, actor)
            uow.commit()
            return None

    def heartbeat(self, ctx):
        if ctx.uow is not None:
            # The active batch already owns authorization, ledger and job locks.
            # A second connection would wait on this very transaction.
            self._renew(ctx.uow, ctx)
            return
        with PgUnitOfWork(self.engine) as uow:
            self.authorize(uow, ctx._row)
            lock_ledger(uow)
            self._renew(uow, ctx)
            uow.commit()

    def _renew(self, uow, ctx):
        fenced_row(uow, ctx.job_id, ctx.lease_token)
        now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
        uow.session.execute(update(jobs).where(jobs.c.id == ctx.job_id).values(
            heartbeat_at=now, lease_expires_at=now + timedelta(seconds=self.settings.job_lease_seconds)))

    def finish(self, ctx, error=None):
        with PgUnitOfWork(self.engine) as uow:
            actor = ActorContext(ctx.actor.subject_id, ctx.actor.request_id, current_version(uow, lock=True))
            denied = False
            denial_code = None
            try:
                check_execution(uow, actor, ctx._row)
            except ApiError as failure:
                denied = True
                denial_code = failure.code if failure.code in ("JOB_POLICY_CHANGED", "JOB_HANDLER_UNREGISTERED") else "AUTHORIZATION_REVOKED"
            lock_ledger(uow)
            row = fenced_row(uow, ctx.job_id, ctx.lease_token)
            code = denial_code if denied else None
            if error is not None and code is None:
                from lzo.synchronization.delivery import DeliveriesPending, DeliveriesFailed
                # Never persist raw exception messages, URLs, payloads or credentials.
                code = ("DOMAIN_DELIVERY_FAILED" if isinstance(error,DeliveriesFailed) else
                    "DOMAIN_DELIVERY_PENDING" if isinstance(error,DeliveriesPending) else
                    "JOB_YIELD" if isinstance(error, ApiError) and error.code == "JOB_YIELD" else "HANDLER_FAILED")
            yield_job = code in ("JOB_YIELD","DOMAIN_DELIVERY_PENDING")
            retry = code is not None and not denied and code != "DOMAIN_DELIVERY_FAILED" and (yield_job or row["retry_count"] < row["max_attempts"])
            state = "queued" if retry else ("failed" if code else "succeeded")
            delay = 5 if code == "DOMAIN_DELIVERY_PENDING" else 0 if yield_job else min(self.settings.job_retry_cap_seconds,
                self.settings.job_retry_base_seconds * 2 ** min(row["retry_count"] - 1, 30))
            now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
            record_attempt(uow, actor, row, state, code)
            append_job_event(uow, actor, row, "job." + state, status=state,
                lease_token=None, lease_expires_at=None, error_code=None if code == "JOB_YIELD" else code,
                retry_count=row["retry_count"] - 1 if yield_job else row["retry_count"],
                available_at=now + timedelta(seconds=delay))
            uow.commit()

    def run_once(self):
        ctx = self.claim()
        if ctx is None:
            return False
        error = None
        token = _execution.set(ctx)
        try:
            # Authorization is repeated immediately before handing payload to the handler.
            with PgUnitOfWork(self.engine) as uow:
                self.authorize(uow, ctx._row)
            handlers[ctx._row["kind"]].call(ctx)
        except Exception as failure:
            error = failure
        finally:
            _execution.reset(token)
        try:
            self.finish(ctx, error)
        except Conflict as failure:
            if failure.code != "LEASE_EXPIRED":
                raise
        return True


def load_handlers(settings):
    """Only trusted deployment configuration can load handler modules."""
    import lzo.gis.service
    import lzo.catalog.service
    import lzo.telemetry.service
    for module in filter(None, (name.strip() for name in settings.worker_handler_modules.split(","))):
        importlib.import_module(module)


def poll_once(runner):
    """Scheduling is bounded and independent of ordinary queue progress."""
    from lzo.ingestion.scheduler import Scheduler
    try:
        with PgUnitOfWork(runner.engine) as uow:
            now=uow.session.execute(select(func.clock_timestamp())).scalar_one()
        Scheduler(runner.engine,runner.settings).tick(now)
    except Exception:
        logger.error('Source scheduler tick failed; continuing ordinary job dispatch')
    return runner.run_once()


def main():
    from lzo.storage.migration import runtime_settings
    from lzo.storage.maintenance import record_writer
    settings = runtime_settings()
    record_writer('worker')
    load_handlers(settings)
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.delivery import poll_deliveries
    registry = StorageRegistry.from_settings(settings)
    engine = registry.engine("core")
    runner = Runner(engine, settings)
    stop = Event()
    for event in (signal.SIGTERM, signal.SIGINT):
        signal.signal(event, lambda *_: stop.set())
    try:
        while not stop.is_set():
            try:
                if settings.storage_mode == "split":
                    poll_deliveries(registry)
                busy = poll_once(runner)
            except Exception:
                logger.error("Worker poll failed; retrying without exposing connection details")
                busy = False
            if not busy:
                stop.wait(settings.job_poll_seconds)
    finally:
        registry.close()
