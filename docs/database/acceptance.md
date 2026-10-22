# Reproducible multi-store acceptance and operations

This guide covers the implemented multi-store change. It does not certify the whole original P0+P1 product, production migration, real field equipment or a Docker deployment. Earlier migration/restore evidence remains in [task6ab-acceptance.json](task6ab-acceptance.json) and [the recovery runbook](migration-recovery.md). Current functional/benchmark results are recorded separately; overlapping task counts must not be summed.

Use Python 3.12.7, Node 24.22.0 and the exact [version/archive/image pins](../../infra/versions.lock.json). Install `backend/requirements.lock` in a worktree-owned `.venv`; npm uses the checked-in lockfiles. All credentials, generated settings, source fixtures, original files, dumps and logs belong in ignored `.runtime`. Never print dotenv contents, admin JSON or process command lines. Tool caches may be borrowed read-only; no package installation or service data writes may target another worktree.

## Owned native prerequisites

The retained native deployment owns PostgreSQL **127.0.0.1:25432** with four independent databases/accounts, S3 **28333**, and Fuseki **23030**. Ownership markers bind this absolute worktree and the specific data/state directories. `multistore_native.py init --tool-cache <cache> --port 25432` is only for a **new** cluster; existing owned state uses `start/status/restart`, never re-init. `object_store_native.py` owns its separate S3 lifecycle. Native lifecycle is supplemental evidence, not Compose.

For a new owned identity fixture (Keycloak writes into its installation tree, so its binaries are copied into an owned runtime):

```powershell
.venv/Scripts/python.exe scripts/multistore_prerequisites.py start-idp --env .runtime/multistore/services.env --tool-root ../p0-p1/.tools --java-home '<existing Java 21 home>' --state .runtime/acceptance-services
```

This binds Keycloak **28080** and management **29000** to loopback. Its private `idp.env` supplies actual discovery/client configuration and a stable session key. Native synthetic identity uses a fresh local dev-file database, not the Compose PostgreSQL identity deployment. Email/external invitations remain disabled. Native cleartext HTTP is restricted to loopback and is not a production identity configuration. Stop it through the same owned `stop-idp` command. Do not invoke the old `native_dev.py` hardcoded restart against this environment.

Every command explicitly loads private dotenv files. Later files override earlier ones. The public wrapper never rewrites them or prints their values:

```powershell
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/acceptance-services/idp.env -- backend/src/lzo/migrate.py
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/acceptance-services/idp.env -- scripts/multistore_inventory.py --output .runtime/schema-inventory.json
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/acceptance-services/idp.env --tool-root ../p0-p1/.tools --matrix scripts/acceptance6c-matrix.json -- pytest -q -o usefixtures=split_delivery_worker --basetemp .runtime/acceptance-UNIQUE
```

`--basetemp` must be a new child of this worktree `.runtime`. The selected JSON matrix covers three synthetic agricultural scenes, shared identity/dynamic extension, inference/retraction, SPARQL/current authorization, concurrent edits/history immutability, rule version publication, actual read-only PostgreSQL/MySQL incremental/TLS/snapshot/delete handling, local map tiles and real provider security checks. It is intentionally not a rerun or passing claim for the interrupted 856-case suite. `split_delivery_worker` models the deployed GIS-delivery-before-RDF loop using actual domain handlers. Source fixtures adapt only their tool root; they initialize isolated owned native PG/MySQL with random loopback ports, TLS and exact cleanup.

That representative command uses split SQL/Fuseki with **local** private bytes. The focused S3/offline and native restart commands also load `--env .runtime/object-store/services.env`; do not infer S3 coverage from the large local-provider matrix. Backend settings/results explicitly identify the provider.

Focused cross-boundary regressions are `test_attachment_heads.py` and `test_multistore_boundaries.py`. They prove public direct/resumable attachment→optimistic edit→GIS read, append-only legacy repair/refusal, exact older catalog reference receipt fencing, lost core acknowledgement, exhausted terminal retry and current authority. Their outputs and the representative matrix are distinct evidence.

The actual focused selection is checked in as `scripts/acceptance6c-focused.json` (attachment cases expand to direct/resumable plus legacy repair). Reproduce its S3/offline boundary with:

