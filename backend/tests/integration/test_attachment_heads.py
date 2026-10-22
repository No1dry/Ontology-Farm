"""A real public attachment must preserve the governed semantic/GIS head."""
import hashlib
from uuid import UUID

import pytest
from sqlalchemy import text


@pytest.mark.parametrize('resumable', [False, True])
def test_attachment_then_optimistic_edit_keeps_semantic_geometry_and_evidence(object_case, gis_tick, resumable):
    s = object_case
    obj = s.create_object().json()['revision']['resource_id']
    geometry = {'resource_id': obj, 'expected_revision': 1, 'source_crs': 'EPSG:4326',
        'geojson': {'type': 'Point', 'coordinates': [114, 30]}, 'source_support': s.assertion(obj)['support']}
    response = s.client.put(f'/api/v1/geometry/{obj}', headers=s.headers(1),
        json={'geometry': geometry, 'bundle_id': str(s.bundle_id)})
    assert response.status_code == 200, response.text
    gis_tick(s)
    content = b'Synthetic attachment: retained evidence'
    if resumable:
        created = s.client.post('/api/v1/artifacts/uploads', headers=s.headers(2), json={
            'resource_id': obj, 'filename': 'synthetic.txt',
            'media_type': 'text/plain', 'size_bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(),
            'metadata': {'synthetic': True}})
        assert created.status_code == 201, created.text
        identifier = created.json()['id']
        uploaded = s.client.put(f'/api/v1/artifacts/uploads/{identifier}/parts/1',
            headers={**s.headers(), 'Content-Type': 'application/octet-stream'}, content=content)
        assert uploaded.status_code == 200, uploaded.text
        attached = s.client.post(f'/api/v1/artifacts/uploads/{identifier}/complete', headers=s.headers())
    else:
        attached = s.client.post(f'/api/v1/artifacts?resource_id={obj}&filename=synthetic.txt',
            headers={**s.headers(2), 'Content-Type': 'text/plain'}, content=content)
    assert attached.status_code == (200 if resumable else 201), attached.text
    version = attached.json()['version_id']
    detail = s.client.get(f'/api/v1/objects/{obj}').json()
    assert detail['revision'] == 3
    assert s.patch(obj, 2).status_code == 409
    assert s.client.get(f'/api/v1/geometry/{obj}').json()['gis_status'] == 'pending'
    gis_tick(s)
    current = s.client.get(f'/api/v1/geometry/{obj}').json()
    assert current['gis_status'] == 'current' and current['features'][0]['geometry']['coordinates'] == [114, 30]
    changed = s.patch(obj, 3, [s.assertion(obj, state='candidate')])
    assert changed.status_code == 200, changed.text
    gis_tick(s)
    assert s.client.get(f'/api/v1/geometry/{obj}').json()['features'][0]['geometry']['coordinates'] == [114, 30]
    assert s.client.get(f'/api/v1/artifacts/{version}/content').content == content
    with s.transaction() as uow:
        row = uow.session.execute(text('SELECT revision FROM semantic_objects WHERE id=:id'), {'id': UUID(obj)}).scalar_one()
        assert row == 4
        event = uow.session.execute(text("SELECT event_id FROM revisions WHERE resource_id=:id AND revision=3"), {'id': UUID(obj)}).scalar_one()
        refs = uow.session.execute(text('SELECT evidence_refs FROM revision_metadata WHERE event_id=:event'), {'event': event}).scalar_one()
        assert version in refs
    s.revoke(s.object_editor_grant)
    s.login()
    assert s.client.get(f'/api/v1/artifacts/{version}/content').status_code == 404


