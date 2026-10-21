"""Operational aggregates; optional sources never become core ready dependencies."""
import re
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from lzo.auth.permissions import require_permission
from lzo.contracts import ResourceScope
from lzo.governance.models import platform_watermarks
from lzo.worker.models import jobs, source_health


class SourceFailure(BaseModel):
    source_id: UUID
    status: str
    error_code: str | None


class HealthOverview(BaseModel):
    queue_depth: int
    running_jobs: int
    failed_jobs: int
    expired_leases: int
    ledger_watermark: int
    input_watermark: int
    projection_watermark: int
    inference_watermark: int
    failed_sources: int
    failed_source_details: list[SourceFailure]
    failed_sources_truncated: bool
    limits: dict[str, int]


def record_source_health(uow, source_id: UUID, status: str, error_code: str | None = None):
    if status not in {"ready", "offline", "degraded"}:
        raise ValueError("Invalid source health state")
    if error_code and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,127}", error_code):
        raise ValueError("Health contains only stable error codes")
    values = dict(source_id=source_id, status=status, error_code=error_code, checked_at=func.clock_timestamp())
    uow.session.execute(insert(source_health).values(**values).on_conflict_do_update(
        index_elements=[source_health.c.source_id], set_=values))


def overview(uow, actor, settings):
    require_permission(actor, "operations.view", ResourceScope(None, None, (), "operations"), uow=uow)
    counts = dict(uow.session.execute(select(jobs.c.status, func.count()).group_by(jobs.c.status)).all())
    watermarks = uow.session.execute(select(platform_watermarks)).mappings().one()
    expired = uow.session.execute(select(func.count()).select_from(jobs).where(
        jobs.c.status == "running", jobs.c.lease_expires_at <= func.clock_timestamp())).scalar_one()
    failed_sources = uow.session.execute(select(func.count()).select_from(source_health).where(
        source_health.c.status != "ready")).scalar_one()
    size = min(settings.list_page_size, settings.list_max_page_size)
    failures = uow.session.execute(select(source_health).where(source_health.c.status != "ready")
                                  .order_by(source_health.c.source_id).limit(size)).mappings().all()
    return HealthOverview(queue_depth=counts.get("queued", 0), running_jobs=counts.get("running", 0),
        failed_jobs=counts.get("failed", 0), expired_leases=expired,
        ledger_watermark=watermarks["event_position"], input_watermark=watermarks["input_watermark"],
        projection_watermark=watermarks["projection_watermark"], inference_watermark=watermarks["inference_watermark"],
        failed_sources=failed_sources,
        failed_source_details=[SourceFailure(**row) for row in failures],
        failed_sources_truncated=failed_sources > size, limits={"queue": settings.job_queue_limit,
            "attempts": settings.job_max_attempts, "batch_seconds": settings.job_batch_timeout_seconds,
            "batches_per_run": settings.job_max_batches_per_run})
