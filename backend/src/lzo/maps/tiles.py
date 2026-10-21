"""Bounded MBTiles/PMTiles v3 reads and local MapLibre style validation.

PMTiles layout follows the public v3 spec; directory/Hilbert logic adapted
from the BSD-3-Clause reference, copyright 2021 Protomaps LLC. The full
notice is retained in tests/fixtures/maps/LICENSE-PMTILES.txt.
No archive is mapped or read as one byte string. Limits fail explicitly.
"""
import gzip
import hashlib
import io
import json
import math
import re
import sqlite3
import struct
from contextlib import contextmanager
from pathlib import Path

from lzo.errors import ApiError

MAX_TILE = 8 * 1024 * 1024
MAX_DIRECTORY = 16 * 1024 * 1024
MAX_ENTRIES = 200_000
MAX_STYLE = 1024 * 1024


def invalid():
    return ApiError('MAP_PACKAGE_INVALID', 422, 'Map package is corrupt or inconsistent')


def archive_range(header, size):
    if header is None:
        return 0, size - 1, 200
    match = re.fullmatch(r'bytes=([0-9]{0,20})-([0-9]{0,20})', header)
    if not match or not any(match.groups()):
        raise ApiError('MAP_RANGE_INVALID', 416, 'One satisfiable byte range is required')
    start, end = match.groups()
    if start:
        start = int(start)
        end = int(end) if end else size - 1
        if start >= size or end < start:
            raise ApiError('MAP_RANGE_INVALID', 416, 'Byte range is unsatisfiable')
        return start, min(end, size - 1), 206
    suffix = int(end)
    if suffix == 0 or size == 0:
        raise ApiError('MAP_RANGE_INVALID', 416, 'Byte range is unsatisfiable')
    return max(0, size - suffix), size - 1, 206


def asset_path(value):
    if (not isinstance(value, str) or not value or len(value) > 256 or
            any(c in value for c in '\\:%?#\x00') or value.startswith('/') or
            any(part in {'', '.', '..'} for part in value.split('/')) or
            any(ord(c) < 32 for c in value)):
        raise ApiError('MAP_ASSET_INVALID', 422, 'Asset must be a registered relative path')
    return value


def bounded_json(stream, maximum=MAX_STYLE):
    data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Map metadata exceeds the supported limit')
    try:
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError, RecursionError):
        raise invalid() from None


def verify_digest(stream, blob):
    digest, size = hashlib.sha256(), 0
    stream.seek(0)
    while data := stream.read(1024 * 1024):
        digest.update(data)
        size += len(data)
    stream.seek(0)
    if size != blob.size_bytes or digest.hexdigest() != blob.sha256:
        raise ApiError('BLOB_CORRUPT', 503, 'Stored artifact failed validation')


def _gunzip(data, limit):
    try:
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError()
        return value
    except (OSError, EOFError, ValueError):
        raise invalid() from None


def _varint(stream):
    value = 0
    for shift in range(0, 70, 7):
        b = stream.read(1)
        if not b:
            raise invalid()
        value |= (b[0] & 127) << shift
        if b[0] < 128:
            return value
    raise invalid()


def _protobuf(data):
    """Parse bounded protobuf wire fields; reject truncation and invalid wire types."""
    stream = io.BytesIO(data)
    fields = []
    while stream.tell() < len(data):
        tag = _varint(stream)
        field, wire = tag >> 3, tag & 7
        if field == 0:
            raise invalid()
        if wire == 0:
            value = _varint(stream)
        elif wire in (1, 2, 5):
            length = _varint(stream) if wire == 2 else (8 if wire == 1 else 4)
            if length > len(data) - stream.tell():
                raise invalid()
            value = stream.read(length)
        else:
            raise invalid()
        fields.append((field, wire, value))
        if len(fields) > MAX_ENTRIES:
            raise invalid()
    return fields


def _packed_uint32(data):
    """Decode the numeric payload, not just its length-delimited envelope."""
    source, result = io.BytesIO(data), []
    while source.tell() < len(data):
        value = 0
        for shift in range(0, 35, 7):
            raw = source.read(1)
            if not raw:
                raise invalid()
            value |= (raw[0] & 127) << shift
            if raw[0] < 128:
                break
        else:
            raise invalid()
        if value > 0xffffffff:
            raise invalid()
        result.append(value)
        if len(result) > MAX_ENTRIES:
            raise invalid()
    return result


