# Private artifact object store

SeaweedFS is pinned to official release **4.48**. The Windows full release is
`https://github.com/seaweedfs/seaweedfs/releases/download/4.48/windows_amd64.zip`,
SHA-256 `fe90c04c0620ad1a1c756f86cd5e1443773f56a446688077a4bb5ec04c3cc874`.
The Compose image is pinned to the registry manifest digest in compose.yaml.
The volume, filer database, and master state must all be backed up together.

Native supplemental setup (from this worktree, using its Python environment):

```powershell
python scripts/object_store_native.py prepare --archive <verified-full-release.zip>
python scripts/object_store_native.py start
python scripts/object_store_native.py restart
python scripts/object_store_native.py stop
```

The runner owns `.runtime/object-store`, validates process identity before stopping,
binds only loopback, disables telemetry and unused catalog listeners, generates
credentials and emits runtime-only configuration references in `services.env`.
It does not use or alter other worktrees. Never commit that directory. Import the
generated environment along with the backend's database configuration before running
native S3 tests. Start tests with a worktree-owned `--basetemp`.

Compose uses `python scripts/dev.py prepare` to generate four credential files in
ignored `infra/compose/secrets`. Only the initializer receives bootstrap credentials;
the API and worker receive bucket-scoped credentials. No S3 or filer port is published.
The initializer checks real write/read before allowing dependent services to start.
Native acceptance does not establish Compose runtime acceptance.

Backend selection is explicit `LZO_BLOB_PROVIDER=local|s3` (`local` is the legacy
compatibility default; Compose explicitly selects `s3`). S3 requires
`LZO_S3_ENDPOINT`, `LZO_S3_BUCKET`, `LZO_S3_REGION`,
`LZO_S3_ACCESS_KEY_FILE`, `LZO_S3_SECRET_KEY_FILE`, and
`LZO_BLOB_STAGING_PATH`. File references contain secrets; configuration reprs and
API payloads do not contain credentials or signed URLs.

All producers use `lzo.storage.blobs.get_blob_store(settings)`. `put`,
`stage_async`, `complete`, `discard`, `open`, and `open_range` retain BlobRef's
independent business version UUID, SHA-256, byte length and media type. The local
provider writes `file_root/artifacts` and reads the old `file_root` layout only
when the canonical object is missing. It never silently falls back from S3.
S3 `open` returns a read-only, seekable named temporary file and deletes it on close,
so SQLite map packages and parser subprocesses can consume it. Callers must close
all streams. Memory stays bounded; disk use can reach the object's full size.

Single HTTP byte ranges and suffix ranges are supported. S3 range reads currently
materialize and verify the entire object before serving the selected bytes. This
preserves complete digest validation, but does not provide remote partial-download
latency for multi-gigabyte video. Deployments must size staging disk and configure
`LZO_ARTIFACT_RESOURCE_BUDGETS` by trusted resource kind before accepting large media.

Resumable routes are `/api/v1/artifacts/uploads`: create with resource, expected
SHA-256/size, filename, media type, optional metadata/previous version plus existing
Idempotency-Key/If-Match/CSRF; PUT `/{id}/parts/{number}` (one-based fixed part size,
short last part); GET `/{id}`; POST `/{id}/complete`; DELETE `/{id}`. Current actor,
ownership and resource permission are rechecked on every operation. Creation
reserves full user/scope quota. Part retries require identical bytes. Completion
verifies the total digest, registers the ordinary governed artifact, and converts
the reservation into one accepted-version charge in one core transaction.

Call `UploadService(uow, store, settings).expire()` from trusted maintenance and
commit to release expired reservations. Expired/cancelled sessions cannot publish.
Crash retries reuse the upload session UUID as final business version UUID and
never overwrite an accepted object. Parts and orphan content are deliberately
retained indefinitely in this release; there is no unsafe eager garbage collection.
Before adding collection, reconcile active sessions, retry jobs and accepted
artifact references, and apply a documented retention grace period. Bucket Object
Lock, encryption at rest, automatic lifecycle collection, and distributed atomic
transactions are not claimed.
