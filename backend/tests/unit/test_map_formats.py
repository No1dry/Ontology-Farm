"""Real format/HTTP boundary tests, without services or mocked archives."""
import io
import sqlite3
from pathlib import Path

import pytest

from lzo.errors import ApiError

FIXTURES = Path(__file__).resolve().parents[3] / 'tests/fixtures/maps'


@pytest.mark.parametrize(('header', 'expected'), [
    (None, (0, 99, 200)), ('bytes=0-127', (0, 99, 206)),
    ('bytes=4-7', (4, 7, 206)), ('bytes=95-', (95, 99, 206)),
    ('bytes=-5', (95, 99, 206)), ('bytes=-200', (0, 99, 206)),
])
def test_ranges_are_exact_and_clamp_only_satisfiable_ends(header, expected):
    from lzo.maps.tiles import archive_range
    assert archive_range(header, 100) == expected


@pytest.mark.parametrize('header', ['bytes=100-', 'bytes=20-10', 'bytes=-0',
    'bytes=0-1,4-7', 'items=0-4', 'bytes=wat', 'bytes=+1-3'])
def test_invalid_or_unsatisfiable_ranges_fail_honestly(header):
    from lzo.maps.tiles import archive_range
    with pytest.raises(ApiError) as error:
        archive_range(header, 100)
    assert error.value.status == 416


@pytest.mark.parametrize('path', ['../a.pbf', '/a.pbf', 'x/../a.pbf',
    'https://evil/x', 'file:///tmp/x', 'x\\a.pbf', 'x/%2e%2e/a.pbf', 'x?q=secret'])
def test_asset_manifest_rejects_paths_and_urls(path):
    from lzo.maps.tiles import asset_path
    with pytest.raises(ApiError):
        asset_path(path)


@pytest.mark.parametrize('kind', ['raster', 'mvt'])
@pytest.mark.parametrize('container', ['mbtiles', 'pmtiles'])
def test_real_archives_and_xyz_tms_tile_bytes(kind, container):
    from lzo.maps.tiles import inspect_package, package_tile
    path = FIXTURES / f'synthetic-{kind}.{container}'
    with path.open('rb') as stream:
        info = inspect_package(stream, container, kind)
        assert info['min_zoom'] == 10 and info['max_zoom'] == 14
        data, media, encoding = package_tile(stream, container, kind, 12, 3350, 1687)
    if kind == 'raster':
        assert media == 'image/png' and data.startswith(b'\x89PNG\r\n\x1a\n')
        assert encoding is None
    else:
        import gzip
        assert media == 'application/vnd.mapbox-vector-tile' and encoding == 'gzip'
        assert '梁子湖' in gzip.decompress(data).decode('utf8', errors='ignore')


def test_corrupt_pmtiles_and_sqlite_are_explicit(tmp_path):
    from lzo.maps.tiles import inspect_package
    for kind in ('mbtiles', 'pmtiles'):
        path = tmp_path / kind
        path.write_bytes(b'not an archive')
        with path.open('rb') as stream, pytest.raises(ApiError) as error:
            inspect_package(stream, kind, 'raster')
        assert error.value.code == 'MAP_PACKAGE_INVALID'


def test_styles_reject_all_remote_and_unlisted_references():
    from lzo.maps.tiles import resolve_style
    for style in [
        {'version': 8, 'sources': {'x': {'type': 'vector', 'url': 'https://evil/x'}}, 'layers': []},
        {'version': 8, 'sources': {}, 'layers': [], 'sprite': 'assets/missing'},
        {'version': 8, 'sources': {}, 'layers': [], 'glyphs': 'https://evil/{range}.pbf'},
        {'version': 8, 'sources': {}, 'layers': [], 'metadata': {'secret': 'file:///private'}},
    ]:
        with pytest.raises(ApiError):
            resolve_style(style, {}, 'https://local/api/v1/maps/resources/id', 'mbtiles', 'mvt')