def _feature_numbers(fields, number):
    values = []
    for n, wire, value in fields:
        if n != number:
            continue
        if wire == 2:
            values.extend(_packed_uint32(value))
        elif wire == 0 and value <= 0xffffffff:
            # Protobuf readers also accept the unpacked repeated numeric form.
            values.append(value)
        else:
            raise invalid()
        if len(values) > MAX_ENTRIES:
            raise invalid()
    return values


def _validate_geometry(values, kind):
    """MVT 2.1 command/count and parameter structure for supported geometries."""
    position = x = y = 0
    def command(expected, minimum, exact=None):
        nonlocal position, x, y
        if position >= len(values):
            raise invalid()
        encoded = values[position]
        position += 1
        ident, count = encoded & 7, encoded >> 3
        if ident != expected or count < minimum or (exact is not None and count != exact):
            raise invalid()
        if expected == 7:
            return
        if count > (len(values) - position) // 2:
            raise invalid()
        for _ in range(count):
            raw_x, raw_y = values[position:position+2]
            position += 2
            # Zigzag's -2**31 sentinel is outside MVT's supported parameter range.
            if raw_x == 0xffffffff or raw_y == 0xffffffff:
                raise invalid()
            dx, dy = (raw_x >> 1) ^ -(raw_x & 1), (raw_y >> 1) ^ -(raw_y & 1)
            if expected == 2 and dx == dy == 0:
                raise invalid()
            x, y = x + dx, y + dy
    if not values:
        raise invalid()
    if kind == 1:
        command(1, 1)
    elif kind in (2, 3):
        while position < len(values):
            command(1, 1, 1)
            start = x, y
            command(2, 1 if kind == 2 else 2)
            if kind == 3:
                if (x, y) == start:
                    raise invalid()
                command(7, 1, 1)
    else:
        raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'Unknown experimental MVT geometry is unsupported')
    if position != len(values):
        raise invalid()


def _validate_mvt_feature(data, keys, value_count):
    fields = _protobuf(data)
    types = []
    for number, wire, value in fields:
        if number == 1 and (wire != 0 or value > 0xffffffffffffffff):
            raise invalid()
        if number == 3:
            if wire != 0 or value > 3:
                raise invalid()
            types.append(value)
    if not types:
        raise invalid()
    tags = _feature_numbers(fields, 2)
    if (len(tags) % 2 or len(set(tags[::2])) != len(tags) // 2 or
            any(key >= keys or value >= value_count for key, value in zip(tags[::2], tags[1::2]))):
        raise invalid()
    _validate_geometry(_feature_numbers(fields, 4), types[-1])


