"""Catalog acceptance against independent PostgreSQL and governed blob versions."""
import io
from uuid import UUID, uuid4

import pytest
from sqlalchemy import insert, select, text, update


def test_catalog_api_contract_is_registered(settings):
    from lzo.main import create_app
    app = create_app(settings)
    try:
        paths = app.openapi()['paths']
        assert '/api/v1/catalog/models' in paths
        assert '/api/v1/catalog/documents/{id}/versions' in paths
    finally:
        app.state.storage_registry.close()


@pytest.fixture
def catalog_case(scope_scenario, settings, tmp_path):
    from lzo.auth.models import roles
    from lzo.catalog.service import CatalogService
    from lzo.catalog.schemas import ModelMetadata, CreateModel
    from lzo.contracts import ResourceScope
    from lzo.storage.registry import StorageRegistry
    from lzo.storage.migrate import migrate_domain
    s = scope_scenario
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(
            actions=['view', 'edit', 'catalog.manage', 'catalog.review']))
        uow.commit()
    s.writer = s.grant('alice', 'editor', scope='farm_a')
    s.grant('admin', 'editor', scope='farm_a')
    s.config = settings.model_copy(update={'file_root': tmp_path, 'blob_provider': 'local'})
    migrate_domain('catalog', s.config.catalog_migration_database_url, core_url=s.config.database_url)
    s.registry = StorageRegistry.from_settings(s.config)
    s.registry._engines['core'].dispose()
    s.registry._engines['core'] = s.app
    def service(uow):
        uow.ontology_settings = s.config
        return CatalogService(uow, s.registry)
    s.service = service
    s.scope = ResourceScope(None, s.scopes['farm_a'], (), 'data')
    def create(label='Synthetic camera', **changes):
        metadata = ModelMetadata(exact_model=label, synthetic=True, **changes)
        with s.transaction() as uow:
            value = service(uow).create_model(s.actor('alice'), CreateModel(scope=s.scope, metadata=metadata), str(uuid4()))
            uow.commit()
            return value
    s.create = create
    yield s
    s.registry.close()


def test_model_devices_have_distinct_physical_identity_and_pending_projection(catalog_case):
    from lzo.catalog.schemas import DeviceBinding
    from lzo.synchronization.delivery import poll_deliveries
    s = catalog_case
    s.grant('alice', 'editor', scope='farm_b')
    s.grant('alice', 'editor', resource='shared_device')
    created = s.create()
    assert created.metadata.manufacturer is None
    assert created.metadata.protocol_profiles is None
    with s.transaction() as uow:
        service = s.service(uow)
        for entity in ('object_a', 'shared_device'):
            service.bind_device(s.actor('alice'), created.id,
                DeviceBinding(entity_id=s.ids_by_name[entity], expected_revision=0), str(uuid4()))
        assert service.get(s.actor('alice'), created.id).projection_status == 'pending'
        uow.commit()
    poll_deliveries(s.registry)
    with s.transaction() as uow:
        value = s.service(uow).get(s.actor('alice'), created.id)
        assert value.projection_status == 'current'
        devices = s.service(uow).devices(s.actor('alice'), created.id)
        assert {v.entity_id for v in devices} == {s.ids_by_name['object_a'], s.ids_by_name['shared_device']}
        assert all(v.entity_id != v.model_id and v.model_id == created.id for v in devices)


def test_source_identity_replay_conflict_and_stale_version(catalog_case):
    from lzo.catalog.schemas import CreateModel, ModelMetadata, VersionModel
    from lzo.errors import ApiError
    s = catalog_case
    command = CreateModel(scope=s.scope, source_identity='urn:test:' + uuid4().hex,
        metadata=ModelMetadata(exact_model='Synthetic pump', synthetic=True))
    with s.transaction() as uow:
        service = s.service(uow)
        first = service.create_model(s.actor('alice'), command, 'create')
        assert service.create_model(s.actor('alice'), command, 'create').id == first.id
        assert service.create_model(s.actor('alice'), command, 'another-key').id == first.id
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='CATALOG_SOURCE_CONFLICT'):
            s.service(uow).create_model(s.actor('alice'), command.model_copy(update={
                'metadata': command.metadata.model_copy(update={'exact_model': 'Changed'})}), 'conflict')
    with s.transaction() as uow:
        service = s.service(uow)
        v2 = service.version_model(s.actor('alice'), first.id,
            VersionModel(expected_revision=1, metadata=command.metadata.model_copy(update={'firmware': 'test-2'})), 'version')
        assert v2.revision.revision == 2
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='REVISION_CONFLICT'):
            s.service(uow).version_model(s.actor('alice'), first.id,
                VersionModel(expected_revision=1, metadata=command.metadata), 'stale')


