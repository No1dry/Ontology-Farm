# Ontology Hub and Specialized Stores Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the approved six-domain storage architecture with real persistence, compatible backend paths, controlled migration, and a Word delivery report published with code to the existing repository.

**Architecture:** Keep Jena Fuseki/TDB2 as the semantic hub. Governance, GIS, time series and catalog are independently connected PostgreSQL databases; S3 holds immutable files. Accepted revisions and durable outbox/inbox deliveries coordinate domain writes without claiming a distributed atomic commit.

**Tech Stack:** Python/FastAPI/SQLAlchemy/Alembic, PostgreSQL 17, PostGIS 3.6, Jena Fuseki/TDB2, SeaweedFS S3, existing React/API contract tooling. The coding model remains GPT-6.1-sol as requested.

**Spec:** `docs/superpowers/specs/2026-10-22-multi-store-architecture-design.md` (user approved option A on 2026-10-22).

## Global Constraints

- “一个语义主库＋五类专业存储”，单机先逻辑分库并允许以后分服务器。
- 延续 UUID＋稳定 IRI，以后端规则／事件关联各库；大模型保持可选候选辅助。
- 时序先采用独立 PostgreSQL 时间分区；对象存储采用 S3 接口并建议 SeaweedFS。
- 先完成静态文件、通用增量与模拟回放；真实设备协议、真实三维测绘由资料清单约束，不能用合成数据代验。
- 不引入跨 PostgreSQL、Fuseki、S3 的分布式原子事务假设。
- 源记录删除只撤回相应支持、标记失效；不能级联删除实体、文件、历史或其他来源事实。
- 前端只访问后端。当前授权必须覆盖地图、观测、媒体、资料、历史、图谱及导出。
- `database/` stores migrations/configuration, `backend/` runtime logic, `frontend/` client. Frontend visual redesign stays paused.
- No production database mutation, old worktree/service disruption, raw secrets, or actual private field data in public GitHub. Runtime state stays in this worktree's ignored `.runtime/`.
- Keep accepted migration history; append migrations. Use actual domain servers for integration acceptance. Missing prerequisites are explicit failures/unexecuted results, never passing mocks or silent provider fallback.
- Actual storage and backend delivery precede the final Word. The report distinguishes native tests, Compose execution, synthetic fixtures, and real field verification.

## Review Focus

- Target commits but worker loses its acknowledgement: Task 1 must prove replay returns one receipt without duplicate domain rows.
- A shared device is revoked while a specialized index is stale: Tasks 3–5 must deny current reads and preserve historic identity without trusting copied ACL fields.
- Reusing a media upload or source record identity with different bytes/time: Tasks 2 and 5 must reject conflicts and retain evidence, including across partitions.
- An old GIS writer, source-withdrawal path, or direct ArtifactStore call bypasses the new store: Tasks 2 and 3 inventory and exercise every affected write/read entry point.
- Migration rollback after new writes: Task 6 must replay accepted changes before reverting reads and prove cross-store hashes and revisions survive recovery.

## Execution and file map

Worktree: `C:/Users/robot/Desktop/Ontology-Farm/.worktrees/multi-store`; branch `feat/multi-store-20261022`; source Git repository is the independent `.worktrees/github-publish` clone, not the outer documentation repository. Base `6bd1b02` contains the approved design. User approval authorizes implementation and the final existing-GitHub update; do not re-open an execution-method choice.

One implementation task runs at a time. Each task gets a fresh GPT-6.1-sol implementer and a task-scoped review; aggregate changes get one final review. Root coordinates Word authoring/rendering using the documents skill after Task 6 supplies actual evidence.

Shared executable inputs (read-only): Git `C:/Users/robot/AppData/Local/Programs/Git/cmd/git.exe`; Python `C:/Users/robot/Desktop/Ontology-Farm/.worktrees/p0-p1/.venv/Scripts/python.exe`; native tool cache `.worktrees/p0-p1/.tools/`. Do not install packages into that shared venv; if a new dependency is needed, create a worktree-local venv. Tests must use an existing worktree-owned `.runtime/` parent and unique `--basetemp`, because the system pytest temp directory is inaccessible.

New files organize around `storage/`, `synchronization/`, `gis/`, `catalog/`, and `telemetry/`. Existing semantic/ingestion/artifact modules consume their interfaces. Prefer small modules, no second independent authentication or rule engine. Preserve existing public API shapes where possible; explicit additive status fields and generated contracts are allowed.

