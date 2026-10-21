"""U05 map routes using real F02/F03/F04 and disposable PostgreSQL schemas."""
import io
import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select

FIXTURES = Path(__file__).resolve().parents[3] / 'tests/fixtures/maps'
BASE = '/api/v1/maps/resources'


@pytest.fixture
def map_http(scope_scenario, settings, tmp_path):
    from fastapi.testclient import TestClient
    from lzo.artifacts.service import ArtifactService
    from lzo.artifacts.store import ArtifactStore
    from lzo.auth.models import sessions
    from lzo.auth.session import SESSION_COOKIE, SessionService, digest
    from lzo.main import create_app

    s = scope_scenario
    from sqlalchemy import text
    with s.app.connect() as connection:
        schema = connection.execute(text('SELECT current_schema()')).scalar_one()
    marker = tmp_path / 'map-fixture-ownership.json'
    ownership = {'schema': schema, 'fixture_path': str(tmp_path),
        'scopes': {key:str(value) for key,value in s.scopes.items()},
        'resources': {key:str(value) for key,value in s.ids_by_name.items()}, 'artifact_versions': []}
    marker.write_text(json.dumps(ownership, indent=2) + '\n', encoding='utf8')
    s.grant('alice', 'editor', scope='farm_a')
    s.grant('admin', 'editor', scope='farm_a')
    s.grant('admin', 'editor', scope='farm_b')
    config = settings.model_copy(update={'file_root': tmp_path / 'private', 'list_page_size': 1})
    s.store = ArtifactStore(config.file_root / 'artifacts', max_upload_bytes=config.artifact_max_upload_bytes)
    app = create_app(config)
    app.state.engine.dispose()
    app.state.engine = s.app
    cookies = {}
    with s.transaction() as uow:
        service = SessionService(uow, config)
        for name in ('alice', 'admin', 'nobody'):
            cookie = service.create(s.users[name], {'access_token': 'synthetic'})
            csrf = uow.session.execute(select(sessions.c.csrf_token).where(sessions.c.id_hash == digest(cookie))).scalar_one()
            cookies[name] = (cookie, csrf)
        uow.commit()
    def headers(name='alice', revision=0, key=None):
        cookie, csrf = cookies[name]
        return {'Cookie': f'{SESSION_COOKIE}={cookie}', 'X-CSRF-Token': csrf,
            'Origin': config.public_origin, 'Idempotency-Key': key or uuid4().hex,
            'If-Match': f'"{revision}"'}
    def upload(path, owner='object_a', name='admin', data=None):
        blob = s.store.put(io.BytesIO(data if data is not None else (FIXTURES / path).read_bytes()), 'application/octet-stream')
        with s.transaction() as uow:
            ref = ArtifactService(uow, s.store, config).register_upload(s.actor(name),
                s.ids_by_name[owner], blob, filename=Path(path).name, metadata={'data_nature': 'synthetic'})
            uow.commit()
        ownership['artifact_versions'].append({'id':str(ref.version_id),
            'owner':str(s.ids_by_name[owner]), 'sha256':blob.sha256})
        marker.write_text(json.dumps(ownership, indent=2) + '\n', encoding='utf8')
        return str(ref.version_id)
    def payload(kind='pmtiles', tile_kind='raster', style=False, owner='object_a'):
        body = {'title': '梁子湖合成底图', 'kind': kind, 'tile_kind': tile_kind,
            'artifact_version_id': upload(f'synthetic-{tile_kind}.{kind}', owner),
            'bbox': [114.48, 30.18, 114.52, 30.22], 'min_zoom': 10, 'max_zoom': 14,
            'attribution': 'Original synthetic fixture', 'license': 'CC0-1.0',
            'asset_versions': {}, 'owner_scope_id': str(s.scopes['farm_a'])}
        if style:
            body['style_version_id'] = upload(f'style-{tile_kind}.json')
            for path in ('sprite/sprite.json', 'sprite/sprite.png', 'sprite/sprite@2x.json', 'sprite/sprite@2x.png',
                         'glyphs/Noto Sans Regular/19968-20223.pbf'):
                body['asset_versions'][path] = upload('assets/' + path)
        return body
    s.headers, s.upload_map_artifact, s.payload = headers, upload, payload
    s.map_settings = config
    s.map_fixture_dir = tmp_path
    with TestClient(app, base_url='https://testserver') as client:
        s.http = client
        yield s


