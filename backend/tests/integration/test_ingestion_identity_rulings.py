"""I02 uses actual source/history/approval services and isolated PostgreSQL."""
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select
from lzo.ingestion.schemas import MappingProfile
from lzo.ingestion.sources import SourceService
from lzo.ingestion.identity import IdentityService


def profile(source_id, **updates):
    return MappingProfile(id=uuid4(), source_id=source_id, revision=1,
        class_iri="urn:Pond", identity_paths=("code",), confirmed_identity_rule_id=None,
        name_path="name", fields=(), geometry_path=None, source_crs=None,
        source_timezone="Asia/Shanghai", digest="", **updates)


def test_mapping_versions_are_immutable_and_save_does_not_activate(evidence_case):
    from lzo.governance.models import revisions
    s = evidence_case
    response = s.source()
    assert response.status_code == 201
    source_id = UUID(response.json()["id"])
    with s.transaction() as uow:
        first = SourceService(uow).save_mapping(uow, s.actor("alice"), profile(source_id))
        assert first.digest and first.revision == 1
        assert SourceService(uow).read(s.actor("alice"), source_id).mapping_version_id is None
        uow.commit()
    with s.transaction() as uow:
        second = SourceService(uow).save_mapping(uow, s.actor("alice"), profile(source_id))
        assert second.id != first.id and second.revision == 2
        payload = uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id == first.id)).scalar_one()
        assert payload["digest"] == first.digest
        uow.commit()


def test_source_mapping_route_honors_current_rights_and_preconditions(evidence_case):
    s = evidence_case
    response = s.source()
    assert response.status_code == 201
    source_id = UUID(response.json()["id"])
    value = profile(source_id).model_dump(mode="json")
    for key in ("id","revision","digest"):value.pop(key)
    headers = {k:v for k,v in s.headers("alice", key=str(uuid4())).items() if k != "If-Match"}
    response = s.http.put(f"/api/v1/sources/{source_id}/mapping", headers=headers, json=value)
    assert response.status_code == 428
    response = s.http.put(f"/api/v1/sources/{source_id}/mapping", headers=s.headers(key="mapping"), json=value)
    assert response.status_code == 200
    saved = response.json()
    replay = s.http.put(f"/api/v1/sources/{source_id}/mapping", headers=s.headers(key="mapping"), json=value)
    assert replay.status_code == 200 and replay.json() == saved
    s.revoke(s.source_grant)
    response = s.http.get(f"/api/v1/sources/{source_id}/mapping", headers=s.headers())
    assert response.status_code in (403, 404)