def test_critical_metadata_uses_existing_independent_approval(catalog_case):
    from lzo.catalog.schemas import VersionModel
    from lzo.governance.approvals import ApprovalService
    from lzo.errors import ApiError
    s = catalog_case
    first = s.create()
    command = VersionModel(expected_revision=1, metadata=first.metadata.model_copy(update={'state': 'retired'}))
    with s.transaction() as uow:
        service = s.service(uow)
        change = service.preview_version(s.actor('alice'), first.id, command)
        approval = ApprovalService(uow).submit(s.actor('alice'), change.kind, change.target_ids,
            change.change_digest, change.baseline, {'command': change.command})
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='SEPARATION_OF_DUTIES'):
            ApprovalService(uow).decide(s.actor('alice'), approval.id, 'approve', 'Self review')
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='APPROVAL_REQUIRED'):
            s.service(uow).version_model(s.actor('alice'), first.id, command, 'without-approval')
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'), approval.id, 'approve', 'Independent review')
        uow.commit()
    with s.transaction() as uow:
        value = s.service(uow).version_model(s.actor('alice'), first.id,
            command.model_copy(update={'approval_id': approval.id}), 'approved')
        assert value.metadata.state == 'retired'
        uow.commit()


def test_revocation_filters_before_cursor_and_global_is_explicit(catalog_case):
    from lzo.errors import ApiError
    s = catalog_case
    values = [s.create(str(n)) for n in range(3)]
    with s.transaction() as uow:
        service = s.service(uow)
        page = service.list(s.actor('alice'), 'model', limit=1)
        assert len(page.items) == 1 and page.next_cursor
        second = service.list(s.actor('alice'), 'model', limit=1, cursor=page.next_cursor)
        assert second.items[0].id != page.items[0].id
    s.revoke(s.writer)
    with s.transaction() as uow:
        service = s.service(uow)
        assert service.list(s.actor('alice'), 'model').items == []
        with pytest.raises(ApiError):
            service.get(s.actor('alice'), values[0].id, revision=1)
        with pytest.raises(ApiError, match='CATALOG_CURSOR_STALE'):
            service.list(s.actor('alice'), 'model', cursor=page.next_cursor)


def test_catalog_delivery_retains_late_versions_and_ack_loss(catalog_case, monkeypatch):
    from lzo.catalog.schemas import VersionModel
    from lzo.catalog.repository import apply_catalog, apply_catalog_history
    from lzo.synchronization import delivery
    s = catalog_case
    first = s.create()
    with s.transaction() as uow:
        second = s.service(uow).version_model(s.actor('alice'), first.id,
            VersionModel(expected_revision=1, metadata=first.metadata.model_copy(update={'firmware': 'v2'})), 'v2')
        uow.commit()
    with s.transaction() as uow:
        wires = uow.session.execute(text('SELECT envelope FROM store_deliveries WHERE entity_id=:id ORDER BY aggregate_revision DESC'), {'id': first.id}).scalars().all()
    from lzo.synchronization.contracts import SyncEnvelope
    delivery._apply(s.registry, SyncEnvelope.model_validate(wires[0]), apply_catalog)
    original = delivery._acknowledge
    monkeypatch.setattr(delivery, '_acknowledge', lambda *a: (_ for _ in ()).throw(ConnectionError()))
    delivery.deliver_once(s.registry, 'catalog', apply_catalog)
    monkeypatch.setattr(delivery, '_acknowledge', original)
    with s.transaction() as uow:
        uow.session.execute(text('UPDATE store_deliveries SET available_at=clock_timestamp() WHERE entity_id=:id'), {'id': first.id})
        uow.commit()
    delivery.poll_deliveries(s.registry)
    with s.registry.engine('catalog').connect() as conn:
        assert conn.execute(text('SELECT count(*) FROM catalog_versions WHERE resource_id=:id'), {'id': first.id}).scalar_one() == 2
        assert conn.execute(text('SELECT revision FROM catalog_heads WHERE resource_id=:id'), {'id': first.id}).scalar_one() == 2
    with s.transaction() as uow:
        assert s.service(uow).get(s.actor('alice'), first.id, revision=1).metadata.firmware is None