def create(s, **kwargs):
    result = s.http.post(BASE, json=s.payload(**kwargs), headers=s.headers())
    assert result.status_code == 201, result.text
    return result.json()


def test_create_patch_idempotency_and_real_revision_audit(map_http):
    from sqlalchemy import func
    from lzo.governance.models import audit_events, revisions, outbox_events
    s = map_http
    body, headers = s.payload(), s.headers(key='map-create')
    missing = s.http.post(BASE, json=body, headers={k:v for k,v in headers.items() if k != 'Idempotency-Key'})
    assert missing.status_code == 428
    response = s.http.post(BASE, json=body, headers=headers)
    assert response.status_code == 201, response.text
    resource = response.json()
    assert resource['revision'] == 1 and resource['validation_status'] == 'valid'
    assert s.http.post(BASE, json=body, headers=headers).json() == resource
    url = f"{BASE}/{resource['id']}"
    assert s.http.patch(url, json={'title':'x'}, headers={k:v for k,v in s.headers().items() if k != 'If-Match'}).status_code == 428
    updated = s.http.patch(url, json={'title':'梁子湖更新'}, headers=s.headers(revision=1))
    assert updated.status_code == 200, updated.text
    assert updated.json()['revision'] == 2
    assert s.http.patch(url, json={'title':'stale'}, headers=s.headers(revision=1)).status_code == 409
    assert s.http.patch(url, json={'id':str(uuid4())}, headers=s.headers(revision=2)).status_code == 422
    with s.transaction() as uow:
        events = uow.session.execute(select(revisions.c.event_id).where(revisions.c.resource_id == resource['id'])).scalars().all()
        assert len(events) == 2
        assert uow.session.execute(select(func.count()).select_from(audit_events).where(audit_events.c.event_id.in_(events))).scalar_one() == 2
        assert uow.session.execute(select(func.count()).select_from(outbox_events).where(outbox_events.c.event_id.in_(events))).scalar_one() == 2


def test_accessible_map_does_not_grant_hidden_identical_package_or_list_counts(map_http):
    s = map_http
    visible = create(s)
    hidden_body = s.payload(owner='object_b')
    hidden = s.http.post(BASE, json=hidden_body, headers=s.headers('admin'))
    assert hidden.status_code == 201, hidden.text
    hidden_id = hidden.json()['id']
    assert s.http.get(f'{BASE}/{hidden_id}', headers=s.headers()).status_code == 404
    listing = s.http.get(BASE, headers=s.headers()).json()
    assert [x['id'] for x in listing['items']] == [visible['id']]
    assert listing['next_cursor'] is None
    assert s.http.post(BASE, json=hidden_body, headers=s.headers()).status_code == 404
    assert s.http.get(BASE, headers=s.headers('nobody')).json() == {'items': [], 'next_cursor': None}
    for suffix in ('', '/style.json', '/archive', '/tiles/12/3350/1687'):
        assert s.http.get(f'{BASE}/{visible["id"]}{suffix}', headers=s.headers('nobody')).status_code == 404


