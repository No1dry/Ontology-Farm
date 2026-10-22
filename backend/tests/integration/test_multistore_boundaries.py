"""Focused cross-store boundary regressions on actual stores."""
import base64
import json
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text, update
from test_timeseries import telemetry_case


def test_gis_decoded_numeric_and_object_cursor_uuids_are_governed(object_case):
    s = object_case
    obj = s.create_object().json()['revision']
    for field in ('resource', 'event', 'actor', 'after'):
        for invalid in (42, {}):
            value = {'version': 1, 'resource': obj['resource_id'], 'event': obj['event_id'],
                'actor': str(s.users['alice']), 'authorization': s.actor('alice').authorization_version,
                'after': str(uuid4()), field: invalid}
            cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
            response = s.client.get(f"/api/v1/objects/{obj['resource_id']}/spatial-assets", params={'cursor': cursor})
            assert response.status_code == 422 and response.json()['code'] == 'INVALID_CURSOR'


@pytest.mark.parametrize('after', [[], ['2026-01-01T00:00:00+00:00'], 1, {}, ['2026-01-01T00:00:00+00:00', {}]])
def test_telemetry_decoded_cursor_shape_is_governed(telemetry_case, after):
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.errors import ApiError
    s = telemetry_case
    s.accept([s.observation('one'), s.observation('two')])
    poll_deliveries(s.registry)
    value = json.loads(base64.urlsafe_b64decode(s.query(limit=1)['next_cursor'] + '=='))
    value['after'] = after
    cursor = base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    with pytest.raises(ApiError) as failure:
        s.query(limit=1, cursor=cursor)
    assert failure.value.code == 'INVALID_CURSOR' and failure.value.status == 422