def test_catalog_equal_revision_duplicate_has_one_row_and_history_failure_rolls_back(catalog_case):
    from lzo.catalog.repository import apply_catalog, apply_catalog_history
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.synchronization import delivery
    s = catalog_case
    first = s.create()
    with s.transaction() as uow:
        wire = uow.session.execute(text('SELECT envelope FROM store_deliveries WHERE event_id=:id'),
            {'id': first.revision.event_id}).scalar_one()
    item = SyncEnvelope.model_validate(wire)
    delivery._apply(s.registry, item, apply_catalog)
    duplicate = item.model_copy(update={'event_id': uuid4()})
    receipt = delivery._apply(s.registry, duplicate, apply_catalog)
    assert receipt.status == 'superseded'
    old = item.model_copy(update={'event_id': uuid4(), 'entity_id': uuid4(), 'aggregate_revision': 1})
    newer = old.model_copy(update={'event_id': uuid4(), 'aggregate_revision': 2})
    delivery._apply(s.registry, newer, apply_catalog)
    def failed_history(connection, envelope):
        apply_catalog_history(connection, envelope)
        raise RuntimeError('Synthetic catalog history rollback')
    delivery.register_delivery_handler('catalog', apply_catalog, history_handler=failed_history)
    try:
        with pytest.raises(RuntimeError):
            delivery._apply(s.registry, old, apply_catalog)
        with s.registry.engine('catalog').connect() as connection:
            assert connection.execute(text('SELECT count(*) FROM catalog_versions WHERE resource_id=:id'), {'id': first.id}).scalar_one() == 1
            assert connection.execute(text('SELECT count(*) FROM catalog_versions WHERE event_id=:id'), {'id': old.event_id}).scalar_one() == 0
            assert connection.execute(text('SELECT count(*) FROM store_inbox WHERE event_id=:id'), {'id': old.event_id}).scalar_one() == 0
            assert connection.execute(text('SELECT revision FROM catalog_heads WHERE resource_id=:id'), {'id': old.entity_id}).scalar_one() == 2
    finally:
        delivery.register_delivery_handler('catalog', apply_catalog, history_handler=apply_catalog_history)


def test_document_versions_pin_real_blob_hash_and_current_permissions(catalog_case):
    from lzo.catalog.schemas import CreateDocument, DocumentMetadata, VersionDocument
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.errors import ApiError
    s = catalog_case
    store = get_blob_store(s.config)
    versions = []
    for body in (b'Original authored synthetic reference v1', b'Independent revised synthetic reference v2'):
        with s.transaction() as uow:
            blob = store.put(io.BytesIO(body), 'text/plain')
            artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
                blob, filename='original-example.txt', metadata={'synthetic': True})
            versions.append(artifact.version_id)
            uow.commit()
    metadata = DocumentMetadata(title='Original example', document_type='reference', artifact_version_id=versions[0],
        source_uri='https://www.w3.org/TR/vocab-ssn/', source_version='2017-10-19', license='Original authored example; no manual copied', summary='Manual summary')
    with s.transaction() as uow:
        service = s.service(uow)
        first = service.create_document(s.actor('alice'), CreateDocument(scope=s.scope, metadata=metadata), 'doc')
        uow.commit()
    with s.transaction() as uow:
        second = s.service(uow).version_document(s.actor('alice'), first.id,
            VersionDocument(expected_revision=1, metadata=metadata.model_copy(update={'artifact_version_id': versions[1]})), 'doc-v2')
        assert first.sha256 != second.sha256
        assert second.revision.revision == 2
        assert s.service(uow).get(s.actor('alice'), first.id, revision=1).metadata.artifact_version_id == versions[0]
        uow.commit()
    s.revoke(s.writer)
    with s.transaction() as uow:
        assert s.service(uow).list(s.actor('alice'), 'document', text='Original').items == []
        with pytest.raises(ApiError):
            s.service(uow).get(s.actor('alice'), first.id, revision=1)