def test_all_asset_references_remain_current_and_private(map_http):
    s = map_http
    resource = create(s, tile_kind='mvt', style=True)
    url = f'{BASE}/{resource["id"]}'
    style = s.http.get(url + '/style.json', headers=s.headers())
    assert style.status_code == 200, style.text
    assert style.json()['glyphs'].startswith(s.map_settings.public_origin + '/api/v1/maps/resources/')
    assert style.headers['Cache-Control'] == 'private,no-store'
    assert 'file:' not in style.text and 'secret' not in style.text
    asset = s.http.get(url + '/assets/sprite/sprite.png', headers=s.headers())
    assert asset.status_code == 200 and asset.content.startswith(b'\x89PNG')
    assert asset.headers['Cache-Control'] == 'private,no-store'
    hidden_asset = s.upload_map_artifact('assets/sprite/sprite.png', owner='object_b')
    changed = dict(resource['asset_versions'])
    changed['sprite/sprite.png'] = hidden_asset
    patched = s.http.patch(url, json={'asset_versions':changed}, headers=s.headers('admin', revision=1))
    assert patched.status_code == 200, patched.text
    for suffix in ('', '/style.json', '/archive', '/assets/sprite/sprite.png', '/tiles/12/3350/1687'):
        assert s.http.get(url + suffix, headers=s.headers()).status_code == 404
    assert s.http.get(BASE, headers=s.headers()).json()['items'] == []


def test_styles_manifest_and_online_urls_fail_closed(map_http):
    s = map_http
    for path in ('../secret.pbf', 'file:///private', 'https://evil/a', 'sprite/%2e%2e/x.png'):
        body = s.payload()
        body['asset_versions'] = {path: body['artifact_version_id']}
        assert s.http.post(BASE, json=body, headers=s.headers()).status_code == 422
    body = s.payload()
    body.update(kind='online', artifact_version_id=None, online_url_template='https://evil/{z}/{x}/{y}?secret=token')
    assert s.http.post(BASE, json=body, headers=s.headers()).status_code == 422
    body = s.payload()
    body['style_version_id'] = s.upload_map_artifact('evil.json', data=json.dumps({'version':8,'sources':{},'layers':[], 'glyphs':'https://evil/{range}.pbf'}).encode())
    assert s.http.post(BASE, json=body, headers=s.headers()).status_code == 422


def test_artifact_revoke_hides_map_while_map_grant_still_active(map_http):
    s = map_http
    resource = create(s, style=True)
    s.ids_by_name['map'] = __import__('uuid').UUID(resource['id'])
    s.grant('nobody', 'viewer', resource='map')
    artifact_grant = s.grant('nobody', 'viewer', resource='object_a')
    url = f'{BASE}/{resource["id"]}'
    assert s.http.get(url + '/archive', headers=s.headers('nobody')).status_code == 200
    s.revoke(artifact_grant)
    for suffix in ('', '/archive', '/style.json', '/assets/sprite/sprite.png', '/tiles/12/3350/1687'):
        assert s.http.get(url + suffix, headers=s.headers('nobody')).status_code == 404
    assert s.http.get(BASE, headers=s.headers('nobody')).json() == {'items': [], 'next_cursor': None}


def test_filtered_pagination_and_scope_writes(map_http):
    s = map_http
    visible = [create(s)['id'] for _ in range(3)]
    for _ in range(2):
        response = s.http.post(BASE, json=s.payload(owner='object_b'), headers=s.headers('admin'))
        assert response.status_code == 201
    cursor, collected = None, []
    for _ in range(3):
        page = s.http.get(BASE, params={'cursor':cursor} if cursor else {}, headers=s.headers()).json()
        assert len(page['items']) == 1
        collected.extend(item['id'] for item in page['items'])
        cursor = page['next_cursor']
    assert cursor is None and collected == sorted(visible)
    body = s.payload()
    body['owner_scope_id'] = str(s.scopes['farm_b'])
    assert s.http.post(BASE, json=body, headers=s.headers()).status_code == 403
    url = f'{BASE}/{visible[0]}'
    assert s.http.patch(url, json={'owner_scope_id':str(s.scopes['farm_b'])}, headers=s.headers(revision=1)).status_code == 403
    assert s.http.get(url, headers=s.headers()).json()['revision'] == 1