### Task 1: Independently provisioned stores and durable delivery substrate

**Files:**
- Create: `backend/src/lzo/storage/{__init__,registry,migrate}.py`, `backend/src/lzo/synchronization/{__init__,contracts,models,delivery}.py`.
- Create: `database/{gis,timeseries,catalog}/` migration entry points, initial domain inbox/version tables and role grants; append `database/migrations/versions/0401_store_deliveries.py`.
- Modify: `backend/src/lzo/config.py`, `backend/src/lzo/db.py`, `backend/src/lzo/main.py`, `database/postgres/init-databases.sh`, `infra/compose/compose.yaml`, `infra/compose/.env.example`.
- Create: `scripts/multistore_native.py`, `backend/tests/unit/test_store_configuration.py`, `backend/tests/integration/test_store_delivery.py`.

**Interfaces:**
- `StorageRegistry.from_settings(settings) -> StorageRegistry`; `.engine(domain: Literal['core','gis','timeseries','catalog']) -> Engine`; `.close() -> None`.
- Explicit config: `LZO_GIS_DATABASE_URL`, `LZO_TIMESERIES_DATABASE_URL`, `LZO_CATALOG_DATABASE_URL`, corresponding owner migration URLs; `LZO_STORAGE_MODE=legacy|split`. Existing legacy deployments remain explicit. New deployment uses `split`; missing split DSNs fail, no implicit fallback.
- `SyncEnvelope`: immutable event ID, entity ID, aggregate revision, target, operation, payload/hash, schema version, source/evidence refs, data nature, recorded timestamp. Payload canonicalization uses existing digest facilities.
- `enqueue_delivery(uow, envelope: SyncEnvelope) -> UUID`; `deliver_once(registry, target: str, handler, limit: int=100) -> DeliveryBatchResult`; handler signature `(connection, envelope) -> DomainReceipt`.
- `delivery_status(uow, event_id: UUID) -> list[DomainReceipt]` and `require_deliveries(uow, event_id: UUID, targets: tuple[str,...]) -> None` make pending/failed explicit. Per-target inbox and applied data share one transaction; core acknowledgement is separate and recoverable.

- [ ] **Step 1:** Add failing tests for explicit provider configuration and delivery recovery. Assert missing split URL raises config failure; domain application is called once after simulated acknowledgement loss; identical replay returns same digest/revision; different payload under same event conflicts; lower aggregate revision cannot replace a higher head; concurrent claims do not lose delivery; retries remain observable.
- [ ] **Step 2:** Run focused tests and record RED. Initialize an isolated native PostgreSQL cluster using the existing binaries at unused configurable loopback ports, generated credentials, and worktree-owned runtime. Verify cluster ownership before restart/stop. Do not connect to existing live project databases.
- [ ] **Step 3:** Implement registry, independent migrations and restricted app/owner roles for `lzo`, `lzo_gis`, `lzo_timeseries`, `lzo_catalog`; GIS enables PostGIS. Add target inbox, delivery ledger, bounded retry/claim semantics and worker integration hook. Database migration must create real tables, not just emit SQL strings.
- [ ] **Step 4:** Update Compose initialization/config and native runner. Native runner accepts a read-only tool cache path and configurable ports, does not change host firewall/virtualization. Core readiness reports required-store reachability/migration and separates pending synchronization from liveness. Ensure each app account is denied other domain data.
- [ ] **Step 5:** Run `pytest backend/tests/unit/test_store_configuration.py backend/tests/integration/test_store_delivery.py -q --tb=short --basetemp=.runtime/task1-pytest` with owned service env, then scoped existing pure tests. Prove actual domain commits, independent connections, grants and persistence across an owned PostgreSQL restart. Report native and Compose checks separately.
- [ ] **Step 6:** Commit implementation, config and tests; write the task report with commands, sanitized evidence and produced interface signatures.

### Task 2: S3-backed immutable artifacts and resumable media

**Files:**
- Create: `backend/src/lzo/storage/{blobs,s3}.py`, `backend/src/lzo/artifacts/uploads.py`, `database/object-store/` configuration; append upload-session migration.
- Modify: `artifacts/{store,service,router,models}.py`, `maps/service.py`, `ontology/drafts.py`, `semantic/files.py`, `ingestion/{evidence,sync}.py`, config/readiness, Compose and native runner, pinned backend dependencies.
- Test: `backend/tests/unit/test_blob_contract.py`, `backend/tests/integration/test_s3_artifacts.py`.

