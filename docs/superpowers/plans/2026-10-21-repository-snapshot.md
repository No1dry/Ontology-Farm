# Current code repository snapshot

## Goal and authority
The user requests saving current code into existing No1dry/Ontology-Farm with database, backend and frontend clearly separated. Original product development remains paused. This is source curation, integration and directory relocation, not new product functionality or deployment acceptance.

## Global Constraints
- Work only in `.worktrees/repository-snapshot`; do not modify other development lanes, running services, database contents, credentials or remote repositories.
- Preserve current source behavior and all three source lanes. Do not implement new features or redesign the frontend.
- Produce actual top-level `backend/`, `frontend/`, `database/` directories, with truthful Chinese documentation.
- Never commit runtime data, logs, secrets, local environment files, dependencies or private keys. Preserve fixture licenses and binary attributes.
- Do not claim final P0+P1 completion, deployment readiness or successful integration tests that were not run.

## Task 1: Consolidate and separate the current source snapshot

Read this entire task before editing. You are the sole code implementer. Do not spawn subagents. Work at C:/Users/robot/Desktop/Ontology-Farm/.worktrees/repository-snapshot (already isolated branch codex/repository-snapshot at 418a52ef782e945ded9ed00004862f71890cfe52). User explicitly requests GPT-6.1-sol for coding.

### Source lanes and integration
Main backend: 418a52ef782e945ded9ed00004862f71890cfe52 (base); Studio: 690c42d21ef733a1988ea346cf4ba4f361b97f08; maps: 82b152c30fa216e3298d1333c025e94b8a521c3c. All three were clean at dispatch. Main includes I04 final fixes. Studio includes U01 and U02 producers. Maps includes U01 plus map backend and fixtures. Common accepted ancestor 70c8e09d1f0d4a0ceadd664ab3079892a7c4299c. Integrate current branch content without duplicating U01 equivalent a0a1d73 / 03bdfbb. Use merges or three-way patches as appropriate, recording resolution choices. Known overlaps: ingestion/schemas.py, worker/handlers.py, contracts/openapi.json. Inspect and retain both behaviors; regenerate combined OpenAPI and frontend generated types from combined backend. The unshipped maps migration 0301_map_resources must follow main's 0204_ingestion_runs_rows rather than fork from 0203; do not modify previously accepted migrations. Verify one migration head.

### Target layout
- `backend/`: current apps/api Python API, worker, models, tests, dependency files, Dockerfile. Core semantic logic remains in backend.
- `frontend/`: current apps/web React Studio, tests, assets and lockfile. This is the current prototype, pending visual redesign per user references; not an approved final frontend.
- `database/migrations/`: actual Alembic env and versions, moved from backend/alembic, plus suitable Alembic config location and documented invocation.
- `database/fuseki/`: actual Fuseki/TDB2 Dockerfile and configuration moved from infra/fuseki.
- `database/postgres/`: actual database initialization script moved from infra/compose/init-databases.sh.
- `infra/`: deployment orchestration, proxy, identity, test images; `contracts/`, `packages/`, `scripts/`, `tests/`, `docs/` stay shared.
Update executable paths, Docker COPY/build paths, Compose mounts, migration module config, Python test path traversal, frontend API-generation relative paths, runtime launch scripts, npm scripts, and active runbooks affected by relocation. Do not broadly rewrite historical specs/plans; put a path-mapping notice in current docs if needed.

### Documentation and privacy
Rewrite stale root README in Chinese: clear directory responsibilities, service/data boundaries (PostgreSQL/PostGIS platform state vs Jena RDF ontology vs external PostgreSQL/MySQL sources), practical existing entry commands, current status and incomplete work. Add component READMEs and docs/status/2026-10-21-source-snapshot.md with source commit pins and integration/relocation explanation. State product paused; 15/29 accepted development milestones before this snapshot, I04/map fixes not yet fully accepted, frontend needs redesign, Compose/offline/backup/restore/performance acceptance incomplete. Snapshot is not release acceptance. Add adequate ignore rules for environment variants, keys, runtime databases/logs, dependencies; preserve intentionally distributable test fixtures and licenses. Do not include prior scratch reports/logs or machine-specific credentials.

### Verification and scope
No new mirror tests needed for directory renames. Run meaningful static/import/migration-head checks, OpenAPI generation consistency, frontend api:check/typecheck/unit tests/build, plus focused existing pure backend tests affected by combined integration as appropriate. Do NOT run native database suites, mutate shared services/schemas, run migrations against a database, install or alter system software, or use Docker/WSL. Do not restart original product tasks. Shared tools are read-only: Python .worktrees/p0-p1/.venv/Scripts/python.exe; Node .worktrees/p0-p1/.tools/node/node-v24.22.0-win-x64/node.exe. You may use local per-snapshot dependency setup or read-only cache reuse, but do not alter source worktree dependencies or commit them. Root controls auth, remote upload, secret scanning and final source-only export; no remote actions from implementer.

### Delivery
Commit local integration/reorganization changes in this isolated branch. Keep a source mapping manifest if useful for verifying file preservation. Write full report to `.superpowers/sdd/2026-10-21-repository-snapshot/task-1-report.md` with commits, exact commands/results, changed paths, conflict resolutions, and limitations. Final response under 15 lines: status, commits, test summary, concerns, report path. Ask root promptly if blocked; do not spend time fixing unrelated pre-existing product defects.