def test_verified_capabilities_reject_ordinary_evidence_shaped_upload(catalog_case):
    from lzo.catalog.schemas import Capability, ModelMetadata, CreateModel
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.errors import ApiError
    s = catalog_case
    store = get_blob_store(s.config)
    with s.transaction() as uow:
        blob = store.put(io.BytesIO(b'Uncontrolled'), 'text/plain')
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'], blob,
            filename='forged.txt', metadata={'evidence_policy': 'evidence-v1', 'high_risk': True})
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            s.service(uow).create_model(s.actor('alice'), CreateModel(scope=s.scope, metadata=ModelMetadata(
                exact_model='Synthetic drone', synthetic=True, capabilities=(Capability(name='flight', state='verified',
                    evidence_version_ids=(artifact.version_id,)),))), 'forged')


@pytest.mark.parametrize('provider', ['local', 's3'])
def test_positive_controlled_evidence_is_independent_and_reference_draft_is_not_publication(catalog_case, provider, tmp_path):
    from pathlib import Path
    from dotenv import dotenv_values
    from lzo.auth.models import roles
    from lzo.catalog.schemas import Capability, VersionModel, DocumentMetadata, CreateDocument
    from lzo.ingestion.schemas import EvidenceCreate
    from lzo.ingestion.evidence import EvidenceService
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.governance.approvals import ApprovalService
    from lzo.ontology.drafts import document_refs
    from lzo.ontology.schemas import SchemaDocument
    from lzo.errors import ApiError
    s = catalog_case
    if provider == 's3':
        env = dotenv_values('.runtime/object-store/services.env')
        assert env, 'Actual owned S3 is required'
        s.config = s.config.model_copy(update={'blob_provider': 's3', 's3_endpoint': env['LZO_S3_ENDPOINT'],
            's3_bucket': env['LZO_S3_BUCKET'], 's3_access_key_file': Path(env['LZO_S3_ACCESS_KEY_FILE']),
            's3_secret_key_file': Path(env['LZO_S3_SECRET_KEY_FILE']), 'blob_staging_path': tmp_path/'staging'})
    with s.transaction() as uow:
        uow.session.execute(update(roles).where(roles.c.id == s.roles['editor']).values(actions=[
            'view', 'edit', 'catalog.manage', 'catalog.review', 'evidence.manage', 'evidence.read']))
        uow.commit()
    first = s.create()
    store = get_blob_store(s.config)
    with s.transaction() as uow:
        evidence = EvidenceService(uow, store, s.config).create(uow, s.actor('alice'), io.BytesIO(b'Original authored synthetic verification'),
            'text/plain', EvidenceCreate(scope=s.scope, target_refs=(), title='Synthetic bench observation', source_id=None,
                locator=None, data_nature='imported_unverified', replaces_id=None))
        uow.commit()
    with s.transaction() as uow:
        ArtifactService(uow, store, s.config).review(s.actor('admin'), evidence.id, 'accepted', 'Independent bench review', 1)
        uow.commit()
    metadata = first.metadata.model_copy(update={'state': 'verified', 'capabilities': (
        Capability(name='image capture', state='verified', evidence_version_ids=(evidence.id,)),)})
    command = VersionModel(expected_revision=1, metadata=metadata)
    with s.transaction() as uow:
        change = s.service(uow).preview_version(s.actor('alice'), first.id, command)
        approval = ApprovalService(uow).submit(s.actor('alice'), change.kind, change.target_ids,
            change.change_digest, change.baseline, {'command': change.command})
        uow.commit()
    with s.transaction() as uow:
        ApprovalService(uow).decide(s.actor('admin'), approval.id, 'approve', 'Catalog capability independently checked')
        uow.commit()
    with s.transaction() as uow:
        verified = s.service(uow).version_model(s.actor('alice'), first.id, command.model_copy(update={'approval_id': approval.id}), 'verified')
        assert verified.metadata.capabilities[0].state == 'verified'
        assert s.service(uow).get(s.actor('alice'), first.id, revision=1).metadata.capabilities == ()
        uow.commit()
    with s.transaction() as uow:
        replacement = EvidenceService(uow, store, s.config).create(uow, s.actor('alice'), io.BytesIO(b'Independent evidence revision'),
            'text/plain', EvidenceCreate(scope=s.scope, target_refs=(), title='Second evidence', source_id=None, locator=None,
                data_nature='imported_unverified', replaces_id=evidence.id), expected_revision=2)
        assert replacement.id != evidence.id
        assert s.service(uow).get(s.actor('alice'), first.id).revision.revision == 2
        assert s.service(uow).get(s.actor('alice'), first.id).metadata.capabilities[0].evidence_version_ids == (evidence.id,)
        uow.commit()
    with s.transaction() as uow:
        reference = s.service(uow).create_document(s.actor('alice'), CreateDocument(scope=s.scope, metadata=DocumentMetadata(
            title='Original SSN reading notes', document_type='ontology', artifact_version_id=evidence.id,
            source_uri='https://www.w3.org/TR/2017/REC-vocab-ssn-20171019/', source_version='2017-10-19',
            license='Original authored synthetic notes; external reference is W3C licensed', model_ids=(first.id,),
            term_iris=('http://www.w3.org/ns/sosa/Sensor',))), 'ontology-ref')
        refs = document_refs(uow, s.actor('alice'), SchemaDocument(reference_versions=(reference.revision,)), None)
        assert reference.id in refs and evidence.id in refs
        assert uow.session.execute(text('SELECT count(*) FROM ontology_releases')).scalar_one() == 0
        with pytest.raises(ApiError, match='INVALID_REFERENCE_PIN'):
            from lzo.contracts import RevisionRef
            document_refs(uow, s.actor('alice'), SchemaDocument(reference_versions=(
                RevisionRef(reference.id, 1, uuid4()),)), None)
        uow.commit()
    with store.open(evidence.blob) as stream:
        assert stream.read() == b'Original authored synthetic verification'