def test_png_with_valid_checksums_but_no_image_data_is_corrupt():
    import struct
    import zlib
    from lzo.maps.tiles import tile_bytes
    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))
    data = b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 256, 256, 8, 6, 0, 0, 0)) + chunk(b'IEND', b'')
    with pytest.raises(ApiError) as error:
        tile_bytes(data, 'raster')
    assert error.value.code == 'MAP_PACKAGE_INVALID'


def test_uncompressed_mvt_pmtiles_preserves_encoding_and_actual_tile():
    import gzip
    import struct
    from lzo.maps.tiles import inspect_package, package_tile
    db = sqlite3.connect(FIXTURES / 'synthetic-mvt.mbtiles')
    data = gzip.decompress(db.execute('SELECT tile_data FROM tiles LIMIT 1').fetchone()[0])
    db.close()
    def vi(n):
        result = bytearray()
        while n >= 128:
            result.append((n & 127) | 128)
            n >>= 7
        return bytes(result + bytes([n]))
    directory = b'\x01\x00\x01' + vi(len(data)) + b'\x01'
    metaoff = 127 + len(directory)
    tileoff = metaoff + 2
    header = b'PMTiles\x03' + struct.pack('<11Q', 127, len(directory), metaoff, 2, tileoff, 0, tileoff, len(data), 1, 1, 1)
    header += bytes((1, 1, 1, 1, 0, 0)) + struct.pack('<4iB2i', -1800000000, -850000000, 1800000000, 850000000, 0, 0, 0)
    stream = io.BytesIO(header + directory + b'{}' + data)
    assert inspect_package(stream, 'pmtiles', 'mvt')['min_zoom'] == 0
    tile, media, encoding = package_tile(stream, 'pmtiles', 'mvt', 0, 0, 0)
    assert tile == data and encoding is None and media == 'application/vnd.mapbox-vector-tile'


def test_pmtiles_overlapping_sections_are_rejected():
    import struct
    from lzo.maps.tiles import inspect_package
    data = bytearray((FIXTURES / 'synthetic-raster.pmtiles').read_bytes())
    struct.pack_into('<Q', data, 24, 127)
    with pytest.raises(ApiError):
        inspect_package(io.BytesIO(data), 'pmtiles', 'raster')


def test_runtime_tile_paths_reject_unbounded_payloads():
    from lzo.maps.tiles import tile_bytes
    with pytest.raises(ApiError):
        tile_bytes(b'x' * (8 * 1024 * 1024 + 1), 'raster')


@pytest.mark.parametrize('data', [b'\xff\xd8\xffgarbage\xff\xd9', b'RIFF\x04\x00\x00\x00WEBP'])
def test_unsupported_raster_codecs_do_not_pass_signature_only_validation(data):
    from lzo.maps.tiles import tile_bytes
    with pytest.raises(ApiError) as error:
        tile_bytes(data, 'raster')
    assert error.value.code == 'MAP_FORMAT_UNSUPPORTED'


def test_fixture_manifest_hashes_coverage_and_licenses_are_complete():
    import hashlib
    import json
    manifest = json.loads((FIXTURES / 'manifest.json').read_text(encoding='utf8'))
    assert manifest['center'] == [114.5, 30.2]
    for name, info in manifest['files'].items():
        data = (FIXTURES / name).read_bytes()
        assert len(data) == info['bytes'] and hashlib.sha256(data).hexdigest() == info['sha256']
    assert len([name for name in manifest['files'] if name.endswith('.pbf')]) == 85
    assert 'LICENSES.md' in manifest['files'] and 'LICENSE-PMTILES.txt' in manifest['files']
    coverage = set(json.loads((FIXTURES / 'glyph-coverage.json').read_text(encoding='utf8'))['codepoints'])
    assert set(range(0x4e00, 0x9fd1)) <= coverage
    assert manifest['glyphs']['missing_unified_ideographs'] == list(range(0x9fd1, 0xa000))