**Interfaces:**
- `get_blob_store(settings) -> BlobStore`, preserving the existing BlobRef identity/hash/length/media contract and staged upload semantics. Contract includes bounded `.put`, `.stage_async`, `.complete`, `.discard`, `.open` and range-read support; synchronous local staging is permitted, file bytes may not all reside in RAM.
- Explicit `LZO_BLOB_PROVIDER=local|s3`; S3 endpoint, bucket, region, credential references and local staging path in config. S3 service is private, durable and pinned to a verified released version/checksum.
- Authenticated upload session create/part/status/complete/cancel under existing artifact routes. Service functions use current actor, resource scope, expected digest/size, idempotency keys and quota reservation; no new authentication system.

- [ ] **Step 1:** Add RED tests for identical bytes in distinct business versions, corrupt read, same part number/different bytes, interrupted resume, wrong final digest, quota exhaustion, duplicate finalization, revoked scope and valid/invalid HTTP Range. Prove cancelled/expired/stale sessions cannot publish artifacts.
- [ ] **Step 2:** Implement the provider interface and local adapter, S3 streaming client, resumable durable sessions, total hash verification, resource-specific configurable size budgets, current permission checks and safe orphan retention. Register metadata only after content verification; repair after crashes without overwriting accepted versions.
- [ ] **Step 3:** Replace all direct runtime construction of ArtifactStore across the listed callers; preserve test injection. Distinguish existing legacy `file_root` and `file_root/artifacts` locations during migration. Keep map/parser consumers working when they require temporary local seekable files.
- [ ] **Step 4:** Provision actual SeaweedFS privately in native tests and Compose with generated credentials and persistence, using official release artifacts and verifying checksums. Pin new Python dependencies locally. Test S3 read/write, auth denial and persistence; mock-only S3 acceptance is insufficient.
- [ ] **Step 5:** Run new artifact tests plus existing map/ingestion file and export tests on both explicit providers, with owned temp directories. Assert no permanent signed URLs or secrets in API/logs. Commit and report exact provider version and limitations.

### Task 3: GIS split with compatible geometry, source withdrawal and semantic barriers

**Files:**
- Create: `backend/src/lzo/gis/{__init__,repository,service,schemas}.py`, GIS geometry/location/asset/survey migrations.
- Modify: `semantic/{geometry,source_rows,merge,facts,projection}.py` as needed for existing interfaces, maps integration and worker target registration. Locate actual projection module before editing; keep existing naming if different.
- Test: `backend/tests/integration/test_split_gis.py`, extend existing geometry/source/merge tests for split mode.

**Interfaces:**
- `GisRepository(registry)` supports immutable version writes via Task 1 handler, normalized geometry validation, authorized-ID batch reads, and stable bbox/time cursors; current core authorization is passed from service, never trusted from copied GIS scopes.
- Existing geometry API and object IDs remain compatible. Core revision carries accepted normalized payload/hash; GIS versions index it independently. `require_deliveries` gates publication of facts depending on GIS.
- Spatial asset registration accepts artifact version, footprint, horizontal/vertical datum, capture time, accuracy, source and quality (`unverified|display_only|verified`); survey evidence required for verified quality. This provides data registration, not a new 3D frontend.

- [ ] **Step 1:** Add RED coverage for geometry write/read, CRSs 4326/3857/4490, invalid/unknown CRS, shared-device revocation, bbox pagination after permission filtering, stale/wrong-order deliveries, and paused GIS preventing dependent RDF publication while unrelated reads continue.
- [ ] **Step 2:** Implement independent GIS schema with version history and index heads; use existing normalization safeguards. Distinguish observed-time positions from current head and unknown height from zero. Add spatial asset package metadata and file dependency/hash validation, without claiming measured accuracy from synthetic fixtures.
- [ ] **Step 3:** Route every existing `semantic_geometry` access through a legacy/split boundary, including `source_rows` withdrawal/restoration, entity merge, conflict review and inference/export inputs. Preserve governance revisions, source supports, audit and expected revisions. After split cutover, do not dual-write an independent authoritative legacy geometry table.
- [ ] **Step 4:** Integrate durable delivery, target receipts and RDF visibility barriers. Exercise target-unavailable, crash-after-commit, withdrawal and merge retries; a stale target cannot be represented as up-to-date.
- [ ] **Step 5:** Run new actual PostGIS tests and affected existing semantic integration tests using isolated core/GIS/Fuseki. Verify identical IDs and semantic retraction plus source evidence history. Commit and report read/write path inventory and remaining compatibility boundaries.

