# Offline deployment and verification boundaries

Current evidence distinguishes native loopback service tests, Python socket egress denial, static client-resource packaging and Compose configuration validation. A physically disconnected container deployment remains **unexecuted** on the current Windows host: no Docker engine/WSL exists. No host firewall, virtualization or reboot change is required or authorized by these instructions.

Before transfer, acquire and verify every image digest/archive in `infra/versions.lock.json`, `backend/requirements.lock`, both npm lockfiles, local fonts/map packages, certificates and private environment/secret files. Export pinned images and an offline wheel/npm cache or prebuilt application images on a connected build host. Keep manifests/checksums with the deployment; original data, credentials and dumps remain private. Development `docker build` may fetch missing sources/dependencies, so cache preparation is a separate step from runtime isolation.

On the target Docker host, using a newly named owned test project and persistent volumes:

1. Load pinned images and verify digests. Prepare the private Compose `.env`, S3 credential files and certificates. `docker compose --env-file infra/compose/.env -f infra/compose/compose.yaml config --quiet` must succeed.
2. Start the four independent databases/roles, Fuseki, S3, Keycloak and proxy with the checked-in Compose file; execute its `migrate` profile. API/worker receive app URLs, while the migrator receives owners. Verify readiness for all stores and local IdP.
3. Install an independently approved synthetic schema, create a scoped object, edit geometry, attach/read private bytes, replay incremental observations and source withdrawal, query graph/SPARQL/history, and revoke access while old professional indexes remain. Verify denied reads before counting success.
4. Disable **external** egress for this owned Compose project/test runner while retaining internal service communication; do not change other workspaces. Open packaged client/map/font assets through the local proxy and repeat core operations. A local vendor/API placeholder is not evidence of real field protocol compatibility.
5. Restart owned API/worker, all relevant database/TDB/S3/identity containers without deleting volumes. Recheck exact hashes/UUIDs/history/versions and current authorization. Exercise target outage, lost acknowledgement, exhausted retry and current-state recovery with unchanged events. Shared PostgreSQL means its outage affects all four logical databases.
6. Use the accepted [backup/restore controller](../database/migration-recovery.md) for coordinated material and a **new** isolated destination cluster/namespace. Verify actual SQL/RDF/private-byte references and current/revoked historical access. Record the target hardware and actual durations before setting RPO/RTO expectations.

The supplemental native offline command is reproducible without host network changes:

```powershell
.venv/Scripts/python.exe scripts/multistore_acceptance.py --env .runtime/multistore/services.env --env .runtime/multistore/maintenance.env --env .runtime/object-store/services.env --env .runtime/acceptance-services/idp.env --offline -- pytest backend/tests/integration/test_offline_process.py backend/tests/integration/test_map_tiles.py backend/tests/integration/test_attachment_heads.py -q --basetemp .runtime/offline-UNIQUE
```

`offline_guard.py` denies non-loopback Python socket DNS/connect calls and the inherited Python child guard is checked explicitly. Native libpq and service processes are not OS-sandboxed by this helper; they use declared loopback endpoints. This demonstrates that the exercised core/map/private-byte Python paths need no external API, and does not establish complete OS/container egress enforcement. The built HTML and bundled local `/assets` resources are separately inspected. A browser journey under actual disconnected container networking remains a target-host acceptance step.
