# Foundation environment and startup

Current source paths: `backend/`, `frontend/`, `database/migrations/`,
`database/fuseki/`, and `database/postgres/`. Historical plans and verification
records retain their original paths; see the [snapshot mapping and acceptance
limits](../status/2026-10-21-source-snapshot.md). Product development is paused.

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

The API receives `LZO_DATABASE_URL` for the application role, `LZO_MIGRATION_DATABASE_URL` for the schema owner, `LZO_FUSEKI_URL`, `LZO_OIDC_ISSUER`, `LZO_OIDC_DISCOVERY_URL`, `LZO_OIDC_CLIENT_ID`, `LZO_OIDC_CLIENT_SECRET`, and `LZO_FILE_ROOT`. `LZO_CA_BUNDLE` optionally points to the development certificate. Keycloak uses a distinct PostgreSQL database and role. The proxy serves `https://localhost:8443` by default; PostgreSQL, Fuseki, and Keycloak have no published host ports.

The `web-test` profile builds the frontend Dockerfile's `test` target and runs frontend unit tests, TypeScript checks, generated API consistency and the production build. Root Playwright projects remain the service/browser harness; `npm run test:workbench` selects the existing workbench tests. These commands require their documented dependencies and owned fixtures; the source snapshot does not establish Compose or browser acceptance.

`GET /health/live` reports the API process. `GET /health/ready` checks PostgreSQL, Fuseki, OIDC discovery and the file root; a failed dependency returns 503. The worker consumes registered governed jobs, including semantic projection and ingestion. Source database profiles are optional and isolated from normal readiness. The Compose `migrate` profile uses owner credentials through `python -m lzo.migrate`; its configuration and versions live in `database/`, not in the backend package.

## Supplemental native Windows check

When Docker is unavailable, install the lockfile in `.venv` and run `python scripts/native_dev.py prepare`, then `python scripts/native_dev.py start --project lzo-test-bootstrap` and `python scripts/native_dev.py test --project lzo-test-bootstrap`. This runner uses verified archives under ignored `.tools`, project data under ignored `.runtime/projects/lzo-test-bootstrap`, and loopback ports 15432, 13030, 18080, 18000, and 18443. Its `restart` retains database and TDB2 files; `stop` retains them as well. The native check can prove the application against real processes, but it does not prove Compose images, networking, mounts, or `docker compose config`.

An air-gapped deployment will require preloaded pinned images and Python/Node artifacts in an internal registry or package mirror. The F01 development build still fetches its pinned Fuseki source archive and Python dependencies when those artifacts are absent locally. Full offline deployment acceptance belongs to later platform delivery.