def test_catalog_reference_publication_waits_for_exact_acknowledged_version(schema_case, monkeypatch):
    from lzo.auth.models import roles
    from lzo.catalog.service import CatalogService
    from lzo.catalog.seed import seed_examples
    from lzo.contracts import ResourceScope, RevisionRef
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization import delivery
    from lzo.catalog.repository import apply_catalog
    from lzo.semantic.projection import ProjectionService
    from lzo.semantic.fuseki import FusekiRepository
    from lzo.ontology.models import installations
    s = schema_case
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=['view', 'edit', 'catalog.manage']))
        uow.commit()
    s.grant('alice', 'editor', scope='farm_a')
    s.grant('admin', 'viewer', scope='farm_a')
    private_grant = s.grant('alice', 'editor', scope='farm_b')
    s.grant('admin', 'viewer', scope='farm_b')
    registry = StorageRegistry.from_settings(s.config)
    registry._engines['core'].dispose()
    registry._engines['core'] = s.app
    try:
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            public_reference = seed_examples(CatalogService(uow, registry), s.actor('alice'),
                ResourceScope(None, s.scopes['farm_a'], (), 'data'))[-1]
            uow.commit()
        import io
        from lzo.artifacts.service import ArtifactService
        from lzo.storage.blobs import get_blob_store
        from lzo.catalog.schemas import VersionDocument, CreateDocument
        with s.transaction() as uow:
            store = get_blob_store(s.config)
            retained = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_b'],
                store.put(io.BytesIO(b'Original synthetic private ontology reference'), 'text/turtle'),
                filename='private-reference.ttl', metadata={'synthetic': True})
            reference = CatalogService(uow, registry).create_document(s.actor('alice'), CreateDocument(
                scope=ResourceScope(None, s.scopes['farm_a'], (), 'data'), metadata=public_reference.metadata.model_copy(
                    update={'artifact_version_id': retained.version_id, 'title': 'Synthetic retained private reference'})), 'private-reference')
            uow.commit()
        with s.transaction() as uow:
            uow.ontology_settings = s.config
            newer = CatalogService(uow, registry).version_document(s.actor('alice'), reference.id,
                VersionDocument(expected_revision=1, metadata=reference.metadata.model_copy(update={
                    'summary': 'Newer metadata', 'artifact_version_id': public_reference.metadata.artifact_version_id})), 'newer')
            assert newer.revision.revision == 2
            uow.commit()
        draft = s.create({'classes': [{'iri': 'urn:ReferenceConsumer', 'label': 'Consumer'}],
            'reference_versions': [{'resource_id': str(pin.resource_id), 'revision': pin.revision,
                'event_id': str(pin.event_id)} for pin in (reference.revision, newer.revision)]})
        response = s.publish(draft, s.approve(draft))
        assert response.status_code == 202, response.text
        with s.transaction() as uow:
            row = uow.session.execute(text("SELECT r.resource_id,r.revision,r.event_id FROM revisions r WHERE r.event_type='schema.install'")).mappings().one()
            ref = RevisionRef(**row)
            dependency = uow.session.execute(text("SELECT payload->'dependency_revisions' FROM revisions WHERE event_id=:event"), {'event': ref.event_id}).scalar_one()
            assert {(p['revision'], p['event_id']) for p in dependency if p['resource_id'] == str(reference.id)} == {
                (pin.revision, str(pin.event_id)) for pin in (reference.revision, newer.revision)}
        def project():
            with s.transaction() as uow:
                uow.ontology_settings = s.config
                value = ProjectionService(uow, FusekiRepository(s.config.fuseki_url), actor=s.actor('alice')).project_revision(ref)
                uow.commit()
                return value
        with pytest.raises(delivery.DeliveriesPending):
            project()
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET status='failed',attempts=3 WHERE event_id=:event AND target='catalog'"), {'event': reference.revision.event_id})
            uow.commit()
        with pytest.raises(delivery.DeliveriesFailed):
            project()
        s.login()
        retry_endpoint = f'/api/v1/catalog/documents/{reference.id}/retry'
        retry_headers = s.headers(2)
        retry_body = {'expected_revision': 2, 'accepted_revision': {'resource_id': str(reference.id),
            'revision': 1, 'event_id': str(reference.revision.event_id)}}
        retried = s.client.post(retry_endpoint, headers=retry_headers, json=retry_body)
        assert retried.status_code == 202, retried.text
        assert s.client.post(retry_endpoint, headers=retry_headers, json=retry_body).json() == retried.json()
        # A real target commit is insufficient until the core acknowledges the exact event.
        original = delivery._acknowledge
        monkeypatch.setattr(delivery, '_acknowledge', lambda *args: (_ for _ in ()).throw(ConnectionError()))
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE target='catalog' AND status IN ('pending','retry')"))
            uow.commit()
        delivery.deliver_once(registry, 'catalog', apply_catalog)
        with pytest.raises(delivery.DeliveriesPending):
            project()
        monkeypatch.setattr(delivery, '_acknowledge', original)
        with s.transaction() as uow:
            uow.session.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE target='catalog'"))
            uow.commit()
        delivery.deliver_once(registry, 'catalog', apply_catalog)
        assert project().state == 'synced'
        with s.transaction() as uow:
            assert uow.session.execute(text('SELECT state FROM ontology_installations WHERE release_id=:id'), {'id': ref.resource_id}).scalar_one() == 'published'
        s.revoke(private_grant)
        s.login()
        assert s.client.get(f'/api/v1/catalog/documents/{reference.id}').status_code == 200
        assert s.client.get(f'/api/v1/catalog/documents/{reference.id}/versions/1').status_code == 404
        assert s.client.get(f'/api/v1/artifacts/{retained.version_id}/content').status_code == 404
        assert s.client.get(f'/api/v1/schema/releases/{ref.resource_id}').status_code == 404
        assert s.client.post(retry_endpoint, headers={**retry_headers, 'X-CSRF-Token': s.csrf}, json=retry_body).status_code == 404
        assert s.client.get(f"/api/v1/history/{retried.json()['audit']['resource_id']}/revisions/1").status_code == 404
    finally:
        registry._engines.pop('core')
        registry.close()