```powershell
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --offline --matrix scripts/acceptance6c-focused.json -- pytest -q -o usefixtures=split_delivery_worker --basetemp .runtime/acceptance-s3-offline-UNIQUE
```

## Owned daemon/store restart

The production defaults are a **30-second worker batch / 60-second lease**. The real two-scene acceptance flow exhausted even its old120-second fixture budget: repeated RDF batches took about162seconds, leaving RDF published while SQL correctly retained its prior head/watermark. These defaults are **not qualified for that flow**. The checked-in [native acceptance workload profile](../../infra/native/acceptance-workload.env.example) explicitly sets **240-second batch / 300-second lease**; the lease exceeds the batch with60seconds of margin. Native API/worker and the benchmark load that same profile. Only the slow shared-scene test overrides its fixture to those values; other fixtures retain their declared budgets. Passing under this profile establishes bounded correctness for this workload, not production capacity or latency acceptance. See `task6c-timeout.json` and final results for actual outcomes.

Only after all active test clients have stopped, start the public deployment using the explicit split+S3 profile. The persistence runner refuses a local provider. Before its first S3 startup, verify accepted public-core local artifact references are absent or already migrated by the accepted controller; an owned synthetic namespace still must not strand old references.

```powershell
.venv/Scripts/python.exe scripts/multistore_prerequisites.py start-app --env infra/native/acceptance-workload.env.example --env .runtime/multistore/services.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --state .runtime/acceptance-services
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --env infra/native/acceptance-workload.env.example -- scripts/multistore_persistence.py seed --manifest .runtime/native-probe-UNIQUE.json --result .runtime/native-before.json
.venv/Scripts/python.exe scripts/object_store_native.py stop
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --env infra/native/acceptance-workload.env.example -- scripts/multistore_persistence.py outage --unavailable s3 --manifest .runtime/native-probe-UNIQUE.json --result .runtime/native-s3-outage.json
.venv/Scripts/python.exe scripts/object_store_native.py start
.venv/Scripts/python.exe scripts/multistore_native.py stop
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --env infra/native/acceptance-workload.env.example -- scripts/multistore_persistence.py outage --unavailable core --manifest .runtime/native-probe-UNIQUE.json --result .runtime/native-core-outage.json
.venv/Scripts/python.exe scripts/multistore_native.py start
.venv/Scripts/python.exe scripts/multistore_prerequisites.py stop-app --env .runtime/multistore/services.env --state .runtime/acceptance-services
.venv/Scripts/python.exe scripts/multistore_prerequisites.py restart-fuseki --env .runtime/multistore/services.env --state .runtime/acceptance-services --fuseki-state .runtime/multistore-fuseki --tool-root ../p0-p1/.tools --java-home '<Java 21 home>'
```

Optionally exercise owned IdP persistence with `stop-idp` then the original `start-idp` command against the **same** marked runtime, retaining its private idp.env/data. Start API/worker again with the exact original split+S3 command, then run `multistore_persistence.py check` through the same dotenv wrapper and manifest, writing a new result path. The manifest contains a private fixture session and never belongs in Git. The safe result compares semantic revision/history, current GIS, S3 hash, exact catalog/telemetry IDs, managed RDF generation/digest, provider JWKS fingerprint/service-account restrictions and four schema heads/postmaster start times. New writer PID/start records prove actual daemon startup, not a short TestClient factory lifecycle. All role/session/data setup is explicitly synthetic; public reads/writes and normal worker progress are real.

## Recovery commands

For a pre-fix semantic object whose ledger ends with a contiguous legacy attachment tail, POST `/api/v1/objects/{id}/evidence/reconcile` with its **current ledger** `If-Match`, CSRF and a new idempotency key. It requires current edit and every retained artifact/dependency; absent bytes, mismatched references, an unknown/intervening event or a changed head refuse. The appended `object.evidence_reconciled` records the exact baseline, legacy event IDs and evidence versions. It preserves all old revisions and leaves current GIS blocked until the new event's real receipt. It is an explicit repair, not an automatic immutable-history rewrite.

