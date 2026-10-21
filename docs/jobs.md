# Durable jobs

F05 provides a PostgreSQL queue and fenced execution protocol. S01 supplies
the real RDF adapter and registers `rdf.project` through the default trusted
`lzo.worker.handlers` module.

## Registration and scope

Both API and worker load the comma-separated modules in
`LZO_WORKER_HANDLER_MODULES`. Modules are trusted deployment configuration,
never names supplied by a job request. Each module calls:

```python
register_handler("domain.operation", handle, references=references, action="edit")
```

The default `references` reads a UUID from `payload["resource_id"]`. A custom
resolver must enumerate **every protected resource referenced by its payload**,
with the primary operation resource first. `bundle_id`, when present, is also
a protected reference. References and required action are frozen at enqueue;
changed registrations fail existing work with `JOB_POLICY_CHANGED`. Result
resources become additional immutable visibility dependencies.

An operation can supply `action=resolver` where the trusted callable accepts
`(uow, payload)` and returns one action string from authoritative application
state. Enqueue freezes that resolved action in the job; execution and job
controls resolve it again and reject policy changes. Existing fixed action
strings retain their behavior. The RDF handler resolves its action from the
actual immutable event and registered domain producer, never a payload action
flag. This supports `schema.publish` without giving viewers control of jobs or
requiring an unrelated generic edit grant.

The initiator remains the execution identity. Each claim, dispatch and batch
loads a fresh authorization version and verifies that the user is enabled,
has the required operation on the primary resource, and can view every
dependency. Revocation prevents further batches; there is no administrator
fallback or reassignment. Worker attempts record both initiator and worker
identity. Workers use the application database credential.

## Enqueue and batches

```python
with PgUnitOfWork(engine) as uow:
    validate_actor(uow, actor)
    # Domain revision and enqueue share this transaction.
    ref = enqueue(uow, actor, "domain.operation", payload, bundle_id=None)
    uow.commit()
```

When composing a domain revision before enqueue, acquire and validate current
authorization **before** the first ledger write. The common lock order is
authorization state → ledger → job/resource/quota locks. Enqueue never commits
the caller's transaction. `uow.job_settings` may supply explicit settings;
otherwise environment-backed application settings apply.

`enqueue_outbox(uow, actor, kind, outbox_id, payload, bundle_id)` accepts a real
outbox event whose resource is among the declared references. Repeated delivery
deduplicates by kind/event in the same transaction. A changed actor, payload or
bundle produces an idempotency conflict.

```python
def handle(ctx):
    while ctx.cursor.get("offset", 0) < total:
        with ctx.batch():
            # Cursor is reloaded inside the fenced transaction.
            offset = ctx.cursor.get("offset", 0)
            write_domain_batch(ctx.uow, ctx.actor, offset)
            checkpoint(ctx.job_id, ctx.lease_token, {"offset": offset + 1})
```

`JobContext` exposes `job_id`, `lease_token`, `actor`, `bundle_id`, `payload`,
and `cursor`; its `uow` exists only inside `batch()`. The batch owns commit:
handlers must not call `commit()` or `rollback()` themselves. Domain effects
and checkpoint must use that same UoW. Do not perform effects before entering
the batch. Exceptions roll back both; PostgreSQL wall clock fences the lease
before effects and again before commit.

Calling public `checkpoint(...)` directly from a registered handler opens
a standalone fenced progress transaction. That form is not atomic with
separate domain effects. Calls outside the executing handler/batch are rejected.
`ctx.heartbeat()` renews a still-valid lease; inside a batch it uses the same
UoW. Heartbeat cannot revive an expired or superseded lease.

Database statement/idle limits and elapsed batch budgets bound lock holding.
The batch-count budget yields work back to the queue without consuming a failure
retry. Handler exceptions persist only a stable error code, with capped
exponential backoff. Crashed attempts are reclaimed after expiry with a new
lease token. Exhausted work is durably failed. Pausing invalidates the current
lease and retains committed progress; it stops new batches. Pausing a running
attempt with a still-valid lease releases only that attempt's reserved failure
allowance. Pausing an already failed queued retry or an expired running attempt
does not refund a failure/crash. Total attempts and append-only attempt history
are retained. Explicit retry resets the failure budget.

Remote writes, including Fuseki updates, must be implemented by an idempotent
adapter using stable job/batch keys. Database fencing cannot undo or atomically
commit a remote service effect. A worker may compute outside a batch, but must
revalidate/reload before committing its result.

## Reads, results and operations

The API exposes `GET /api/v1/jobs`, `GET /api/v1/jobs/{id}` and
`POST /api/v1/jobs/{id}/{pause,resume,retry}`. Writes require the normal session
CSRF controls, `Idempotency-Key`, and quoted `If-Match`. Current visibility and
operation rights are checked before idempotency replay. List filtering happens
in SQL before pagination. History events declare their protected dependencies
using the F04 generic history mechanism.

The explicit service is `JobService(uow, settings)`. The public
`set_job_state(actor, job_id, state, expected_revision)` facade uses the UoW
bound by `permission_context(uow)` and leaves commit to the caller.

Result modules register a fixed route template:

```python
register_result_kind("domain.result", "/api/v1/results/{resource_id}")
with ctx.batch():
    ctx.set_result(JobResultRef(
        kind="domain.result",
        resource_id=result_id,
        api_path=f"/api/v1/results/{result_id}",
    ))
```

Only matching registered `/api/v1/.../{resource_id}` resource paths are
accepted. They are references, not URL proxies. The target route must perform
its own current authorization. Loss of target visibility hides the job and
referencing history even when primary-resource visibility remains.

`GET /api/v1/health/overview` requires an explicit global `operations.view`
grant with security class `operations`. It reports queue/lease counts,
integer ledger/input/projection/inference watermarks, bounded failed-source
identifiers and stable codes, and configured budgets. It returns no connection
strings, credentials or exception messages. `record_source_health` is an
internal producer API; optional-source status never alters core readiness.

`LZO_JOB_*` settings control lease length, retry attempts/base/cap, poll
interval, claim scan budget, statement/idle/batch time, batches per run, JSON
size and queue admission. These are operational limits, not capacity claims.
Keep batch time comfortably below lease duration and renew leases between
long-running batches.

## Acceptance

`tests/integration/test_job_recovery.py` uses isolated real PostgreSQL schemas,
two competing workers, and a real `python -m lzo.worker` subprocess kill/restart.
Its synthetic handler writes a test-only table atomically with checkpoints.
The production registry contains the real S01 projection handler and no
synthetic projection handler.
Native-service acceptance is supplemental; Compose persistence and offline
delivery gates still require a Docker-capable host.
