# Original synthetic telemetry examples

All bytes and coordinates here are authored synthetic examples. UUIDs are
placeholders, no devices are installed, and no live ONVIF/DJI connectivity or
control is provided. `field.csv` preserves zero; `pond.json` preserves decimal
precision; `orchard.geojson` carries a WGS84 trajectory point with unknown height.

Validate and bind an example to existing authorized identities:

```powershell
.venv/Scripts/python.exe scripts/replay_telemetry.py --source-id SOURCE_UUID --input tests/fixtures/telemetry/pond.json --format json --object-id OBJECT_UUID --device-id DEVICE_UUID
```

Without `--base-url` the script only prints the validated normalized manifest.
Authenticated incremental replay also takes `--base-url`, `--session-file`,
`--csrf-file`, optional `--ca-file`, and a reusable `--idempotency-key`. Secrets
are read from private files. Static bytes first use the existing governed
artifact upload API; then pass `--artifact-version-id VERSION_UUID --format json`
in place of `--input` and UUID overrides. The backend reparses the registered
private bytes, pins their exact version, and sends the same normalized contract.

For corrections, increment `version` and supply
`"supersedes":{"source_record_id":"pond-temperature","version":1}`.
Withdrawal is a subsequent explicit version with `"operation":"withdraw"`.
Reusing an existing source record/version with altered value or sample time is
a conflict. Withdrawal retains immutable bytes, samples and provenance.

The source must already exist with `data_nature=synthetic`; its namespace,
permissions and active governed mapping are resolved by the backend. Input
format adapters consume this normalized field contract, without silently
guessing device model, clocks, units, CRS or missing values. Nullable CSV values
use the literal `null`; `0` is zero. Model/device and geometry references are
optional exact version pins; unknown historical assignments remain null.

Partition operations are separate: a DBA provisions
`lzo_timeseries_maintainer` (NOLOGIN group by default in Compose), grants it to a
dedicated operations login, and runs `scripts/telemetry_partitions.py` with
`LZO_TIMESERIES_MAINTENANCE_DATABASE_URL`. Native owned development can create its
restricted login with `scripts/multistore_native.py provision-telemetry-maintenance`.
Membership alone is insufficient: the connection must explicitly select
`current_user=lzo_timeseries_maintainer`, using the validated URL query option
`options=-c%20role%3Dlzo_timeseries_maintainer` (decoded as
`options=-c role=lzo_timeseries_maintainer`). See the role-selection example in
[the acceptance runbook](../../../docs/database/acceptance.md). Do not grant
partition DDL to the application role.
Create the needed UTC calendar months before delivery. The application cannot
create partitions; absent partitions produce explicit retries/failures. After
maintenance, an authorized caller can retry the exact accepted envelope via
`POST /api/v1/telemetry/batches/{id}/retry`. No deletion operation is exposed.

Queries use `[start,end)` and time/ID keyset cursors. Later arrivals before a
cursor require restarting the interval. `latest=true` returns one valid known
numeric record per source/object/device/point/metric stream after explicit
supersession/withdrawal filtering. Independent sources remain separate; no
cross-source winner is invented. Aggregates describe only the returned page.
Queries cap candidate scans at 10,000 and ask for a narrower interval if needed.
Watermarks are acknowledged batch acceptance clocks, not a promise that no
late device data remains. Retention preview reports a page and never deletes
referenced evidence or history.

An optional `quality=valid|suspect|invalid|unknown` selector is applied before
page limits and aggregates and participates in cursor identity. `latest=true`
accepts only omitted/valid quality. Optional source UUID narrows source scope.

The additive structured fields `measurement_method`, `calibration`,
`time_quality` and `media_metadata` are JSON objects in JSON/GeoJSON replay;
CSV cells use JSON object syntax. Their unknown values remain nullable and
absent values do not alter old accepted hashes. Model and firmware references
remain exact immutable pins. See [schema guide](../../../docs/database/schema-guide.md)
for the full structure and catalog authorship fields.

Controlled archival uses `POST /api/v1/telemetry/archives` with explicit object,
source, kind and exclusive `before` cutoff; optional start/quality/exact record
IDs narrow the selection. It requires current authority and acknowledged
complete records, records an audit and retains an immutable private manifest.
At most `min(1000,list_max_page_size)` records and 2 MB are captured synchronously;
oversized selections require a narrower interval or explicit subset. It never
deletes source history/evidence or changes current/latest values. Current
source/evidence rights continue to govern archive and generic artifact reads.