def _validate_indexed_pixels(decoded, width, depth, row_bytes, palette_entries):
    """Undo PNG filters for packed palette indices under the decoded-size bound."""
    previous = bytearray(row_bytes - 1)
    for offset in range(0, len(decoded), row_bytes):
        filtering = decoded[offset]
        row = bytearray(decoded[offset+1:offset+row_bytes])
        for index in range(len(row)):
            left = row[index-1] if index else 0
            up = previous[index]
            upper_left = previous[index-1] if index else 0
            predictor = 0
            if filtering == 1:
                predictor = left
            elif filtering == 2:
                predictor = up
            elif filtering == 3:
                predictor = (left + up) // 2
            elif filtering == 4:
                base = left + up - upper_left
                distances = abs(base-left), abs(base-up), abs(base-upper_left)
                predictor = (left, up, upper_left)[distances.index(min(distances))]
            row[index] = (row[index] + predictor) & 255
        for x in range(width):
            bit = x * depth
            value = row[bit // 8] >> (8 - depth - bit % 8) & ((1 << depth) - 1)
            if value >= palette_entries:
                raise invalid()
        previous = row


def tile_bytes(data, kind, compression=None):
    if not isinstance(data, bytes) or len(data) > MAX_TILE or not data:
        raise invalid()
    if kind == 'mvt':
        if compression not in (None, 1, 2):
            raise invalid()
        if compression != 1 and not data.startswith(b'\x1f\x8b'):
            raise invalid()
        fields = _protobuf(data if compression == 1 else _gunzip(data, MAX_TILE))
        layers = [v for n, w, v in fields if n == 3 and w == 2]
        if not layers:
            raise invalid()
        for layer in layers:
            parsed = _protobuf(layer)
            if not any(n == 1 and w == 2 and v for n, w, v in parsed) or not any(
                    n == 15 and w == 0 and v in (1, 2) for n, w, v in parsed):
                raise invalid()
            keys = sum(n == 3 and w == 2 for n, w, v in parsed)
            value_count = sum(n == 4 and w == 2 for n, w, v in parsed)
            for n, w, v in parsed:
                if n == 2 and w == 2:
                    _validate_mvt_feature(v, keys, value_count)
        return data, 'application/vnd.mapbox-vector-tile', None if compression == 1 else 'gzip'
    if compression not in (None, 1):
        raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'Raster tile compression is unsupported')
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        offset, seen_end, image_data = 8, False, []
        palette_entries = 0
        seen_idat = idat_finished = False
        row_bytes = expected = None
        import zlib
        while offset < len(data):
            if offset + 12 > len(data):
                raise invalid()
            size = int.from_bytes(data[offset:offset+4], 'big')
            tag, value = data[offset+4:offset+8], data[offset+8:offset+8+size]
            end = offset + size + 12
            if end > len(data) or zlib.crc32(tag + value) != int.from_bytes(data[end-4:end], 'big'):
                raise invalid()
            if not re.fullmatch(b'[A-Za-z]{4}', tag) or tag[2] & 32:
                raise invalid()
            if offset == 8 and (tag != b'IHDR' or size != 13):
                raise invalid()
            if tag[0] & 32 == 0 and tag not in (b'IHDR', b'PLTE', b'IDAT', b'IEND'):
                raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'Unknown critical PNG chunk is unsupported')
            if seen_idat and tag != b'IDAT':
                idat_finished = True
            if tag == b'IHDR':
                if offset != 8:
                    raise invalid()
                width, height, depth, color, compression_method, filtering, interlace = struct.unpack('>IIBBBBB', value)
                allowed = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
                if not 1 <= width <= 4096 or not 1 <= height <= 4096 or depth not in allowed.get(color, ()) or compression_method or filtering:
                    raise invalid()
                if interlace:
                    raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'Interlaced raster tiles are unsupported')
                channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
                row_bytes = (width * channels * depth + 7) // 8 + 1
                expected = row_bytes * height
                if expected > MAX_DIRECTORY:
                    raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Decoded raster tile exceeds the supported limit')
            if tag == b'PLTE':
                if (seen_idat or palette_entries or color in (0, 4) or
                        not 0 < size <= 768 or size % 3 or (color == 3 and size // 3 > 1 << depth)):
                    raise invalid()
                palette_entries = size // 3
            if tag == b'IDAT':
                if idat_finished or (color == 3 and not palette_entries):
                    raise invalid()
                seen_idat = True
                image_data.append(value)
            if tag == b'IEND':
                if size or not seen_idat or end != len(data):
                    raise invalid()
                seen_end = True
            offset = end
        if not seen_end:
            raise invalid()
        try:
            decoder = zlib.decompressobj()
            decoded = decoder.decompress(b''.join(image_data), expected + 1)
            if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail or len(decoded) != expected or
                    any(decoded[pos] > 4 for pos in range(0, len(decoded), row_bytes))):
                raise invalid()
        except zlib.error:
            raise invalid() from None
        if color == 3:
            _validate_indexed_pixels(decoded, width, depth, row_bytes, palette_entries)
        return data, 'image/png', None
    if data.startswith((b'\xff\xd8\xff', b'RIFF')):
        raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'Only PNG raster and MVT tiles are supported')
    raise invalid()


@contextmanager
def _sqlite(stream):
    db = None
    try:
        # The name is obtained only from ArtifactStore.open, never request data.
        uri = Path(stream.name).resolve().as_uri() + '?mode=ro&immutable=1'
        db = sqlite3.connect(uri, uri=True)
        db.execute('PRAGMA query_only=ON')
        db.execute('PRAGMA trusted_schema=OFF')
        work = [0]
        def progress():
            work[0] += 1
            return work[0] > 100_000
        db.set_progress_handler(progress, 1000)
        tables = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name IN ('metadata','tiles')"))
        if tables != {'metadata': 'table', 'tiles': 'table'}:
            raise invalid()
        yield db
    except (sqlite3.Error, OSError, ValueError, TypeError):
        raise invalid() from None
    finally:
        if db:
            db.close()


