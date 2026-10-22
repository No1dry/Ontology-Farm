# Checked migration and isolated platform recovery

The semantic identity/governance database remains canonical during legacy GIS and
blob migration. Migration never removes legacy geometry, original files, domain
history or accepted events. `preview` creates a durable plan and claims a fresh
GIS schema; backfill checkpoints identify exact event/version digests. Use a new
S3 key prefix (`LZO_S3_KEY_PREFIX`) or a new local file root. Empty prefix preserves
the pre-migration object key format, but is refused as a migration destination.
The controller exclusively claims the blob namespace with a plan/restore UUID:
S3 uses a conditional `If-None-Match: *` ownership object; a local destination must
not already exist. Backfill/startup checks that exact owner. Competing plans,
changed ownership and previously claimed partial namespaces are refused. Resume
the existing migration plan; a failed new restore requires a fresh namespace.

This is a platform-data backup. It includes platform identities, permissions,
governance/history, source connection *definitions*, accepted source evidence,
outbox/deliveries, professional stores, retained upload parts and RDF. It does not
back up independent business source databases, the OIDC provider/realm, SMTP,
external device state or a whole enterprise. Recovery also needs the existing
pinned executables and separately protected configuration/credential files.

## Maintenance boundary

Use a stopped-writer maintenance window. Before starting the owned API and worker,
set `LZO_WRITER_WORKSPACE` to the absolute owned workspace and
`LZO_WRITER_OWNERSHIP_DIR` to a private directory inside it. API lifespan and the
worker entry point record their PID, creation time, executable and workspace.
Those processes include HTTP ingest/upload acceptance, scheduled ingestion,
professional delivery and RDF publication. Stop both processes and disable their
automatic restart before maintenance. Never copy or invent ownership records.

The controller verifies those exact incarnations are gone, refuses even idle
connections from the four provisioned application roles, and holds NOWAIT
`SHARE ROW EXCLUSIVE` locks on every table in each declared source schema.
Backup, cutover, rollback and publication recovery require all four stores. Their
physical database/schema identities and migration versions are bound at preview,
compared with the verified package at activation, and checked again at startup.
Replacing a catalog/time-series namespace under the same URL requires a new
governed plan; a configuration digest alone does not certify its identity.
The independent owner connections used for
dumping cannot modify their locked sources from another transaction. A refused
inner operation rolls back its savepoint while retaining the outer locks.

Keep S3/Fuseki private to these owned services and stop direct maintenance writers.
The controller never kills a process, changes role privileges or alters host
networking. Immutable blob reads verify length/hash. Full RDF exports before and
after capture must match; accepted RDF heads and retained generations are checked
independently. An untracked external administrator remains outside this operational
trust boundary; a marker alone is not a distributed write fence.

## Operator sequence

All environment files, authority keys, snapshots and activation JSON contain or
protect sensitive data. Keep them under ignored private runtime storage, with
operator-only access; never commit them or print a DSN. The examples use paths and
platform UUIDs supplied by the operator, not embedded credentials.

1. Apply the append-only core migrations through the configured core owner and
   professional migrations through each domain owner. Check actual migration
   versions; do not downgrade. Provision a new empty GIS schema and target blob
   prefix. Prepare complete private `source.env` and `target.env`; core connection
   identity must be the same in both. Other runtime settings are digest-bound.
2. Run `multistore_migrate.py preview`, then repeat `backfill --plan <UUID>` until
   `remaining` is zero. Repeating after a process loss reuses exact frozen
   envelopes and immutable target receipts. A limit is a checkpoint boundary,
   not a statement that later accepted events are absent.
3. Run `reconcile`. It verifies every historical/current geometry, target inbox,
   head, accepted blob version and upload part. New input or a changed target
   requires catch-up and a new reconciliation. Preserve source and target on any
   refusal.
4. Stop and verify writers as above. Create a coordinated backup using the target
   configuration (the staged GIS/blob stores already contain the checked legacy
   versions), the plan UUID and all four owner URLs. Retain the separate authority
   key outside the backup. A caller-supplied `status` or dump file cannot certify
   success. Creation independently verifies actual inventories, receipts,
   accepted blob/part bytes and RDF before recording its core receipt.
5. Attach that backup with `reconcile --backup <manifest> --approval-command`.
   Restart the source API for governance approval only (keep ingestion, upload,
   scheduled workers and business writers paused). Use the authenticated API to preview/submit the exact
   `storage.activate` command. The initiating user requires `storage.manage`; a
   different current reviewer requires `storage.review`. Current authority,
   plan epoch, data position, targets, reconciliation and backup digest are bound
   and rechecked at execution. Reconcile/approve again after any material change.
6. Stop that API again and verify its latest ownership record. With all writers
   stopped, run `cutover` with the plan, initiating actor,
   approved record, writer directory/workspace and a **new** private activation
   path. It rechecks actual sources/targets and backup material under the locks,
   durably stages the complete private configuration, commits governance/audit
   state, then publishes the artifact atomically without replacing an existing
   file. A bad parent path fails before the transition commits.
   It does not change a running process. Set `LZO_ACTIVATION_CONFIG` to that file
   before starting both API and worker. Startup checks the committed activation
   and exact physical targets. Verify actual application reads, current rights,
   delivery status and hashes before ending maintenance.

