"""History HTTP contract, filtered by current database permissions."""
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from lzo.auth.router import AuthRequest, authenticated
from lzo.contracts import Page, RevisionRef
from lzo.governance.history import HistoryQuery, HistoryService, RevisionDetail, RevisionDiff

router = APIRouter(prefix="/api/v1/history", tags=["History"])


@router.get("", response_model=Page[RevisionRef], operation_id="listHistory")
def history(resource_id: UUID | None = None, resource_kind: str | None = None,
            actor_id: UUID | None = None, task_id: UUID | None = None,
            recorded_from: datetime | None = None, recorded_to: datetime | None = None,
            cursor: str | None = None, auth: AuthRequest = Depends(authenticated)):
    return HistoryService(auth.uow, auth.service.settings.list_page_size,
                          auth.service.settings.list_max_page_size).list(auth.actor, HistoryQuery(resource_id, resource_kind,
        actor_id, task_id, recorded_from, recorded_to), cursor)


@router.get("/{resource_id}/revisions/{revision}", response_model=RevisionDetail, operation_id="getRevision")
def revision(resource_id: UUID, revision: int, auth: AuthRequest = Depends(authenticated)):
    return HistoryService(auth.uow).get(auth.actor, resource_id, revision)


@router.get("/{resource_id}/diff", response_model=RevisionDiff, operation_id="diffRevisions")
def diff(resource_id: UUID, from_revision: int = Query(ge=1), to_revision: int = Query(ge=1),
         auth: AuthRequest = Depends(authenticated)):
    return HistoryService(auth.uow).diff(auth.actor, resource_id, from_revision, to_revision)