For an exhausted catalog event, POST `/api/v1/catalog/models/{id}/retry` or `/documents/{id}/retry` with `If-Match` matching body `expected_revision`, and body `accepted_revision` containing the exact original resource/revision/event. Current `catalog.manage` and all retained dependencies/bytes are required. Only that failed event resets. Pending/applied/unrelated events refuse. The response returns an independent scoped audit RevisionRef whose current history visibility also depends on the original catalog resource. Catalog metadata/version heads do not advance for retry. Once domain progress is acknowledged, use the existing failed semantic-job retry if a dependent release job has already failed. Retrying a failed job alone does not repair catalog delivery. GIS and telemetry keep their accepted governed recovery interfaces.

## Least-privilege time-series partition maintenance

The app role cannot perform DDL. Compose initializes `lzo_timeseries_maintainer` as a restricted NOLOGIN role; a DBA grants a separate operator login membership. The retained native fixture also has the restricted direct maintainer LOGIN provisioned for earlier telemetry tests; this is recorded in `task6c-maintenance.json`, not silently changed. A member login does **not** automatically change `current_user`; the supported maintenance function deliberately checks the exact current role. Keep the operator password in its private dotenv/secret service. For example, a DBA grants a dedicated `LOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE` operator CONNECT and membership in `lzo_timeseries_maintainer`. Build its SQLAlchemy URL with `make_url(private_url).update_query_dict({'options': '-c role=lzo_timeseries_maintainer'})`; save the result privately as `LZO_TIMESERIES_MAINTENANCE_DATABASE_URL`. This explicitly assumes the restricted role at connection startup.

```powershell
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/maintenance-member.env -- scripts/telemetry_partitions.py --start 2026-04-01 --end 2026-05-01
```

Expected output is `telemetry_samples_202604`. Verify `session_user` is the operator and `current_user` is `lzo_timeseries_maintainer`; table CREATE, sample SELECT and out-of-calendar maintenance must remain denied. Do not publish the previously nonworking recipe that merely logs in as a member and assumes `current_user` changes. This role can call only the fixed bounded monthly partition function; owner credentials remain a separate explicit maintenance interface. Retention preview is not automatic destruction or a settled business retention policy.

## Benchmark

Run after functional tests with no simultaneous document render/test load. Never stop unrelated host workloads to create an idle score. The configurable runner emits safe JSON throughout setup, including partial setup failures:

```powershell
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --env infra/native/acceptance-workload.env.example -- scripts/benchmark_multistore.py --entities 10000 --relations 100000 --users 10 --seed 20261023 --seed-seconds 900 --warmup 0 --repetitions 1 --observations 100 --media-bytes 4096 --git-executable '<git executable>' --hardware-label 'Intel Core i5-14600KF' --output .runtime/benchmark.json --basetemp .runtime/benchmark-UNIQUE
```

Seed installation is explicitly synthetic, committed SQL plus complete RDF companion fixture representations and real GIS handler/inbox/acknowledgement. Its setup time is excluded from API latency. Counts distinguish requested/persisted semantic entities, accepted relations, actual RDF counts, readable projection heads and real GIS receipts. A projection budget/failure cannot be reported as a fully ready RDF baseline. Isolated core schemas are removed after count/measurement; unrelated retained domain/RDF negative evidence is not deleted. Scope uses unique UUIDs, not global physical row totals.

Ten users are distinct persisted fixture identities and encrypted backend sessions with normal current authorization. Transport is in-process ASGI through the real API and native stores; this is not actual-network API or live OIDC login throughput. Object, map, graph, SPARQL, exact history, domain aggregation and bounded accept/sync operations have explicit definitions. Results include revision/dirty-content hash, IDs, seed/sizes, warmup/repetitions, P50/P95, complete versus HTTP200 incomplete/partial versus errors, attempt/complete throughput, and CPU/RSS scope. Pass explicit owned `--service-pid` values to include service processes; host CPU/memory includes unrelated activity. The actual host is i5-14600KF, 14 physical/20 logical CPUs, about 31.85 GiB RAM; it is not the specified 8-core/32GB/SSD acceptance machine.

## Compatibility, remaining deployment and field limits

Regenerate/check OpenAPI with `scripts/export_openapi.py`, run `frontend/scripts/api.mjs --check`, **actual** `tsc --noEmit` and a production Vite build. Generated equality alone does not establish TypeScript compatibility. Frontend visual redesign is unchanged.

