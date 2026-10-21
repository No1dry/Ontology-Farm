"""R2/R3/R4 real byte/package regressions and supported positive encodings."""
import gzip
import io
import struct
import zlib

import pytest

from lzo.errors import ApiError
from lzo.maps.tiles import inspect_package, resolve_style, tile_bytes


def vi(value):
    out = bytearray()
    while value >= 128:
        out.append((value & 127) | 128)
        value >>= 7
    return bytes(out + bytes([value]))


def field(number, data):
    return vi(number * 8 + 2) + vi(len(data)) + data


def mvt(geometry=b'\x09\x02\x02', tags=b'\x00\x00', kind=1):
    feature = field(2, tags) + b'\x18' + vi(kind) + field(4, geometry)
    layer = field(1, b'x') + field(2, feature) + field(3, b'name') + field(4, field(1, b'farm')) + b'\x78\x02'
    return field(3, layer)


def one_entry_archive(payload, kind):
    # Real PMTiles v3, z0 tile0, NONE internal compression, declared exact sections.
    directory = b'\x01\x00\x01' + vi(len(payload)) + b'\x01'
    metaoff = 127 + len(directory)
    tileoff = metaoff + 2
    header = b'PMTiles\x03' + struct.pack('<11Q', 127, len(directory), metaoff, 2, tileoff, 0, tileoff, len(payload), 1, 1, 1)
    header += bytes((1, 1, 2 if kind == 'mvt' else 1, 1 if kind == 'mvt' else 2, 0, 0))
    header += struct.pack('<4iB2i', -1800000000, -850000000, 1800000000, 850000000, 0, 0, 0)
    return io.BytesIO(header + directory + b'{}' + payload)


@pytest.mark.parametrize(('geometry','tags','kind'), [
    (b'\x80', b'', 1),  # review's exact truncated uint32 geometry
    (b'\x09\x80', b'', 1),
    (b'\x09' + vi(1 << 32) + b'\x00', b'', 1),
    (b'\x09\x02\x02', b'\x80', 1),
    (b'\x09\x02\x02', b'\x00', 1),
    (b'\x09\x02\x02', b'\x01\x00', 1),
    (b'\x09\x02\x02', b'\x00\x01', 1),
    (b'\x09\x02\x02', b'\x00\x00\x00\x00', 1),
    (b'\x01', b'', 1),  # zero command count
    (b'\x0b\x02\x02', b'', 1),  # unknown command id
    (b'\x09\x02\x02\x09\x02\x02', b'', 1),  # two POINT commands
    (b'\x09\x02\x02', b'', 2),  # unfinished line
    (b'\x11\x02\x02\x02\x02\x0a\x02\x02', b'', 2),
    (b'\x09\x02\x02\x0a\x00\x00', b'', 2),  # zero-length segment
    (b'\x09\x02\x02\x12\x02\x00\x00\x02', b'', 3),  # no ClosePath
    (b'\x09\x02\x02\x0a\x02\x02\x0f', b'', 3),  # too few ring vertices
    (b'\x09\x02\x02\x12\x02\x00\x00\x02\x17', b'', 3),
])
def test_malformed_packed_mvt_rejected_as_tile_and_real_archive(geometry, tags, kind):
    payload = gzip.compress(mvt(geometry, tags, kind), mtime=0)
    for check in (lambda:tile_bytes(payload, 'mvt'),
                  lambda:inspect_package(one_entry_archive(payload, 'mvt'), 'pmtiles', 'mvt')):
        with pytest.raises(ApiError) as error:
            check()
        assert error.value.code == 'MAP_PACKAGE_INVALID'


def test_exact_review_truncated_feature_is_rejected():
    feature = b'\x18\x01\x22\x01\x80'
    layer = b'\x0a\x01x\x12' + bytes([len(feature)]) + feature + b'\x78\x02'
    payload = gzip.compress(b'\x1a' + bytes([len(layer)]) + layer, mtime=0)
    with pytest.raises(ApiError):
        tile_bytes(payload, 'mvt')
    with pytest.raises(ApiError):
        inspect_package(one_entry_archive(payload, 'mvt'), 'pmtiles', 'mvt')


@pytest.mark.parametrize(('geometry','kind'), [
    ([9,50,34],1), ([17,10,14,3,9],1),
    ([9,4,4,18,0,16,16,0],2),
    ([9,4,4,18,0,16,16,0,9,17,17,10,4,8],2),
    ([9,6,12,18,10,12,24,44,15],3),
    ([9,0,0,26,20,0,0,20,19,0,15,9,22,2,26,18,0,0,18,17,0,15,9,4,13,26,0,8,8,0,0,7,15],3),
])
def test_supported_mvt_spec_geometry_and_tags_still_read(geometry, kind):
    payload = gzip.compress(mvt(b''.join(vi(v) for v in geometry), kind=kind), mtime=0)
    assert tile_bytes(payload, 'mvt')[1] == 'application/vnd.mapbox-vector-tile'
    assert inspect_package(one_entry_archive(payload, 'mvt'), 'pmtiles', 'mvt')['max_zoom'] == 0