def _pm_header(stream):
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    data = stream.read(127)
    if len(data) != 127 or data[:8] != b'PMTiles\x03':
        raise invalid()
    values = struct.unpack_from('<11Q', data, 8)
    sections = []
    for off, length in zip(values[:8:2], values[1:8:2]):
        if length and (off < 127 or off + length > size):
            raise invalid()
        if length:
            sections.append((off, off + length))
    sections.sort()
    if any(a[1] > b[0] for a, b in zip(sections, sections[1:])) or not all(values[i] for i in (1, 3, 7)):
        raise invalid()
    internal, compression, tile_type, minimum, maximum = data[97:102]
    if internal not in (1, 2) or compression not in (1, 2) or tile_type not in (1, 2):
        raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'PMTiles compression or tile type is unsupported')
    bbox = tuple(v / 1e7 for v in struct.unpack_from('<4i', data, 102))
    if not 0 <= minimum <= maximum <= 24 or not (-180 <= bbox[0] < bbox[2] <= 180 and -85.051129 <= bbox[1] < bbox[3] <= 85.051129):
        raise invalid()
    return {'sections': values[:8], 'counts': values[8:], 'internal': internal,
            'compression': compression, 'type': tile_type,
            'min_zoom': minimum, 'max_zoom': maximum, 'bbox': bbox}


def _read(stream, offset, length, limit):
    if length < 0 or length > limit:
        raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Map index or tile exceeds the supported limit')
    stream.seek(offset)
    data = stream.read(length)
    if len(data) != length:
        raise invalid()
    return data


def _directory(stream, offset, length, compression):
    data = _read(stream, offset, length, MAX_DIRECTORY)
    if compression == 2:
        data = _gunzip(data, MAX_DIRECTORY)
    source = io.BytesIO(data)
    count = _varint(source)
    if count > MAX_ENTRIES:
        raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Map directory exceeds the supported limit')
    entries, last = [], 0
    for index in range(count):
        delta = _varint(source)
        if index and delta == 0:
            raise invalid()
        last += delta
        entries.append([last, 0, 0, 0])
    for slot in (1, 2, 3):
        for i, entry in enumerate(entries):
            value = _varint(source)
            entry[slot] = (entries[i-1][3] + entries[i-1][2] if i and value == 0 else value - 1) if slot == 3 else value
    if source.tell() != len(data):
        raise invalid()
    return entries


def _tileid(z, x, y):
    result = ((1 << (z * 2)) - 1) // 3
    for a in range(z - 1, -1, -1):
        s = 1 << a
        rx, ry = x & s, y & s
        result += ((3 * rx) ^ ry) << a
        if ry == 0:
            if rx:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
    return result


def _pm_entries(stream, header):
    root, rlen, _, _, leaf, llen, tile, tlen = header['sections']
    visited, count = set(), 0
    def walk(offset, length, depth):
        nonlocal count
        if depth > 4 or (offset, length) in visited:
            raise invalid()
        visited.add((offset, length))
        for entry in _directory(stream, offset, length, header['internal']):
            count += 1
            if count > MAX_ENTRIES:
                raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Map index exceeds the supported limit')
            ident, run, size, off = entry
            if off < 0 or size <= 0:
                raise invalid()
            if run:
                if off + size > tlen:
                    raise invalid()
                yield ident, run, tile + off, size
            else:
                if off + size > llen:
                    raise invalid()
                yield from walk(leaf + off, size, depth + 1)
    yield from walk(root, rlen, 0)


def inspect_package(stream, container, kind):
    if container == 'mbtiles':
        with _sqlite(stream) as db:
            meta = dict(db.execute('SELECT name,value FROM metadata LIMIT 1001'))
            if len(meta) > 1000 or meta.get('format') not in ('png', 'pbf'):
                raise ApiError('MAP_FORMAT_UNSUPPORTED', 422, 'MBTiles format is unsupported')
            if (meta['format'] == 'pbf') != (kind == 'mvt'):
                raise invalid()
            try:
                result = {'bbox': tuple(map(float, meta['bounds'].split(','))), 'min_zoom': int(meta['minzoom']), 'max_zoom': int(meta['maxzoom'])}
            except (KeyError, ValueError, TypeError):
                raise invalid() from None
            count = 0
            zooms = set()
            for z, x, row, data in db.execute('SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles LIMIT ?', (MAX_ENTRIES + 1,)):
                count += 1
                if count > MAX_ENTRIES:
                    raise ApiError('MAP_LIMIT_EXCEEDED', 422, 'Map tile count exceeds the supported limit')
                if not all(isinstance(v, int) for v in (z, x, row)) or not 0 <= z <= 24 or not 0 <= x < 1 << z or not 0 <= row < 1 << z:
                    raise invalid()
                zooms.add(z)
                tile_bytes(data, kind)
            if not count or min(zooms) != result['min_zoom'] or max(zooms) != result['max_zoom']:
                raise invalid()
            return result
    header = _pm_header(stream)
    if (header['type'] == 1) != (kind == 'mvt'):
        raise invalid()
    _, _, meta, mlen, *_ = header['sections']
    data = _read(stream, meta, mlen, MAX_STYLE)
    bounded_json(io.BytesIO(_gunzip(data, MAX_STYLE) if header['internal'] == 2 else data))
    count, end = 0, -1
    for ident, run, off, length in _pm_entries(stream, header):
        if ident <= end or ident < _tileid(header['min_zoom'], 0, 0) or ident + run > ((1 << (2 * (header['max_zoom'] + 1))) - 1) // 3:
            raise invalid()
        end = ident + run - 1
        count += run
        tile_bytes(_read(stream, off, length, MAX_TILE), kind, header['compression'])
    if not count or count != header['counts'][0]:
        raise invalid()
    return {k: header[k] for k in ('bbox', 'min_zoom', 'max_zoom')}