### Task 4: Versioned equipment and professional reference catalog

**Files:**
- Create: `backend/src/lzo/catalog/{__init__,schemas,repository,service,router}.py`, catalog migrations, domain delivery handler.
- Modify: app router registration, existing authorization resource registration, ontology references integration and generated contracts only where additive.
- Test: `backend/tests/integration/test_catalog.py`.

**Interfaces:**
- `/api/catalog/models` and `/api/catalog/documents` create/list/detail/version endpoints; request signatures use existing ActorContext/UoW, idempotency and expected revision contracts.
- Models contain manufacturer, exact model, firmware/applicability, capabilities, protocol profiles, offline constraints, verification evidence and state; physical `entity_id` references `model_id` without confusing identities.
- Document versions refer to artifact versions and optional entity/term IRIs; search accepts text/type/model/date filters and authorized IDs. Reference ontology records preserve version/hash/license and feed existing draft/release review, never auto-publish schema.

- [ ] **Step 1:** Add RED tests for one model linked to two distinct devices; draft vs verified capabilities; independent evidence versions; source duplicate/conflict; stale expected revision; reviewer independent of submitter for critical metadata/semantic publication; current regional permission and global reference visibility policy.
- [ ] **Step 2:** Implement governed core metadata acceptance and idempotent catalog projection; specialized rows do not own mutable permissions. Read lists with stable cursors and explicit projection status. Allow structured/manual summaries and attachments; no mandatory vector/LLM/OCR service.
- [ ] **Step 3:** Seed clearly synthetic camera, pump and drone model records plus reference-document examples with actual source provenance. Preserve unknown manufacturer/protocol fields; a placeholder must not masquerade as installed equipment or a licensed copied manual.
- [ ] **Step 4:** Run real catalog/core/blob integration, repeat imports and event delivery, revocation, historic versions and outage recovery. Update generated API contracts without changing frontend visuals. Commit and report endpoints and examples.

### Task 5: Time-series observations, media timeline and cross-store object views

**Files:**
- Create: `backend/src/lzo/telemetry/{__init__,schemas,repository,service,router,replay}.py`, timeseries migrations/partition maintenance.
- Create: `backend/src/lzo/storage/object_view.py`, synthetic data replay script and input examples.
- Modify: app registration, ingestion adapter registrations, worker handlers and contracts as needed.
- Test: `backend/tests/integration/test_timeseries.py`, `backend/tests/integration/test_object_data_view.py`.

**Interfaces:**
- Authenticated `/api/telemetry/batches`, `/api/telemetry/observations`, `/api/telemetry/events`, `/api/telemetry/media`, `/api/telemetry/tracks` and batch status; existing source mapping and resource permissions are reused.
- `ObservationInput` preserves source namespace/record ID, device/point/metric IDs, raw and normalized value/unit, sampled/reported/received clocks and quality, evidence, device/location/model version and data nature. Correction has explicit superseded record/version; same identity/different content is a conflict, not implicit correction.
- `/api/objects/{id}/data` aggregates semantic identity, authorized GIS, catalog/model, latest permitted observations and media references with independent watermarks/status. No direct browser SQL, S3 credentials or unrestricted SPARQL federation.