@pytest.mark.parametrize('unpacked', [False, True])
def test_repeated_numeric_protobuf_encodings_remain_supported(unpacked):
    if unpacked:
        feature = b'\x10\x00\x10\x00\x18\x01\x20\x09\x20\x02\x20\x02'
    else:
        feature = field(2,b'\x00') + field(2,b'\x00') + b'\x18\x01' + field(4,b'\x09') + field(4,b'\x02\x02')
    layer = field(1,b'x') + field(2,feature) + field(3,b'name') + field(4,field(1,b'farm')) + b'\x78\x02'
    assert tile_bytes(gzip.compress(field(3,layer),mtime=0),'mvt')[1] == 'application/vnd.mapbox-vector-tile'


def chunk(tag, data):
    return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data))


def png_parts(color=3, depth=8):
    return [chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, depth, color, 0, 0, 0)),
        chunk(b'PLTE', b'\x00\x00\x00'), chunk(b'IDAT', zlib.compress(b'\x00\x00')),
        chunk(b'IEND', b'')]


@pytest.mark.parametrize('case', ['missing_palette','palette_after_data','bad_palette_size',
    'duplicate_palette','nonempty_iend','split_idat','unknown_critical','palette_index_outside'])
def test_corrupt_png_critical_chunks_rejected_in_tile_and_archive(case):
    ihdr, palette, image, end = png_parts()
    cases = {
        'missing_palette':[ihdr,image,end],
        'palette_after_data':[ihdr,image,palette,end],
        'bad_palette_size':[ihdr,chunk(b'PLTE',b'xx'),image,end],
        'duplicate_palette':[ihdr,palette,palette,image,end],
        'nonempty_iend':[ihdr,palette,image,chunk(b'IEND',b'x')],
        'split_idat':[ihdr,palette,image,chunk(b'tEXt',b'x\x00y'),chunk(b'IDAT',b''),end],
        'unknown_critical':[ihdr,palette,chunk(b'ABCD',b''),image,end],
        'palette_index_outside':[ihdr,palette,chunk(b'IDAT',zlib.compress(b'\x00\x01')),end],
    }
    payload = b'\x89PNG\r\n\x1a\n' + b''.join(cases[case])
    for check in (lambda:tile_bytes(payload, 'raster'),
                  lambda:inspect_package(one_entry_archive(payload, 'raster'), 'pmtiles', 'raster')):
        with pytest.raises(ApiError):
            check()


@pytest.mark.parametrize('depth', [1,2,4,8])
def test_valid_indexed_png_supported_with_palette(depth):
    payload = b'\x89PNG\r\n\x1a\n' + b''.join(png_parts(depth=depth))
    assert tile_bytes(payload, 'raster')[1] == 'image/png'
    assert inspect_package(one_entry_archive(payload, 'raster'), 'pmtiles', 'raster')['max_zoom'] == 0


@pytest.mark.parametrize('scanlines', [
    b'\x00\x00\x01\x00\x01\x00',  # none
    b'\x01\x00\x01\x01\x01\xff',  # Sub
    b'\x02\x00\x01\x02\x01\xff',  # Up
    b'\x03\x00\x01\x03\x01\xff',  # Average
    b'\x04\x00\x01\x04\x01\xff',  # Paeth
])
def test_valid_indexed_png_filters_decode_actual_palette_indices(scanlines):
    # All encode rows [0,1] and [1,0] with a two-entry palette.
    payload = b'\x89PNG\r\n\x1a\n' + b''.join([
        chunk(b'IHDR',struct.pack('>IIBBBBB',2,2,8,3,0,0,0)),
        chunk(b'PLTE',b'\x00\x00\x00\xff\xff\xff'),
        chunk(b'IDAT',zlib.compress(scanlines)),chunk(b'IEND',b'')])
    assert tile_bytes(payload,'raster')[1] == 'image/png'


@pytest.mark.parametrize('source', [None, 0, 'bad', []])
def test_scalar_style_source_is_controlled_422(source):
    with pytest.raises(ApiError) as error:
        resolve_style({'version':8, 'sources':{'bad':source}, 'layers':[]}, {}, 'https://local/maps/id', 'mbtiles', 'raster')
    assert error.value.status == 422 and error.value.code == 'MAP_STYLE_INVALID'
