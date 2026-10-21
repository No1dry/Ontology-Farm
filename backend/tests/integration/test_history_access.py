"""History visibility is filtered in SQL before pagination."""
from datetime import datetime, timezone

import pytest

from lzo.errors import ApiError


def test_history_revocation_hides_previous_revisions(scope_scenario):
    from lzo.governance.history import HistoryService, HistoryQuery
    s = scope_scenario
    grant = s.grant("alice", "viewer", scope="farm_a")
    with s.transaction() as uow:
        service = HistoryService(uow)
        assert service.get(s.actor("alice"), s.ids_by_name["object_a"], 1).payload == {"value": "initial"}
    s.revoke(grant)
    with s.transaction() as uow:
        service = HistoryService(uow)
        with pytest.raises(ApiError) as failure:
            service.get(s.actor("alice"), s.ids_by_name["object_a"], 1)
        assert failure.value.status == 404
        assert service.list(s.actor("alice"), HistoryQuery(), None).items == []


def test_cross_resource_filters_and_pagination_only_include_visible_rows(scope_scenario):
    from lzo.governance.history import HistoryService, HistoryQuery
    s = scope_scenario
    s.grant("alice", "viewer", scope="farm_b")
    with s.transaction() as uow:
        service = HistoryService(uow, page_size=1)
        page = service.list(s.actor("alice"), HistoryQuery(resource_kind="object"), None)
        assert [ref.resource_id for ref in page.items] == [s.ids_by_name["object_b"]]
        assert page.next_cursor is None
        assert service.list(s.actor("nobody"), HistoryQuery(), None).items == []
        assert service.list(s.actor("alice"), HistoryQuery(actor_id=s.users["nobody"]), None).items == []
        assert service.list(s.actor("alice"), HistoryQuery(recorded_to=datetime(2000, 1, 1, tzinfo=timezone.utc)), None).items == []


def test_revision_detail_and_diff_preserve_real_provenance(scope_scenario):
    from lzo.governance.history import HistoryService
    from lzo.governance.revisions import append_revision
    s = scope_scenario
    s.grant("alice", "viewer", scope="farm_a")
    with s.transaction() as uow:
        ref = append_revision(uow, s.admin_actor(), s.ids_by_name["object_a"], 1,
                              {"value": "changed", "extra": 3}, "object.edited")
        uow.commit()
    with s.transaction() as uow:
        service = HistoryService(uow)
        detail = service.get(s.actor("alice"), ref.resource_id, 2)
        assert detail.actor_id == s.users["admin"]
        assert detail.task_id is None and detail.bundle_id is None
        assert detail.evidence_refs == [] and detail.proof_refs == []
        diff = service.diff(s.actor("alice"), ref.resource_id, 1, 2)
        assert diff.changed == {"value": {"from": "initial", "to": "changed"}}
        assert diff.added == {"extra": 3}
        assert diff.removed == {}
        assert diff.actors == [s.users["admin"]]
        assert ref.event_id in diff.event_ids


def test_invalid_history_cursor_is_typed_error(scope_scenario):
    from lzo.governance.history import HistoryService, HistoryQuery
    s = scope_scenario
    with s.transaction() as uow:
        with pytest.raises(ApiError) as failure:
            HistoryService(uow).list(s.actor("alice"), HistoryQuery(), "not-a-cursor")
        assert failure.value.status == 422


def test_history_task_filter_and_metadata_preserve_producer_refs(scope_scenario):
    from uuid import uuid4
    from sqlalchemy import insert
    from lzo.governance.history import HistoryService, HistoryQuery, revision_metadata
    from lzo.governance.revisions import append_revision
    s = scope_scenario
    s.grant("alice", "viewer", scope="farm_a")
    task, bundle, evidence, proof = (uuid4() for _ in range(4))
    with s.transaction() as uow:
        ref = append_revision(uow, s.admin_actor(), s.ids_by_name["object_a"], 1, {"value": "produced"}, "object.edited")
        uow.session.execute(insert(revision_metadata).values(event_id=ref.event_id, task_id=task, bundle_id=bundle,
            evidence_refs=[str(evidence)], proof_refs=[str(proof)]))
        uow.commit()
    with s.transaction() as uow:
        service = HistoryService(uow)
        page = service.list(s.actor("alice"), HistoryQuery(task_id=task), None)
        assert page.items == [ref]
        detail = service.get(s.actor("alice"), ref.resource_id, ref.revision)
        assert (detail.task_id, detail.bundle_id, detail.evidence_refs, detail.proof_refs) == (task, bundle, [evidence], [proof])
