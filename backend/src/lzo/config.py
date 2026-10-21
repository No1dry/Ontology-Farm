"""Environment-backed service configuration."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LZO_", extra="ignore")

    database_url: str = Field(repr=False)
    migration_database_url: str | None = Field(default=None, repr=False)
    fuseki_url: str = Field(repr=False)
    oidc_issuer: str = Field(repr=False)
    oidc_discovery_url: str = Field(repr=False)
    oidc_client_id: str = "lzo-api"
    oidc_client_secret: str | None = Field(default=None, repr=False)
    oidc_backchannel_base: str | None = None
    file_root: Path
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
