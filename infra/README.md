# Runtime boundaries

`compose/compose.yaml` defines PostgreSQL/PostGIS, Fuseki, private S3, Keycloak, API, worker and proxy. Four database/role pairs are created by `database/postgres/init-databases.sh`; `migrate` explicitly upgrades core and all three domains. API/worker use application credentials; owners and maintenance logins belong to separate operator commands. Internal storage ports are not browser endpoints.

Exact image/archive versions and digests are in [versions.lock.json](versions.lock.json). Offline delivery must pre-load these images plus Python/npm dependencies and local map/font/static assets. See [offline deployment](../docs/operations/offline-deployment.md), [acceptance](../docs/database/acceptance.md) and [recovery](../docs/database/migration-recovery.md).

The current Windows host has native supplemental service evidence and Compose configuration validation. Docker runtime, container volume restart and physically disconnected deployment remain separately unexecuted. Four databases sharing PostgreSQL have a common process failure domain; this is not high availability.

The nonsecret [native acceptance workload profile](native/acceptance-workload.env.example) uses existing configuration keys for a240-second worker batch and300-second lease. Production defaults remain30/60seconds; the measured two-scene batch exceeded120seconds. Start API and worker with the same declared profile and read the [acceptance limitations](../docs/database/acceptance.md).