def test_registered_online_adapter_is_fixed_private_and_authorized(map_http):
    from lzo.maps.online import register_online_template
    s = map_http
    public = f'https://{uuid4().hex}.example.invalid/tiles/{{z}}/{{x}}/{{y}}'
    # A real fixed synthetic provider for this test; it has no forwarding input.
    def adapter(z, x, y):
        if (z, x, y) == (12, 3350, 1687):
            return (FIXTURES / 'assets/sprite/sprite.png').read_bytes()
        raise RuntimeError('private-provider-credential-must-never-appear')
    register_online_template(public, adapter)
    body = s.payload()
    body.update(kind='online', artifact_version_id=None, online_url_template=public)
    result = s.http.post(BASE, json=body, headers=s.headers())
    assert result.status_code == 201, result.text
    url = f'{BASE}/{result.json()["id"]}'
    style = s.http.get(url + '/style.json', headers=s.headers())
    assert style.json()['sources']['basemap']['tiles'] == [s.map_settings.public_origin + url + '/tiles/{z}/{x}/{y}']
    tile = s.http.get(url + '/tiles/12/3350/1687', headers=s.headers())
    assert tile.status_code == 200 and tile.headers['Content-Type'] == 'image/png'
    assert s.http.get(url + '/tiles/12/3350/1687', headers=s.headers('nobody')).status_code == 404
    failed = s.http.get(url + '/tiles/10/837/421', headers=s.headers())
    assert failed.status_code == 503 and 'credential-must' not in failed.text
    assert s.http.get(url + '/archive', headers=s.headers()).status_code == 422


def test_all_f04_inherited_references_protect_map_reads_and_history(map_http):
    from sqlalchemy import insert
    from lzo.governance.history import HistoryService, revision_visibility_dependencies
    from lzo.governance.models import resource_heads
    from lzo.governance.revisions import append_revision
    from lzo.errors import ApiError
    s = map_http
    # A real trusted producer dependency on the artifact owner's next revision;
    # F04 then carries it into its actual registered upload event.
    with s.transaction() as uow:
        revision = uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id == s.ids_by_name['object_a'])).scalar_one()
        ref = append_revision(uow, s.actor('admin'), s.ids_by_name['object_a'], revision, {'reference':str(s.ids_by_name['object_b'])}, 'object.fixture_reference')
        uow.session.execute(insert(revision_visibility_dependencies).values(event_id=ref.event_id, resource_id=s.ids_by_name['object_b']))
        uow.commit()
    body = s.payload()
    response = s.http.post(BASE, json=body, headers=s.headers('admin'))
    assert response.status_code == 201, response.text
    from uuid import UUID
    resource_id = UUID(response.json()['id'])
    url = f'{BASE}/{resource_id}'
    assert s.http.get(url + '/archive', headers=s.headers()).status_code == 404
    assert s.http.get(BASE, headers=s.headers()).json()['items'] == []
    with s.transaction() as uow, pytest.raises(ApiError) as error:
        HistoryService(uow).get(s.actor('alice'), resource_id, 1)
    assert error.value.status == 404
    grant = s.grant('alice', 'viewer', resource='object_b')
    assert s.http.get(url + '/archive', headers=s.headers()).status_code == 200
    s.revoke(grant)
    assert s.http.get(url + '/archive', headers=s.headers()).status_code == 404


def test_complete_local_chinese_pack_uses_registered_authorized_assets(map_http):
    import time
    s = map_http
    started = time.perf_counter()
    body = s.payload(tile_kind='mvt', style=True)
    for path in sorted((FIXTURES / 'assets/glyphs/Noto Sans Regular').glob('*.pbf')):
        asset = path.relative_to(FIXTURES / 'assets').as_posix()
        if asset not in body['asset_versions']:
            body['asset_versions'][asset] = s.upload_map_artifact('assets/' + asset)
    assert len([p for p in body['asset_versions'] if p.endswith('.pbf')]) == 85
    uploads_done = time.perf_counter()
    response = s.http.post(BASE, json=body, headers=s.headers())
    assert response.status_code == 201, response.text
    url = f'{BASE}/{response.json()["id"]}'
    resource_done = time.perf_counter()
    request_times = []
    coverage = set(json.loads((FIXTURES / 'glyph-coverage.json').read_text(encoding='utf8'))['codepoints'])
    # Dynamic agricultural labels outside the six original probe characters.
    for character in '梁子湖莲藕灌溉泵鳜':
        assert ord(character) in coverage
        start = ord(character) // 256 * 256
        path = f'glyphs/Noto Sans Regular/{start}-{start+255}.pbf'
        request_started = time.perf_counter()
        glyph = s.http.get(url + '/assets/' + path, headers=s.headers())
        request_times.append(time.perf_counter() - request_started)
        assert glyph.status_code == 200 and glyph.content == (FIXTURES / 'assets' / path).read_bytes()
        assert glyph.headers['Cache-Control'] == 'private,no-store'
    assert s.http.get(url + '/assets/glyphs/Unknown/0-255.pbf', headers=s.headers()).status_code == 404
    (s.map_fixture_dir / 'glyph-timings.json').write_text(json.dumps({
        'f04_registration_seconds':uploads_done-started,
        'map_validation_registration_seconds':resource_done-uploads_done,
        'authorized_glyph_request_seconds':request_times}, indent=2) + '\n', encoding='utf8')