def test_catalog_outage_recovery_and_failed_status(catalog_case):
    from sqlalchemy import create_engine
    from lzo.synchronization.delivery import poll_deliveries
    s = catalog_case
    first = s.create()
    original = s.registry._engines['catalog']
    unavailable = create_engine(original.url.set(port=1), connect_args={'connect_timeout': 1})
    s.registry._engines['catalog'] = unavailable
    try:
        poll_deliveries(s.registry)
        with s.transaction() as uow:
            assert s.service(uow).get(s.actor('alice'), first.id).projection_status == 'pending'
            assert uow.session.execute(text('SELECT status FROM store_deliveries WHERE event_id=:id'),
                {'id': first.revision.event_id}).scalar_one() == 'retry'
    finally:
        s.registry._engines['catalog'] = original
        unavailable.dispose()
    with s.transaction() as uow:
        uow.session.execute(text('UPDATE store_deliveries SET available_at=clock_timestamp() WHERE event_id=:id'), {'id': first.revision.event_id})
        uow.commit()
    poll_deliveries(s.registry)
    with s.transaction() as uow:
        assert s.service(uow).get(s.actor('alice'), first.id).projection_status == 'current'
    s.registry._engines['catalog'] = unavailable
    try:
        with s.transaction() as uow:
            assert s.service(uow).get(s.actor('alice'), first.id).projection_status == 'unavailable'
    finally:
        s.registry._engines['catalog'] = original
    with s.transaction() as uow:
        uow.session.execute(text("UPDATE store_deliveries SET status='failed' WHERE event_id=:id"), {'id': first.revision.event_id})
        assert s.service(uow).get(s.actor('alice'), first.id).projection_status == 'failed'


def test_global_reference_visibility_needs_explicit_global_grant(catalog_case):
    from lzo.auth.models import grants
    from lzo.catalog.schemas import CreateModel, ModelMetadata
    from lzo.contracts import ResourceScope
    from lzo.errors import ApiError
    s = catalog_case
    global_command = CreateModel(scope=ResourceScope(None, None, (), 'data'),
        metadata=ModelMetadata(exact_model='Synthetic shared reference', synthetic=True))
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            s.service(uow).create_model(s.actor('alice'), global_command, 'no-global')
    with s.transaction() as uow:
        uow.session.execute(insert(grants).values(id=uuid4(), user_id=s.users['alice'], role_id=s.roles['editor'],
            security_class='data', global_scope=True, revision=1))
        uow.commit()
    with s.transaction() as uow:
        global_ref = s.service(uow).create_model(s.actor('alice'), global_command, 'global')
        uow.commit()
    with s.transaction() as uow:
        assert s.service(uow).get(s.actor('alice'), global_ref.id).id == global_ref.id
        assert s.service(uow).list(s.actor('admin'), 'model').items == []


