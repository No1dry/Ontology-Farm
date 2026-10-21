"""Actual raster/MVT bytes and authenticated PMTiles Range/revocation."""
import pytest

from test_map_resources import BASE, FIXTURES, create, map_http  # noqa: F401


def test_pmtiles_range_is_exact_and_authorized_after_revocation(map_http):
    s = map_http
    resource = create(s)
    url = f'{BASE}/{resource["id"]}/archive'
    original = (FIXTURES / 'synthetic-raster.pmtiles').read_bytes()
    for header, expected in [('bytes=0-127', original[:128]), ('bytes=127-999999999', original[127:]),
                              ('bytes=-20', original[-20:]), ('bytes=127-', original[127:])]:
        response = s.http.get(url, headers={**s.headers(), 'Range':header})
        assert response.status_code == 206, response.text
        assert response.headers['Accept-Ranges'] == 'bytes'
        assert response.headers['Cache-Control'] == 'private,no-store'
        assert response.content == expected
        assert int(response.headers['Content-Length']) == len(expected)
        assert response.headers['Content-Range'].endswith('/' + str(len(original)))
    for header in ('bytes=999999999-', 'bytes=10-9', 'bytes=0-1,3-4', 'invalid'):
        response = s.http.get(url, headers={**s.headers(), 'Range': header})
        assert response.status_code == 416
        assert response.headers['Content-Range'] == f'bytes */{len(original)}'
    full = s.http.get(url, headers=s.headers())
    assert full.status_code == 200 and full.content == original
    stale = full.headers['ETag'][:-1] + '-forged"'
    conditional = s.http.get(url, headers={**s.headers(), 'Range':'bytes=0-127', 'If-Range':stale})
    assert conditional.status_code == 200 and conditional.content == original
    conditional = s.http.get(url, headers={**s.headers(), 'Range':'bytes=0-127', 'If-Range':full.headers['ETag']})
    assert conditional.status_code == 206 and conditional.content == original[:128]
    # Revoke the real editor grant; the accessible map record alone cannot help.
    from lzo.auth.models import grants
    from lzo.auth.schemas import GrantDetail
    from sqlalchemy import select
    with s.transaction() as uow:
        rows = uow.session.execute(select(grants).where(grants.c.user_id == s.users['alice'], grants.c.active.is_(True))).mappings().all()
    for row in rows:
        s.revoke(GrantDetail(**row))
    assert s.http.get(url, headers={**s.headers(), 'Range':'bytes=0-127', 'If-None-Match':full.headers['ETag']}).status_code == 404


@pytest.mark.parametrize('kind', ['raster','mvt'])
@pytest.mark.parametrize('container', ['mbtiles','pmtiles'])
def test_real_tile_formats_missing_and_coverage(map_http, kind, container):
    s = map_http
    resource = create(s, kind=container, tile_kind=kind)
    url = f'{BASE}/{resource["id"]}/tiles'
    response = s.http.get(url + '/12/3350/1687', headers=s.headers())
    assert response.status_code == 200, response.text
    if kind == 'raster':
        assert response.headers['Content-Type'] == 'image/png' and response.content.startswith(b'\x89PNG')
    else:
        assert response.headers['Content-Type'] == 'application/vnd.mapbox-vector-tile'
        assert response.headers['Content-Encoding'] == 'gzip'
        assert '梁子湖' in response.content.decode('utf8', errors='ignore')
    for address in ('9/837/421', '12/0/0', '25/0/0', '12/-1/0'):
        response = s.http.get(url + '/' + address, headers=s.headers())
        assert response.status_code == 404
        assert response.json()['code'] == 'MAP_OUT_OF_COVERAGE'


@pytest.mark.parametrize('container', ['mbtiles', 'pmtiles'])
def test_corrupt_archive_rejected_before_resource_registration(map_http, container):
    s = map_http
    body = s.payload(kind=container)
    body['artifact_version_id'] = s.upload_map_artifact('broken.' + container, data=b'not an archive')
    response = s.http.post(BASE, json=body, headers=s.headers())
    assert response.status_code == 422 and response.json()['code'] == 'MAP_PACKAGE_INVALID'


def test_unregistered_assets_and_encoded_traversal_are_not_read(map_http):
    s = map_http
    resource = create(s, style=True)
    url = f'{BASE}/{resource["id"]}/assets/'
    for path in ('unknown.png', 'sprite/%252e%252e/secret.png', '%2Fetc%2Fpasswd', 'file%3A/private', 'sprite/%2e%2e/secret'):
        assert s.http.get(url + path, headers=s.headers()).status_code in (404, 422)


def test_missing_in_coverage_tile_is_distinct_from_outside_coverage(map_http):
    s = map_http
    import shutil
    import sqlite3
    path = s.store.root / 'hole.mbtiles'
    shutil.copyfile(FIXTURES / 'synthetic-raster.mbtiles', path)
    with sqlite3.connect(path) as db:
        db.execute('DELETE FROM tiles WHERE zoom_level=12 AND tile_column=3350 AND tile_row=2408')
    body = s.payload(kind='mbtiles')
    body['artifact_version_id'] = s.upload_map_artifact('hole.mbtiles', data=path.read_bytes())
    resource = s.http.post(BASE, json=body, headers=s.headers()).json()
    response = s.http.get(f'{BASE}/{resource["id"]}/tiles/12/3350/1687', headers=s.headers())
    assert response.status_code == 404 and response.json()['code'] == 'MAP_TILE_MISSING'
