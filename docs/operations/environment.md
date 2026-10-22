# Foundation environment and startup

Current source paths: `backend/`, `frontend/`, `database/migrations/`,
`database/fuseki/`, and `database/postgres/`. Historical plans and verification
records retain their original paths; see the [snapshot mapping and acceptance
limits](../status/2026-10-21-source-snapshot.md). Frontend visual redesign is paused;
the approved multi-store backend work has separate [current evidence](../database/acceptance.md).

Run `python scripts/check_environment.py` first. It reports Git, Node, Compose, daemon and container availability, WSL on Windows, CPU, RAM, disk space, and physical disk media type without changing the host. F01 targets Python 3.12.7 and Node 24.22.0. `infra/versions.lock.json` holds the exact service and archive versions and digests. Keep generated `.env`, certificates, and `.runtime` state out of Git.

The container probe uses the pinned PostgreSQL image only when it is already cached. If the daemon is working but that image is absent, the audit reports `pinned_image_absent` and still marks the host ready to attempt Compose acceptance, which may pull the pinned image.

## Compose development and acceptance

Create a project-local Python 3.12 environment, then install the pinned requirements:

```sh
python -m venv .venv
.venv/bin/python -m pip install -r backend/requirements.lock
.venv/bin/python scripts/dev.py prepare
.venv/bin/python scripts/dev.py up
.venv/bin/python scripts/dev.py test
```

On Windows use `.venv\Scripts\python.exe` and `scripts\dev.ps1`. `prepare` creates random values in ignored `infra/compose/.env` and a short-lived self-signed certificate in ignored `infra/compose/certs`. Trust this certificate only within a test client or browser profile; the scripts do not change the machine trust store. `test` sets `COMPOSE_PROJECT_NAME=lzo-test-bootstrap` by default, starts its own services, then runs the API tests. The persistence test restarts only containers labeled for that test project and requires its own named volumes. `down` leaves volumes intact.

In split mode API/worker receive `LZO_DATABASE_URL`, `LZO_GIS_DATABASE_URL`, `LZO_TIMESERIES_DATABASE_URL` and `LZO_CATALOG_DATABASE_URL` for independent application roles, plus Fuseki/OIDC/private S3 configuration. Four `*_MIGRATION_DATABASE_URL` owner URLs belong only to migration/maintenance controllers. `LZO_CA_BUNDLE` optionally points to the development certificate. Keycloak uses a distinct PostgreSQL database and role in Compose. The proxy serves `https://localhost:8443` by default; internal databases are not browser endpoints.

The `web-test` profile builds the frontend Dockerfile's `test` target and runs frontend unit tests, TypeScript checks, generated API consistency and the production build. Root Playwright projects remain the service/browser harness; `npm run test:workbench` selects the existing workbench tests. These commands require their documented dependencies and owned fixtures; the source snapshot does not establish Compose or browser acceptance.

`GET /health/live` reports the API process. `GET /health/ready` checks PostgreSQL, Fuseki, OIDC discovery and the file root; a failed dependency returns 503. The worker consumes registered governed jobs, including semantic projection and ingestion. Source database profiles are optional and isolated from normal readiness. The Compose `migrate` profile uses owner credentials through `python -m lzo.migrate`; its configuration and versions live in `database/`, not in the backend package.

## Supplemental native Windows check

For the four-store Windows acceptance use [the explicit runner and ownership/port instructions](../database/acceptance.md). `multistore_native.py` owns PostgreSQL 25432; `object_store_native.py` owns S3 28333; the supplemental Fuseki is 23030. `multistore_prerequisites.py` copies Keycloak into a newly owned writable runtime and starts loopback 28080, management 29000 and API 28000. It never writes into a borrowed tool cache. Private dotenv files are explicit, and source PG/MySQL fixtures use fresh owned clusters and random ports. The older `native_dev.py` has a separate hardcoded port set; do not invoke its restart path against this worktree or a borrowed deployment.

An air-gapped deployment requires preloaded pinned images, Python/npm artifacts, certificates and local map/font/static assets. Development builds may fetch missing artifacts. Python socket egress-denial checks, actual native restart and Compose config validation have separate evidence boundaries; none certifies physically disconnected container deployment. Follow [the offline runbook](offline-deployment.md) on the target Docker host.

Native integrated acceptance uses the checked-in `infra/native/acceptance-workload.env.example` with `LZO_JOB_BATCH_TIMEOUT_SECONDS=240` and `LZO_JOB_LEASE_SECONDS=300`, loaded identically by API/worker and benchmark. Defaults remain30/60seconds and are not sufficient for the measured shared-scene RDF batch (~162seconds). This explicit profile is not a performance qualification; see [acceptance](../database/acceptance.md).
