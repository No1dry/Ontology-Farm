"""Lease fencing uses PostgreSQL wall clock, never a worker's local clock."""
from sqlalchemy import func, select

from lzo.errors import Conflict
from lzo.worker.models import jobs


def fenced_row(uow, job_id, lease_token):
    row = uow.session.execute(select(jobs).where(jobs.c.id == job_id).with_for_update()).mappings().first()
    now = uow.session.execute(select(func.clock_timestamp())).scalar_one()
    if (row is None or row["status"] != "running" or row["lease_token"] != lease_token
            or row["lease_expires_at"] <= now):
        raise Conflict("LEASE_EXPIRED", "Worker lease is no longer valid")
    return row