See [offline deployment](../operations/offline-deployment.md), [schema/data flow](schema-guide.md), [real-data questions](real-data-questions.json) and [source/version references](sources.md). S3 Range verifies whole objects; upload part/orphan GC, physical capacity policy, high-volume catalog indexing and unbounded core-history scans remain limits. Telemetry budgets may produce actionable incomplete responses. No real ONVIF/DJI/device protocol, actual surveyed boundary/height, professional source-verification promotion, production HA, RPO/RTO or automatic deletion policy is certified.

Current native evidence is [task6c-native.json](task6c-native.json). The initial measured [20/40/10-user smoke](task6c-benchmark-smoke.json) completed 70 requests: 50 complete and 20 errors. Every graph and SPARQL request returned 422 SPARQL_TIMEOUT. Graph uses the 15,000ms server budget; explicit SPARQL asks for 10,000ms, capped by that server budget. Pytest completing the runner does not qualify this workload. There was no extra warmup pass and only ten samples per operation; caches were not cleared, so this is not a cold-cache claim. The [first malformed-seed run](task6c-benchmark-smoke-red.json) is retained separately. The requested-size result below retains both failed setup runs for reproducibility.


The [requested-size result](task6c-benchmark.json), measured from clean commit `717b07f4600ed9f490dc697033ff63a9aac37004`, verifies **10,000 semantic entities / 100,000 accepted relations**, matching actual RDF counts and 10,000 projection heads/GIS acknowledgements/versions. Seed setup took 237.87s within the 900s budget. Real auxiliary inputs persisted 100 observations plus one synthetic non-playable 4,096-byte media record. These are fixture setup/store counts, not API ingestion throughput or a claim that every entity was individually queried through HTTP. The [cross-chunk seed failure](task6c-benchmark-size-red.json) is preserved separately.

Ten distinct authorized users attempted 70 operations in 175.39s: **50 complete, zero incomplete HTTP200, 20 HTTP422 SPARQL_TIMEOUT errors**. Graph and SPARQL each failed all ten requests. Pytest's `1 passed / 494.87s` establishes runner completion only. This workload is not performance qualified. No timeout budget was raised to obtain a passing number. The shared error code does not identify whether a cumulative acquisition checkpoint, parsing child or remote acquisition consumed its budget; no narrower bottleneck is claimed.

| Operation | Complete / errors | P50 seconds | P95 seconds |
| --- | ---: | ---: | ---: |
| Object | 10 / 0 | 6.961 | 7.139 |
| Map first page | 10 / 0 | 66.579 | 73.319 |
| Graph | 0 / 10 | 16.406 | 16.975 |
| SPARQL | 0 / 10 | 11.826 | 13.472 |
| Exact history | 10 / 0 | 0.502 | 1.075 |
| Domain history | 10 / 0 | 2.689 | 4.363 |
| Bounded accept/sync | 10 / 0 | 67.149 | 67.913 |

Attempt throughput was 0.3991/s; complete throughput was 0.2851/s across this mixed workload. Graph/SPARQL latency entries are failed requests, and the JSON separately reports complete/incomplete/error distributions. P95 with ten samples is the maximum observed sample, not an SLO qualification. Earlier domain/RDF evidence and host caches were retained. These are bounded page reads and ten sampled object IDs, not exhaustive API-read verification of all stored rows.

Resource sampling covered **single PIDs**, not process trees: Python client, PostgreSQL postmaster parent, S3 server, Fuseki JVM and IdP JVM. PostgreSQL connection backends and Python query children are omitted from selected-PID CPU/RSS. Host CPU/used memory includes those and unrelated desktop activity. Across 334 samples host CPU averaged 25.85%, peaked at 89.1%, and used memory peaked at 27.789 GiB. Selected-PID peak RSS was client 609.71 MiB, PG parent 27.61 MiB, S3 188.79 MiB, Fuseki 1496.25 MiB and IdP JVM 500.47 MiB; their sum is not total service memory. Native API/worker were stopped for measurement. The actual 14-physical/20-logical CPU host with about 31.85 GiB RAM differs from the specified 8-core/32GB/SSD machine.

Seed counts exclude the one template and the ten later measured create/sync operations. Store counts are taken before measurement/owned core-schema teardown; retained RDF/domain evidence is not removed to obtain a cleaner score.