def test_synthetic_seed_is_repeatable_and_never_installs_equipment(catalog_case):
    from lzo.catalog.seed import seed_examples
    s = catalog_case
    with s.transaction() as uow:
        result = seed_examples(s.service(uow), s.actor('alice'), s.scope)
        again = seed_examples(s.service(uow), s.actor('alice'), s.scope)
        assert [r.id for r in result] == [r.id for r in again]
        assert len(result) == 4
        assert {r.metadata.exact_model for r in result if r.kind == 'model'} == {
            'SYNTHETIC camera example', 'SYNTHETIC pump example', 'SYNTHETIC drone example'}
        assert all(r.metadata.manufacturer is None and r.metadata.protocol_profiles is None for r in result if r.kind == 'model')
        assert uow.session.execute(text('SELECT count(*) FROM catalog_device_models')).scalar_one() == 0


def test_generic_artifact_write_cannot_replace_catalog_governed_head(catalog_case):
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.errors import ApiError
    s = catalog_case
    first = s.create()
    store = get_blob_store(s.config)
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='CATALOG_COMMAND_REQUIRED'):
            ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), first.id,
                store.put(io.BytesIO(b'arbitrary'), 'text/plain'), filename='bypass.txt', metadata={})


def test_http_catalog_versions_and_preconditions_use_real_session(catalog_case):
    from fastapi.testclient import TestClient
    from lzo.auth.session import SessionService, SESSION_COOKIE, digest
    from lzo.auth.models import sessions
    from lzo.main import create_app
    from lzo.catalog.schemas import CreateModel, ModelMetadata
    s = catalog_case
    app = create_app(s.config)
    app.state.engine.dispose()
    app.state.storage_registry.close()
    app.state.engine = s.app
    app.state.storage_registry = s.registry
    with s.transaction() as uow:
        cookie = SessionService(uow, s.config).create(s.users['alice'], {'access_token': 'synthetic'})
        csrf = uow.session.execute(select(sessions.c.csrf_token).where(sessions.c.id_hash == digest(cookie))).scalar_one()
        uow.commit()
    headers = {'Cookie': f'{SESSION_COOKIE}={cookie}', 'X-CSRF-Token': csrf, 'Origin': s.config.public_origin,
        'Idempotency-Key': 'http-create', 'If-Match': '"1"'}
    with TestClient(app, base_url='https://testserver') as client:
        body = CreateModel(scope=s.scope, metadata=ModelMetadata(exact_model='HTTP synthetic', synthetic=True)).model_dump(mode='json')
        created = client.post('/api/v1/catalog/models', json=body, headers=headers)
        assert created.status_code == 201, created.text
        assert client.post('/api/v1/catalog/models', json=body, headers=headers).json()['id'] == created.json()['id']
        identifier = created.json()['id']
        version_body = {'expected_revision': 1, 'metadata': {**body['metadata'], 'firmware': 'test-v2'}}
        url = f'/api/v1/catalog/models/{identifier}/versions'
        assert client.post(url, json=version_body, headers={**headers, 'If-Match': '"2"', 'Idempotency-Key': 'bad-revision'}).status_code == 409
        assert client.post(url, json=version_body, headers={**headers, 'Idempotency-Key': 'http-version'}).status_code == 201
        assert client.get(f'{url}/1', headers=headers).json()['metadata']['firmware'] is None
        versions = client.get(url, headers=headers)
        assert versions.status_code == 200, versions.text
        assert [v['revision']['revision'] for v in versions.json()['items']] == [1, 2]
        assert client.get('/api/v1/catalog/models?text=HTTP&limit=1', headers=headers).json()['items'][0]['id'] == identifier
        assert client.get('/api/v1/catalog/models?cursor=malformed', headers=headers).status_code == 422
        preview_body = {'expected_revision': 2, 'metadata': {**version_body['metadata'], 'state': 'retired'}}
        preview = client.post(f'/api/v1/catalog/models/{identifier}/preview', json=preview_body,
            headers={**headers, 'If-Match': '"2"'})
        assert preview.status_code == 200, preview.text
        assert len(preview.json()['change_digest']) == 64 and preview.json()['risk'] == 'high'