def package_tile(stream, container, kind, z, x, y):
    if not 0 <= z <= 24 or not 0 <= x < 1 << z or not 0 <= y < 1 << z:
        raise ApiError('MAP_OUT_OF_COVERAGE', 404, 'Tile address is outside map coverage')
    if container == 'mbtiles':
        with _sqlite(stream) as db:
            row = db.execute('SELECT tile_data FROM tiles WHERE zoom_level=? AND tile_column=? AND tile_row=? LIMIT 1', (z, x, (1 << z) - 1 - y)).fetchone()
            if row:
                return tile_bytes(row[0], kind)
    else:
        header, ident = _pm_header(stream), _tileid(z, x, y)
        for start, run, off, length in _pm_entries(stream, header):
            if start <= ident < start + run:
                return tile_bytes(_read(stream, off, length, MAX_TILE), kind, header['compression'])
    raise ApiError('MAP_TILE_MISSING', 404, 'Tile is missing from this package')


def tile_in_bbox(z, x, y, bbox):
    n = 1 << z
    west, east = x / n * 360 - 180, (x + 1) / n * 360 - 180
    north = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * y / n))))
    south = math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * (y + 1) / n))))
    return west < bbox[2] and east > bbox[0] and south < bbox[3] and north > bbox[1]


def resolve_style(style, assets, base, container, tile_kind, online_template=None):
    """Only a small explicit MapLibre subset has authority to introduce URLs."""
    style = json.loads(json.dumps(style))
    if style.get('version') != 8 or not isinstance(style.get('sources'), dict) or not isinstance(style.get('layers'), list):
        raise invalid()
    # Reject URL-shaped strings anywhere, including metadata/imports/unknown extensions.
    def check(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'imports', 'url', 'data', 'models', 'images'}:
                    raise ApiError('MAP_STYLE_INVALID', 422, 'Style contains an unsupported reference')
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, str) and (':' in value or '\\' in value or value.startswith('/') or '..' in value or '%' in value or '?' in value):
            raise ApiError('MAP_STYLE_INVALID', 422, 'Style contains a nonlocal reference')
    check(style)
    from urllib.parse import quote
    if 'glyphs' in style:
        glyphs = style['glyphs']
        if glyphs != 'assets/glyphs/{fontstack}/{range}.pbf' or not any(p.startswith('glyphs/') and p.endswith('.pbf') for p in assets):
            raise ApiError('MAP_STYLE_INVALID', 422, 'Glyphs must use registered local ranges')
        style['glyphs'] = base + '/' + glyphs
    if 'sprite' in style:
        sprite = style['sprite']
        if not isinstance(sprite, str) or not sprite.startswith('assets/'):
            raise invalid()
        path = asset_path(sprite[7:])
        if not all(path + suffix in assets for suffix in ('.json', '.png', '@2x.json', '@2x.png')):
            raise ApiError('MAP_STYLE_INVALID', 422, 'Sprite must be registered at both pixel ratios')
        style['sprite'] = base + '/assets/' + quote(path, safe='/')
    for source in style['sources'].values():
        if (not isinstance(source, dict) or
                source.get('type') != ('vector' if tile_kind == 'mvt' else 'raster') or
                source.get('tiles') != ['tiles/{z}/{x}/{y}']):
            raise ApiError('MAP_STYLE_INVALID', 422, 'Style sources must use this resource tiles')
        source['tiles'] = [online_template or base + '/tiles/{z}/{x}/{y}']
    return style