If publication fails after commit, the CLI returns `publication_pending` with
`transition_committed: true` and retains `<activation>.pending`. Keep writers
stopped. Run `multistore_migrate.py recover-activation` with the same source/target
environment files, `--plan`, current authorized `--actor`, `--writers`,
`--workspace` and the original `--activation` path. No new approval or second
activation is created. Recovery validates the committed state/audit payload,
exact settings, original publication path and all four protected targets before
publishing the staged bytes. Repeating recovery validates an already published
artifact and succeeds without changing the plan epoch or audit history. A stage
from an uncommitted transition, tampered settings, changed target or stale plan
is refused. Never use the pending file as runtime configuration, copy an
activation to another path, overwrite a foreign file, or manually edit plan state.

Command entry points (run their `--help` for all required arguments):

```powershell
.venv/Scripts/python.exe scripts/multistore_migrate.py --help
.venv/Scripts/python.exe scripts/multistore_backup.py --help
```

`multistore_backup.py create` requires `--env`, `--actor`, `--writers`,
`--workspace`, `--destination`, `--pg-bin`, `--authority-key` and, for migration,
`--migration-plan`. `verify` reads and checks actual material, including the
signature, archive structure, inventory digests and exact reference closure.
Keep the authority key separately; it permits verification/recovery when the
original core is unavailable. Migration activation additionally requires the
matching authoritative core backup receipt.

## Restore and rollback

`multistore_backup.py restore` requires the manifest, authority key, pinned PG
binary directory, a new destination, a new `lzo_restore_<8..24 lowercase
letters/digits>` namespace and a private admin environment file containing
`LZO_RESTORE_ADMIN_DATABASE_URL`. It creates four new databases on the explicitly
chosen server, a new object prefix and a separate Fuseki dataset. Existing names
are refused. Original domain owner/app roles and ACL/function boundaries are
retained; this deployment recipe uses the existing provisioned role credentials.
Every owner/runtime SQL URL uses the explicit admin destination host/port and
new database name, retaining domain credentials and schema options. Hidden URL
transport overrides (`host`, `hostaddr`, `service`, etc.) and ambient
`PGHOSTADDR`/`PGSERVICE`/`PGSERVICEFILE` are refused; remove them rather than
redirecting recovery implicitly. The destination admin must read
`pg_control_system()`. Before any archive is loaded, every owner and runtime
connection must read the unique admin-created restore binding and destination
cluster identifier from the new database. A database with the same name on the
source server is never selected by this operation. New destination servers need
the provisioned domain roles/credentials and matching extension versions first.

Restore verifies actual SQL table digests, partition contents, role/function
security, all referenced bytes, exact accepted domain receipts and RDF current
and historical generations. Full RDF dataset comparison retains blank-node
relationships across named graphs. It publishes a private configuration only
after verification. It does not promote that isolated namespace to production.
Partial targets remain marked failed; inspect them and use a fresh namespace for
retry. No source database, prefix, graph or existing target is dropped.

For compatible GIS/blob rollback, stop writers, reconcile and create a fresh
coordinated backup including every post-cutover write. Submit the exact rollback
command for independent approval. `rollback` copies every accepted blob/part
back through the local adapter and rebuilds current legacy geometry from the
unchanged immutable core history before publishing legacy configuration.
Rolled-back plans are terminal. Subsequent reconciliation is refused; a stale
reconciliation cannot overwrite a winning rollback or invalidate its artifact.

Global legacy mode maps catalog and time-series access into core. If accepted
catalog/telemetry or GIS asset/location data cannot be represented there,
rollback refuses with `RESTORE_REQUIRED`. Preserve both stores, create/verify a
fresh coordinated backup containing those new writes, restore to a new compatible
split namespace and verify its application/current/history/reference reads.
Refusal alone is not evidence that recovery succeeded.

SQL inventories list tables independently, including partition parents and
children. Their row counts overlap and must not be added as independent workload
or event counts. Physical orphan/negative fixtures can be retained in source
domain schemas or the RDF dataset; accepted reference-completeness findings are
scoped to the captured core ledger, not every physical row's business validity.

## Focused acceptance reproduction

Use the worktree's `.venv` and its explicitly provisioned PostgreSQL/PostGIS,
private S3 and Fuseki services. Private dotenv files must contain that worktree's
runtime/domain-owner URLs and the pinned native PostgreSQL tool directory must be
recorded in its owned runtime inventory. Tests create isolated core/GIS schemas,
fresh object prefixes and named restore databases/datasets. They retain partial
restore targets for diagnosis; they never delete source services or change roles.
Do not run service tests concurrently: quiescence deliberately refuses any
connection from a writer role in the same owned database.

This equivalent environment-loading command uses checked-in tests and does not
depend on an ignored task-specific Python runner. Replace the two private dotenv
paths with the owned provisioning outputs. Never print their contents.

```powershell
@'
import os
from dotenv import load_dotenv
from cryptography.fernet import Fernet
load_dotenv('.runtime/multistore/services.env', override=True)
load_dotenv('.runtime/multistore/maintenance.env', override=True)
os.environ['LZO_SESSION_ENCRYPTION_KEY'] = Fernet.generate_key().decode()
import pytest
raise SystemExit(pytest.main([
    'backend/tests/integration/test_multistore_migration.py',
    'backend/tests/integration/test_multistore_restore.py',
    '-q', '--basetemp=.runtime/multistore-acceptance-new-run'
]))
'@ | .venv/Scripts/python.exe -
```

Supply a **new** owned `--basetemp` on each retained acceptance run. These are
synthetic integration fixtures using real production storage/recovery paths;
their success is not production promotion, an equipment claim or OIDC disaster
recovery evidence.