def test_explicit_legacy_repair_is_append_only_current_authorized_and_conflict_safe(object_case, gis_tick, monkeypatch):
    from lzo.semantic import evidence
    from lzo.governance.revisions import append_revision
    s = object_case
    obj = s.create_object().json()['revision']['resource_id']
    geometry = {'resource_id': obj, 'expected_revision': 1, 'source_crs': 'EPSG:4326',
        'geojson': {'type': 'Point', 'coordinates': [114, 30]}, 'source_support': s.assertion(obj)['support']}
    assert s.client.put(f'/api/v1/geometry/{obj}', headers=s.headers(1),
        json={'geometry': geometry, 'bundle_id': str(s.bundle_id)}).status_code == 200
    gis_tick(s)
    # Preserved pre-fix producer behavior, through the real public upload path.
    original = evidence.attach_to_object
    monkeypatch.setattr(evidence, 'attach_to_object', lambda *args: None)
    attached = s.client.post(f'/api/v1/artifacts?resource_id={obj}&filename=legacy.txt',
        headers={**s.headers(2), 'Content-Type': 'text/plain'}, content=b'Preserved legacy evidence')
    assert attached.status_code == 201, attached.text
    monkeypatch.setattr(evidence, 'attach_to_object', original)
    with s.transaction() as uow:
        before = list(uow.session.execute(text('SELECT event_id,payload FROM revisions WHERE resource_id=:id ORDER BY revision'), {'id': UUID(obj)}))
    from lzo.storage.migration import MigrationService, MigrationError, _snapshot
    preflight = MigrationService(s.app, None, s.config, s.config)
    with pytest.raises(MigrationError, match='LEGACY_HEAD_REPAIR_REQUIRED'):
        preflight._check_legacy_heads()
    endpoint = f'/api/v1/objects/{obj}/evidence/reconcile'
    assert s.client.post(endpoint, headers=s.headers(2)).status_code == 409
    s.login('nobody')
    assert s.client.post(endpoint, headers=s.headers(3)).status_code == 404
    s.login()
    headers = s.headers(3)
    repaired = s.client.post(endpoint, headers=headers)
    assert repaired.status_code == 200, repaired.text
    assert repaired.json()['revision'] == 4
    assert s.client.post(endpoint, headers=headers).json() == repaired.json()
    preflight._check_legacy_heads()
    with s.transaction() as uow:
        items, _ = _snapshot(uow)
        replay = next(item['value'] for item in items if item['kind'] == 'geometry' and item['key'] == repaired.json()['event_id'])
        assert replay.payload['geometry']['normalized']['coordinates'] == [114, 30]
        assert attached.json()['version_id'] in replay.source_refs
    assert s.client.get(f'/api/v1/geometry/{obj}').json()['gis_status'] == 'pending'
    gis_tick(s)
    assert s.client.get(f'/api/v1/geometry/{obj}').json()['features'][0]['geometry']['coordinates'] == [114, 30]
    with s.transaction() as uow:
        after = list(uow.session.execute(text('SELECT event_id,payload FROM revisions WHERE resource_id=:id AND revision<4 ORDER BY revision'), {'id': UUID(obj)}))
        assert before == after
    assert s.patch(obj, 4).status_code == 200
    missing = UUID(s.create_object().json()['revision']['resource_id'])
    with s.transaction() as uow:
        from uuid import uuid4
        append_revision(uow, s.actor('alice'), missing, 1, {'evidence_version_id': str(uuid4()),
            'sha256': '0'*64, 'size_bytes': 1, 'metadata': {}}, 'evidence.attached')
        uow.commit()
    assert s.client.post(f'/api/v1/objects/{missing}/evidence/reconcile', headers=s.headers(2)).status_code == 404
    # An intervening unknown event cannot be hidden by the repair/snapshot reader.
    with s.transaction() as uow:
        append_revision(uow, s.actor('alice'), UUID(obj), 5, {'unknown': True}, 'test.unrecognized_head')
        uow.commit()
    blocked = s.client.get(f'/api/v1/geometry/{obj}')
    assert blocked.status_code == 200 and blocked.json()['gis_status'] == 'pending'
    assert blocked.json()['features'] == []  # No exact delivery for the unknown head.
    from lzo.gis.service import accepted_geometry
    from lzo.errors import ApiError
    with s.transaction() as uow, pytest.raises(ApiError) as unknown_snapshot:
        uow.ontology_settings = s.config
        accepted_geometry(uow, UUID(obj))
    assert unknown_snapshot.value.code == 'OBJECT_SNAPSHOT_INCONSISTENT'
    refused = s.client.post(endpoint, headers=s.headers(6))
    assert refused.status_code == 409 and refused.json()['code'] == 'LEGACY_EVIDENCE_TAIL_REQUIRED'
    s.grant('alice', 'viewer', scope='farm_a')
    s.revoke(s.object_editor_grant)
    s.login()
    assert s.client.post(endpoint, headers={**headers, 'X-CSRF-Token': s.csrf}).status_code == 403