def test_f04_bulk_read_authorization_checks_every_version_and_current_actor(map_http):
    from lzo.artifacts.service import ArtifactService
    from lzo.errors import ApiError
    from uuid import UUID
    s = map_http
    visible = UUID(s.upload_map_artifact('synthetic-raster.pmtiles'))
    hidden = UUID(s.upload_map_artifact('synthetic-raster.pmtiles', owner='object_b'))
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.map_settings)
        service.authorize_versions(s.actor('alice'), {visible})
        for refs in ({visible, hidden}, {visible, uuid4()}):
            with pytest.raises(ApiError) as error:
                service.authorize_versions(s.actor('alice'), refs)
            assert error.value.status == 404
    stale = s.actor('alice')
    grant = s.grant('alice', 'viewer', resource='object_b')
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.map_settings)
        with pytest.raises(ApiError) as error:
            service.authorize_versions(stale, {visible, hidden})
        assert error.value.status == 404
        service.authorize_versions(s.actor('alice'), {visible, hidden})
    s.revoke(grant)
    with s.transaction() as uow, pytest.raises(ApiError) as error:
        ArtifactService(uow, s.store, s.map_settings).authorize_versions(s.actor('alice'), {visible, hidden})
    assert error.value.status == 404


def test_f04_bulk_preserves_current_schema_owner_head_dependencies(map_http):
    from sqlalchemy import insert, update
    from lzo.artifacts.service import ArtifactService
    from lzo.auth.models import resource_scopes
    from lzo.errors import ApiError
    from lzo.governance.history import revision_visibility_dependencies
    from lzo.governance.models import resource_heads
    from lzo.governance.revisions import append_revision
    from uuid import UUID
    s = map_http
    version = UUID(s.upload_map_artifact('synthetic-raster.pmtiles'))
    with s.transaction() as uow:
        owner = s.ids_by_name['object_a']
        uow.session.execute(update(resource_scopes).where(resource_scopes.c.resource_id == owner).values(resource_kind='schema.draft'))
        revision = uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id == owner)).scalar_one()
        ref = append_revision(uow, s.actor('admin'), owner, revision, {'synthetic':'head dependency'}, 'schema.fixture_reference')
        uow.session.execute(insert(revision_visibility_dependencies).values(event_id=ref.event_id, resource_id=s.ids_by_name['object_b']))
        uow.commit()
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.map_settings)
        for check in (lambda actor:service.authorize_version(actor, version),
                      lambda actor:service.authorize_versions(actor, {version})):
            with pytest.raises(ApiError) as error:
                check(s.actor('alice'))
            assert error.value.status == 404
            check(s.actor('admin'))


