"""Environment-backed service configuration."""

from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LZO_", extra="ignore")

    storage_mode: Literal["legacy", "split"] = "legacy"
    gis_database_url: str | None = Field(default=None, repr=False)
    timeseries_database_url: str | None = Field(default=None, repr=False)
    catalog_database_url: str | None = Field(default=None, repr=False)
    gis_migration_database_url: str | None = Field(default=None, repr=False)
    timeseries_migration_database_url: str | None = Field(default=None, repr=False)
    catalog_migration_database_url: str | None = Field(default=None, repr=False)

    @model_validator(mode="after")
    def explicit_split_stores(self):
        if self.storage_mode == "split" and any(not getattr(self, f"{d}_database_url") for d in ("gis", "timeseries", "catalog")):
            raise ValueError("split storage requires explicit GIS, timeseries and catalog database URLs")
        return self

    database_url: str = Field(repr=False)
    migration_database_url: str | None = Field(default=None, repr=False)
    fuseki_url: str = Field(repr=False)
    oidc_issuer: str = Field(repr=False)
    oidc_discovery_url: str = Field(repr=False)
    oidc_client_id: str = "lzo-api"
    oidc_client_secret: str | None = Field(default=None, repr=False)
    oidc_backchannel_base: str | None = None
    file_root: Path
    blob_provider: Literal['local', 's3'] = 'local'
    s3_endpoint: str | None = Field(default=None, repr=False)
    s3_bucket: str = 'lzo-artifacts'
    s3_key_prefix: str = ''
    s3_region: str = 'us-east-1'
    s3_access_key_file: Path | None = Field(default=None, repr=False)
    s3_secret_key_file: Path | None = Field(default=None, repr=False)
    blob_staging_path: Path | None = None
    artifact_resource_budgets: dict[str, int] = Field(default_factory=dict)
    artifact_upload_session_seconds: int = Field(default=86400, ge=60, le=604800)
    artifact_upload_part_bytes: int = Field(default=8 * 1024 * 1024, ge=1)

    @model_validator(mode='after')
    def explicit_blob_configuration(self):
        import re
        if self.s3_key_prefix and not re.fullmatch(r'[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*', self.s3_key_prefix):
            raise ValueError('S3 key prefix must contain safe nonempty path segments')
        if self.blob_provider == 's3' and not all((self.s3_endpoint, self.s3_access_key_file,
                                                  self.s3_secret_key_file, self.blob_staging_path)):
            raise ValueError('s3 requires endpoint, credential files and local staging path')
        if any(value < 1 for value in self.artifact_resource_budgets.values()):
            raise ValueError('resource budgets must be positive')
        return self
    artifact_max_upload_bytes: int = Field(default=50 * 1024 * 1024, ge=1)
    artifact_user_quota_bytes: int = Field(default=2 * 1024 * 1024 * 1024, ge=1)
    artifact_scope_quota_bytes: int = Field(default=20 * 1024 * 1024 * 1024, ge=1)
    ingestion_transfer_seconds: float = Field(default=60, gt=0)
    source_encryption_key: str | None = Field(default=None,repr=False)
    source_ca_bundle: Path | None = None
    source_scan_timeout_seconds: int = Field(default=120,ge=1,le=3600)
    ingestion_max_bytes: int = Field(default=8*1024*1024, ge=1)
    ingestion_max_rows: int = Field(default=10000, ge=1)
    ingestion_preview_sync_bytes: int = Field(default=65536,ge=1)
    ingestion_preview_sync_rows: int = Field(default=50,ge=1)
    ingestion_preview_rows_per_batch: int = Field(default=20,ge=1,le=200)
    ingestion_max_columns: int = Field(default=256, ge=1)
    ingestion_max_cells: int = Field(default=100000, ge=1)
    ingestion_max_text: int = Field(default=65536, ge=1)
    ingestion_max_depth: int = Field(default=32, ge=1)
    ingestion_max_expanded_bytes: int = Field(default=32*1024*1024, ge=1)
    ingestion_max_members: int = Field(default=256, ge=1)
    ingestion_max_ratio: int = Field(default=200, ge=1)
    ingestion_max_output_bytes: int = Field(default=16*1024*1024, ge=1)
    ingestion_timeout_ms: int = Field(default=20000, ge=1)
    ingestion_memory_mb: int = Field(default=512, ge=1)
    ca_bundle: Path | None = None
    public_origin: str = "https://localhost:8443"
    session_encryption_key: str | None = Field(default=None, repr=False)
    session_lifetime_seconds: int = Field(default=28800, ge=300, le=86400)
    identity_admin_client_id: str = "lzo-identity-admin"
    identity_admin_client_secret: str | None = Field(default=None, repr=False)
    identity_admin_url: str | None = None
    invitation_smtp_configured: bool = False
    list_page_size: int = Field(default=50, ge=1)
    list_max_page_size: int = Field(default=200, ge=1)
    job_lease_seconds: int = Field(default=60, ge=1)
    job_max_attempts: int = Field(default=3, ge=1, le=100)
    job_retry_base_seconds: int = Field(default=5, ge=1)
    job_retry_cap_seconds: int = Field(default=300, ge=1)
    job_poll_seconds: float = Field(default=1, gt=0)
    job_claim_scan_limit: int = Field(default=50, ge=1)
    job_batch_timeout_seconds: int = Field(default=30, ge=1)
    job_max_batches_per_run: int = Field(default=100, ge=1)
    job_max_json_bytes: int = Field(default=65536, ge=256)
    job_queue_limit: int = Field(default=10000, ge=1)
    inference_max_triples: int = Field(default=1000000, ge=0)
    inference_max_proofs: int = Field(default=2000000, ge=0)
    inference_timeout_ms: int = Field(default=15000, ge=1)
    query_timeout_ms: int = Field(default=15000, ge=1)
    query_max_rows: int = Field(default=1000, ge=1)
    query_max_quads: int = Field(default=1000000, ge=1)
    query_work_limit: int = Field(default=1000000, ge=1)
    query_ast_limit: int = Field(default=10000, ge=1)
    query_memory_mb: int = Field(default=512, ge=1)
    query_input_bytes: int = Field(default=16*1024*1024, ge=1)
    query_output_bytes: int = Field(default=2*1024*1024, ge=1)
    query_read_response_bytes: int = Field(default=32*1024*1024, ge=1)
    query_read_total_bytes: int = Field(default=128*1024*1024, ge=1)
    query_parse_output_bytes: int = Field(default=64*1024*1024, ge=1)
    query_max_proof_nodes: int = Field(default=200, ge=1)
    query_max_hops: int = Field(default=5, ge=1)
    worker_handler_modules: str = "lzo.worker.handlers"