- [ ] **Step 1:** Add RED tests for late/out-of-order/duplicate data, identical source ID altered timestamp across partitions, decimal/unknown/zero distinctions, interval queries/latest-valid filtering, historical device binding, correction/withdrawal, batch replay after target commit and before ack, and revocation while indexes retain old scope.
- [ ] **Step 2:** Create real time partitions and maintained indexes with dedicated maintenance role. Global dedupe and target rows commit together. Add bounded writes, time+ID cursors, basic deterministic aggregates and retain-history policy preview; no automatic deletion of referenced evidence.
- [ ] **Step 3:** Implement unified static CSV/JSON/GeoJSON and incremental envelope adapters, batch manifest acceptance, idempotent target delivery and query endpoints. Preserve existing multi-source identity/conflict rules. High-frequency raw data does not automatically create one full RDF revision per sample; publish selected event/summary/reference semantics using existing ontology governance.
- [ ] **Step 4:** Implement camera/channel segment timeline and drone mission/trajectory/media associations against Task 2 asset versions, Task 3 geometry versions and Task 4 models. Expose adapter capability contracts and replay, without inventing live ONVIF/DJI connectivity or enabling control.
- [ ] **Step 5:** Run end-to-end synthetic field/pond/orchard scenarios with camera, pump and drone; static ingest then incremental changes, conflicting evidence, retraction and cross-store view. Verify actual persistence, current authorization, evidence traces and partial-unavailable responses. Commit and report input fixtures and benchmark parameters.

### Task 6: Migration, restore, reproducible acceptance and delivery evidence

**Files:**
- Create: `backend/src/lzo/storage/{migration,backup}.py`, `scripts/multistore_migrate.py`, `scripts/multistore_backup.py`, `scripts/benchmark_multistore.py`.
- Create: `backend/tests/integration/test_multistore_migration.py`, `backend/tests/integration/test_multistore_restore.py`, `docs/database/` operator guide, actual schema/data-flow reference, questions register and machine-readable sanitized acceptance report.
- Modify: database/backend/infra READMEs, Compose/offline deployment docs and root README, generated OpenAPI/client as required.

**Interfaces:**
- Migration CLI supports `preview`, `backfill`, `reconcile`, `cutover`, `rollback`; target identities, checkpoints, watermark and digest manifests are explicit. Production cutover requires an authorized governance approval record; isolated tests supply explicit test authorization rather than bypass production checks.
- Backup CLI records consistent core watermark and exact domain/blob/RDF versions, pauses or drains bounded writes where necessary, and verifies reference completeness before success. Restore targets only a new explicitly named isolated namespace/cluster; never drops an existing source. Sensitive runtime backups remain ignored.
- Benchmark emits JSON with hardware, versions, source nature, operation counts, P50/P95, throughput, errors, CPU/memory, input sizes and reproducible command. Full baseline and environment limitations are separately named.

- [ ] **Step 1:** Add RED tests for interrupted legacy geometry/artifact migration, duplicate backfill, inconsistent hashes preventing cutover, new events during backfill, rollback after post-cutover writes, and wrong-target refusal. Preserve legacy identity/history and test both legacy artifact roots.
- [ ] **Step 2:** Implement checked migration/replay and coordinated backup/restore, including S3 file hashes and RDF versions; no destructive downgrade or silent lost writes. Demonstrate restore to separate owned databases and object storage location, not merely a dump-file creation.
- [ ] **Step 3:** Run relevant backend suites, API contract check and frontend compatibility build. Run offline core scenario, storage restart persistence, target outages and restore. Record actual 10-user/10k-entity/100k-relation baseline where feasible, along with configurable observation/media workload and resource usage; unavailable standard hardware/Compose/field devices remain explicit, never fabricated.
- [ ] **Step 4:** Produce actual schema inventory and sample relationships, migration/restore runbook, environment setup, version pins, source licenses and final issue statuses. Update Q01 as approved; Q02–Q18 remain evidence-driven. Any requirements not verified are listed precisely with commands/inputs needed next.
- [ ] **Step 5:** Commit code and sanitized text evidence. Root then creates the Chinese Word report using the documents skill, renders every page and fixes layout. Include actual built structure, tests, real-data needs and remaining limitations; do not claim this whole original P0+P1 product is complete.
- [ ] **Step 6:** Perform final whole-branch review, fix material findings, verify no secrets/runtime assets, then fast-forward/merge and push to existing `No1dry/Ontology-Farm` under already granted authorization. Reconcile remote SHA and provide source and Word links.

## Self-review record

Spec sections 1–5 map to Tasks 1–3; specialist domains 6.1/6.3/6.4/6.2 map to Tasks 3/2/4/5; static/incremental inputs to Task 5; external references/questions, migration, acceptance and Word/GitHub delivery to Task 6. The reviewed interface names are consumed consistently. File overlap is sequential; no concurrent implementers. Current hardware/Compose limitations affect evidence claims, not a reason to stop coding the approved design. No extra approval is needed for the already authorized worktree, implementation method or final repository update.