@pytest.mark.parametrize('kind', ['evidence', 'semantic.export'])
def test_f04_bulk_specialized_kinds_keep_authoritative_fail_closed_path(map_http, kind):
    from sqlalchemy import update
    from lzo.artifacts.service import ArtifactService
    from lzo.auth.models import resource_scopes
    from lzo.errors import ApiError
    from uuid import UUID
    s = map_http
    version = UUID(s.upload_map_artifact('synthetic-raster.pmtiles'))
    # An ordinary upload cannot become valid controlled evidence/export merely
    # because a legacy/corrupt owner is classified as that reserved kind.
    with s.transaction() as uow:
        uow.session.execute(update(resource_scopes).where(resource_scopes.c.resource_id == s.ids_by_name['object_a']).values(resource_kind=kind))
        uow.commit()
    with s.transaction() as uow:
        service = ArtifactService(uow, s.store, s.map_settings)
        for check in (lambda:service.authorize_version(s.actor('admin'), version),
                      lambda:service.authorize_versions(s.actor('admin'), {version})):
            with pytest.raises(ApiError) as error:
                check()
            assert error.value.status == 404


def test_patch_replay_rechecks_complete_saved_response_without_new_effects(map_http):
    from sqlalchemy import func
    from uuid import UUID
    from lzo.governance.models import audit_events, outbox_events, revisions
    s = map_http
    # Alice keeps map/B scope rights, but A belongs to a separately revocable owner.
    a_grant = s.grant('alice', 'viewer', resource='object_b')
    body = s.payload(owner='object_b')
    response = s.http.post(BASE, json=body, headers=s.headers())
    assert response.status_code == 201, response.text
    resource = response.json()
    resource_id = UUID(resource['id'])
    url = f'{BASE}/{resource_id}'
    original_headers = s.headers(revision=1, key='old-title-patch')
    original_body = {'title':'saved title with A'}
    original = s.http.patch(url, json=original_body, headers=original_headers)
    assert original.status_code == 200, original.text
    saved = original.json()
    b = s.upload_map_artifact('synthetic-raster.pmtiles', owner='object_a')
    replacement = s.http.patch(url, json={'artifact_version_id':b}, headers=s.headers('admin', revision=2))
    assert replacement.status_code == 200, replacement.text
    # An authorized replay must return the exact saved revision/A, not latest B.
    replay = s.http.patch(url, json=original_body, headers=original_headers)
    assert replay.status_code == 200 and replay.json() == saved
    assert saved['revision'] == 2 and saved['artifact_version_id'] != b
    s.revoke(a_grant)
    current = s.http.get(url, headers=s.headers())
    assert current.status_code == 200 and current.json()['artifact_version_id'] == b
    # Prove this same live session still has current map/B edit authority.
    current_edit = s.http.patch(url, json={'title':'current B edit'}, headers=s.headers(revision=3))
    assert current_edit.status_code == 200, current_edit.text
    assert current_edit.json()['revision'] == 4 and current_edit.json()['artifact_version_id'] == b
    def counts():
        with s.transaction() as uow:
            events = select(revisions.c.event_id).where(revisions.c.resource_id == resource_id)
            return tuple(uow.session.execute(select(func.count()).select_from(table).where(
                table.c.event_id.in_(events))).scalar_one() for table in (revisions, audit_events, outbox_events))
    before = counts()
    denied = s.http.patch(url, json=original_body, headers=original_headers)
    assert denied.status_code == 404, denied.text
    assert counts() == before == (4, 4, 4)
    assert s.http.get(url, headers=s.headers()).json()['revision'] == 4


@pytest.mark.parametrize('source', [None, 0, 'bad', []])
def test_scalar_style_sources_are_controlled_on_create_and_patch(map_http, source):
    s = map_http
    body = s.payload()
    style = {'version':8, 'sources':{'bad':source}, 'layers':[]}
    invalid_style = s.upload_map_artifact('bad-style.json', data=json.dumps(style).encode())
    body['style_version_id'] = invalid_style
    response = s.http.post(BASE, json=body, headers=s.headers())
    assert response.status_code == 422, response.text
    assert response.json()['code'] == 'MAP_STYLE_INVALID'
    assert s.http.get(BASE, headers=s.headers()).json()['items'] == []
    resource = create(s)
    url = f'{BASE}/{resource["id"]}'
    response = s.http.patch(url, json={'style_version_id':invalid_style}, headers=s.headers(revision=1))
    assert response.status_code == 422 and response.json()['code'] == 'MAP_STYLE_INVALID'
    assert s.http.get(url, headers=s.headers()).json()['revision'] == 1
