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


def test_legacy_repair_retains_all_exact_baseline_pins_after_dependency_advance(object_case, gis_tick, monkeypatch):
    from uuid import uuid4
    from sqlalchemy import select
    from lzo.contracts import RevisionRef
    from lzo.semantic import facts, evidence
    from lzo.ontology.releases import get_execution_bundle
    from lzo.auth.permissions import permission_context
    s = object_case
    s.scopes['retained_dependency'] = uuid4()
    grant = s.grant('alice', 'editor', scope='retained_dependency')
    s.login()
    dependency = s.client.post('/api/v1/objects', headers=s.headers(), json={'bundle_id': str(s.bundle_id), 'create': {
        'resource_id': str(uuid4()), 'class_iris': ['urn:Pond'], 'display_name': 'Exact retained dependency',
        'owner_scope_id': str(s.scopes['retained_dependency']), 'related_scope_ids': [], 'state': 'active',
        'data_nature': 'synthetic', 'assertions': [], 'decision_ref': None}})
    assert dependency.status_code == 201, dependency.text
    first = dependency.json()['revision']
    second = s.patch(first['resource_id'], 1)
    assert second.status_code == 200, second.text
    retained = (RevisionRef(**{**first, 'resource_id': UUID(first['resource_id']), 'event_id': UUID(first['event_id'])}),
        RevisionRef(**{**second.json()['revision'], 'resource_id': UUID(first['resource_id']),
            'event_id': UUID(second.json()['revision']['event_id'])}))
    obj = UUID(s.create_object().json()['revision']['resource_id'])
    # Existing trusted snapshot machinery supports several exact versions.
    with s.transaction() as uow, permission_context(uow):
        uow.ontology_settings = s.config
        for pin in retained:
            facts.verify_reference(uow, s.actor('alice'), pin)
        row = facts.get_object_row(uow, s.actor('alice'), obj, 'edit')
        facts.record_state(uow, s.actor('alice'), row, 1, get_execution_bundle(s.actor('alice'), s.bundle_id),
            {pin.resource_id for pin in retained}, 'object.facts_changed', {'operation': 'test.retained_pins'})
        uow.commit()
    gis_tick(s)
    with monkeypatch.context() as patch:
        patch.setattr(evidence, 'attach_to_object', lambda *args: None)
        attached = s.client.post(f'/api/v1/artifacts?resource_id={obj}&filename=legacy-pins.txt',
            headers={**s.headers(2), 'Content-Type': 'text/plain'}, content=b'Exact baseline evidence')
    assert attached.status_code == 201, attached.text
    newer = s.patch(first['resource_id'], 2)
    assert newer.status_code == 200, newer.text
    with s.transaction() as uow:
        before = list(uow.session.execute(text('SELECT event_id,payload FROM revisions WHERE resource_id=:id ORDER BY revision'), {'id': obj}))
        baseline = before[1].payload['dependency_revisions']
        assert {(p['revision'], p['event_id']) for p in baseline if p['resource_id'] == first['resource_id']} == {
            (pin.revision, str(pin.event_id)) for pin in retained}
    endpoint = f'/api/v1/objects/{obj}/evidence/reconcile'
    repaired = s.client.post(endpoint, headers=s.headers(3))
    assert repaired.status_code == 200, repaired.text
    with s.transaction() as uow:
        row = uow.session.execute(text('SELECT payload FROM revisions WHERE event_id=:id'), {'id': UUID(repaired.json()['event_id'])}).scalar_one()
        assert all(pin in row['dependency_revisions'] for pin in baseline)
        assert {(p['revision'], p['event_id']) for p in row['dependency_revisions'] if p['resource_id'] == first['resource_id']} == {
            (pin.revision, str(pin.event_id)) for pin in retained}
        scheduled = uow.session.execute(text('SELECT dependency_refs FROM projection_events WHERE event_id=:id'),
            {'id': UUID(repaired.json()['event_id'])}).scalar_one()
        assert all(pin in scheduled for pin in baseline)
        assert list(uow.session.execute(text('SELECT event_id,payload FROM revisions WHERE resource_id=:id AND revision<4 ORDER BY revision'), {'id': obj})) == before
    changed = s.patch(obj, 4)
    assert changed.status_code == 200, changed.text
    with s.transaction() as uow:
        pins = uow.session.execute(text('SELECT payload FROM revisions WHERE resource_id=:id AND revision=5'), {'id': obj}).scalar_one()['dependency_revisions']
        assert all(pin in pins for pin in baseline)
        assert not any(p['resource_id'] == first['resource_id'] and p['revision'] == 3 for p in pins)
    s.revoke(grant)
    s.login()
    assert s.client.post(endpoint, headers=s.headers(5)).status_code == 404