def test_catalog_terminal_retry_is_exact_audited_current_authorized_and_recovers(schema_case):
    from dataclasses import asdict
    from sqlalchemy import create_engine
    from lzo.auth.models import roles
    from lzo.catalog.repository import apply_catalog
    from lzo.synchronization.delivery import deliver_once
    s = schema_case
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=['view', 'edit', 'catalog.manage']))
        uow.commit()
    grant = s.grant('alice', 'editor', scope='farm_a')
    s.login()
    model = s.client.post('/api/v1/catalog/models', headers=s.headers(), json={
        'scope': {'resource_id': None, 'owner_scope_id': str(s.scopes['farm_a']), 'related_scope_ids': [], 'security_class': 'data'},
        'metadata': {'exact_model': 'Synthetic terminal retry', 'synthetic': True}})
    assert model.status_code == 201, model.text
    item = model.json()
    identifier, ref = item['id'], item['revision']
    registry = s.client.app.state.storage_registry
    registry._engines['core'].dispose()
    registry._engines['core'] = s.app
    original = registry._engines['catalog']
    unavailable = create_engine(original.url.set(port=1), connect_args={'connect_timeout': 1})
    registry._engines['catalog'] = unavailable
    try:
        for _ in range(3):
            with s.transaction() as uow:
                uow.session.execute(text("UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id"), {'id': UUID(ref['event_id'])})
                uow.commit()
            deliver_once(registry, 'catalog', apply_catalog)
    finally:
        registry._engines['catalog'] = original
        unavailable.dispose()
    with s.transaction() as uow:
        before = uow.session.execute(text('SELECT status,envelope_hash FROM store_deliveries WHERE event_id=:id'), {'id': UUID(ref['event_id'])}).mappings().one()
        assert before['status'] == 'failed'
    endpoint = f'/api/v1/catalog/models/{identifier}/retry'
    body = {'expected_revision': 1, 'accepted_revision': ref}
    s.login('nobody')
    assert s.client.post(endpoint, headers=s.headers(1), json=body).status_code == 404
    s.login()
    assert s.client.post(endpoint, headers=s.headers(2), json={**body, 'expected_revision': 2}).status_code == 409
    headers = s.headers(1)
    retried = s.client.post(endpoint, headers=headers, json=body)
    assert retried.status_code == 202, retried.text
    assert s.client.post(endpoint, headers=headers, json=body).json() == retried.json()
    audit = retried.json()['audit']
    with s.transaction() as uow:
        assert uow.session.execute(text('SELECT revision FROM resource_heads WHERE resource_id=:id'), {'id': UUID(identifier)}).scalar_one() == 1
        row = uow.session.execute(text('SELECT status,attempts,envelope_hash FROM store_deliveries WHERE event_id=:id'), {'id': UUID(ref['event_id'])}).mappings().one()
        assert row['status'] == 'retry' and row['attempts'] == 0 and row['envelope_hash'] == before['envelope_hash']
    deliver_once(registry, 'catalog', apply_catalog)
    assert s.client.get(f'/api/v1/catalog/models/{identifier}').json()['projection_status'] == 'current'
    assert s.client.post(endpoint, headers=s.headers(1), json=body).status_code == 409
    s.ids_by_name['retry_audit'] = UUID(audit['resource_id'])
    s.grant('alice', 'viewer', resource='retry_audit')
    s.revoke(grant)
    s.login()
    assert s.client.post(endpoint, headers={**headers, 'X-CSRF-Token': s.csrf}, json=body).status_code == 404
    assert s.client.get(f"/api/v1/history/{audit['resource_id']}/revisions/1").status_code == 404
    registry._engines.pop('core')  # TestClient must not close the fixture's borrowed engine.