def test_linked_permission_revocation_hides_stale_projection_and_fills_page(catalog_case):
    from lzo.catalog.schemas import CreateModel, ModelMetadata, VersionModel
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.synchronization.delivery import poll_deliveries
    from lzo.governance.history import HistoryService
    from lzo.errors import ApiError
    s = catalog_case
    grant = s.grant('alice', 'editor', scope='farm_b')
    store = get_blob_store(s.config)
    with s.transaction() as uow:
        artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_b'],
            store.put(io.BytesIO(b'Private region attachment'), 'text/plain'), filename='private.txt', metadata={})
        uow.commit()
    values = [s.create('Visible ' + str(n)) for n in range(3)]
    hidden = sorted(values, key=lambda item: item.id)[0]
    with s.transaction() as uow:
        s.service(uow).version_model(s.actor('alice'), hidden.id, VersionModel(expected_revision=1,
            metadata=hidden.metadata.model_copy(update={'attachment_version_ids': (artifact.version_id,)})), 'regional-link')
        uow.commit()
    poll_deliveries(s.registry)
    s.revoke(grant)
    with s.transaction() as uow:
        service = s.service(uow)
        page = service.list(s.actor('alice'), 'model', limit=2)
        assert len(page.items) == 2 and hidden.id not in {item.id for item in page.items}
        assert page.next_cursor is None
        with pytest.raises(ApiError):
            service.get(s.actor('alice'), hidden.id)
        with pytest.raises(ApiError):
            HistoryService(uow).get(s.actor('alice'), hidden.id, 2)


def test_reference_pin_enters_real_schema_draft_and_independent_release_review(catalog_case):
    from lzo.auth.models import roles, grants
    from lzo.catalog.seed import seed_examples
    from lzo.ontology.drafts import create_draft
    from lzo.ontology.releases import descriptor, submit_draft
    from lzo.ontology.schemas import SchemaDocument, CreateDraftRequest, ClassDef
    from lzo.governance.approvals import ApprovalService
    from lzo.errors import ApiError
    s = catalog_case
    with s.transaction() as uow:
        for name, actions in (('alice', ['view', 'schema.edit', 'schema.publish']), ('admin', ['view', 'schema.review'])):
            role = uuid4()
            uow.session.execute(insert(roles).values(id=role, name=name+'-catalog-schema', actions=actions, revision=1))
            uow.session.execute(insert(grants).values(id=uuid4(), user_id=s.users[name], role_id=role,
                security_class='schema', global_scope=True, revision=1))
        reference = seed_examples(s.service(uow), s.actor('alice'), s.scope)[-1]
        draft = create_draft(uow, s.actor('alice'), CreateDraftRequest(document=SchemaDocument(
            classes=(ClassDef(iri='urn:lzo:synthetic:CameraExample', label='Synthetic camera'),),
            reference_versions=(reference.revision,))))
        change = descriptor(uow, s.actor('alice'), draft.id)
        approval = submit_draft(uow, s.actor('alice'), draft.id, change.change_digest, 1)
        assert approval.risk == 'high' and reference.id in approval.target_ids
        assert uow.session.execute(text('SELECT active_release_id FROM ontology_catalog')).scalar_one() is None
        uow.commit()
    with s.transaction() as uow:
        with pytest.raises(ApiError, match='SEPARATION_OF_DUTIES'):
            ApprovalService(uow).decide(s.actor('alice'), approval.id, 'approve', 'Self review')
    with s.transaction() as uow:
        approved = ApprovalService(uow).decide(s.actor('admin'), approval.id, 'approve', 'Reviewed original reference provenance')
        assert approved.status == 'approved'
        assert uow.session.execute(text('SELECT count(*) FROM ontology_releases')).scalar_one() == 0
        uow.commit()


def test_document_entity_iri_resolves_registered_entity_and_current_rights(catalog_case):
    from lzo.catalog.seed import seed_examples
    from lzo.catalog.schemas import VersionDocument
    from lzo.semantic.facts import object_iri
    from lzo.errors import ApiError
    s = catalog_case
    regional = s.grant('alice', 'editor', scope='farm_b')
    identifier = s.ids_by_name['object_b']
    with s.transaction() as uow:
        service = s.service(uow)
        reference = seed_examples(service, s.actor('alice'), s.scope)[-1]
        metadata = type(reference.metadata).model_validate({**reference.metadata.model_dump(mode='json'),
            'entity_iris': [object_iri(identifier)]})
        value = service.version_document(s.actor('alice'), reference.id,
            VersionDocument(expected_revision=1, metadata=metadata), 'entity-iri')
        assert value.metadata.entity_iris == (object_iri(identifier),)
        uow.commit()
    s.revoke(regional)
    with s.transaction() as uow:
        with pytest.raises(ApiError):
            s.service(uow).get(s.actor('alice'), reference.id)
