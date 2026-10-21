"""Generate licensed synthetic Liangzi Lake packages and complete local assets.

PMTiles v3/Hilbert implementation adapted from the BSD-3-Clause reference;
copyright 2021 Protomaps LLC. See tests/fixtures/maps/LICENSE-PMTILES.txt.
"""

import gzip
import hashlib
import json
import math
import platform
import sqlite3
import struct
import zlib
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1] / "tests/fixtures/maps"
OUT = ROOT
OUT.mkdir(parents=True, exist_ok=True)


def varint(value):
    out = bytearray()
    while value > 127:
        out.append((value & 127) | 128)
        value >>= 7
    out.append(value)
    return bytes(out)


def field(number, value):
    return varint(number << 3 | 2) + varint(len(value)) + value


def uint(number, value):
    return varint(number << 3) + varint(value)


def zigzag(value):
    return (value << 1) ^ (value >> 31)


def mvt_point(z, x, y):
    """One genuine MVT v2 point with a name and sprite identifier."""
    label = f"梁子湖 {z}/{x}/{y} 农田 水塘 果园"
    geometry = b"".join(map(varint, (9, zigzag(2048), zigzag(2048))))
    feature = (uint(1, 1) + field(2, b"\x00\x00\x01\x01") +
               uint(3, 1) + field(4, geometry))
    layer = (field(1, b"farms") + field(2, feature) +
             field(3, b"name") + field(3, b"icon") +
             field(4, field(1, label.encode())) +
             field(4, field(1, b"farm")) +
             uint(5, 4096) + uint(15, 2))
    return field(3, layer)


def png_chunk(tag, data):
    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data))


def png(width, height, pixel):
    rows = bytearray()
    for py in range(height):
        rows.append(0)
        for px in range(width):
            rows.extend(pixel(px, py))
    return (b"\x89PNG\r\n\x1a\n" +
            png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) +
            png_chunk(b"IDAT", zlib.compress(bytes(rows), 9)) +
            png_chunk(b"IEND", b""))


def raster(z, x, y):
    def pixel(px, py):
        stripe = (px // 24 + py // 32 + x + y) % 3
        pond = ((px - 185) ** 2) / 1700 + ((py - 75) ** 2) / 650 < 1
        if pond:
            return (69, 145, 179, 255)
        if px % 64 < 2 or py % 64 < 2:
            return (229, 219, 171, 255)
        return ((119, 157, 97), (139, 175, 99), (104, 146, 91))[stripe] + (255,)
    return png(256, 256, pixel)


def xyz_centers():
    lon, lat = 114.5, 30.2
    for z in range(10, 15):
        cx = int((lon + 180) / 360 * (1 << z))
        cy = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * (1 << z))
        for y in range(cy - 1, cy + 2):
            for x in range(cx - 1, cx + 2):
                yield z, x, y


def build_mbtiles(kind):
    path = OUT / f"synthetic-{kind}.mbtiles"
    path.unlink(missing_ok=True)
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE metadata (name text, value text)")
    db.execute("CREATE TABLE tiles (zoom_level integer, tile_column integer, tile_row integer, tile_data blob)")
    db.execute("CREATE UNIQUE INDEX tile_index ON tiles (zoom_level, tile_column, tile_row)")
    meta = {
        "name": f"Synthetic farm {kind}", "format": "png" if kind == "raster" else "pbf",
        "type": "baselayer" if kind == "raster" else "overlay",
        "bounds": "114.48,30.18,114.52,30.22", "center": "114.5,30.2,12",
        "minzoom": "10", "maxzoom": "14", "attribution": "Original synthetic fixture",
        "description": "Original generated data; no third-party geography",
    }
    if kind == "mvt":
        meta["json"] = json.dumps({"vector_layers": [{"id": "farms", "fields": {"name": "String", "icon": "String"}, "minzoom": 10, "maxzoom": 14}]}, separators=(",", ":"))
    db.executemany("INSERT INTO metadata VALUES (?, ?)", sorted(meta.items()))
    for z, x, y in xyz_centers():
        data = raster(z, x, y) if kind == "raster" else gzip.compress(mvt_point(z, x, y), mtime=0)
        db.execute("INSERT INTO tiles VALUES (?, ?, ?, ?)", (z, x, (1 << z) - 1 - y, data))
    db.commit()
    db.execute("VACUUM")
    db.close()
    return path


def tile_id(z, x, y):
    value = ((1 << (2 * z)) - 1) // 3
    for a in range(z - 1, -1, -1):
        s = 1 << a
        rx, ry = x & s, y & s
        value += ((3 * rx) ^ ry) << a
        if ry == 0:
            if rx:
                x, y = s - 1 - x, s - 1 - y
            x, y = y, x
    return value


def convert_pmtiles(mbtiles):
    # Public PMTiles v3 layout; fixed gzip mtime, no runtime converter dependency.
    path = mbtiles.with_suffix('.pmtiles')
    db = sqlite3.connect(mbtiles)
    rows = sorted((tile_id(z, x, (1 << z) - 1 - row), data)
        for z, x, row, data in db.execute('SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles'))
    metadata = dict(db.execute('SELECT name,value FROM metadata'))
    db.close()
    ids = [key for key, data in rows]
    offsets, offset = [], 0
    for key, data in rows:
        offsets.append(offset + 1)
        offset += len(data)
    directory = varint(len(rows))
    previous = 0
    for ident in ids:
        directory += varint(ident - previous)
        previous = ident
    directory += b''.join(varint(1) for _ in rows)
    directory += b''.join(varint(len(data)) for key, data in rows)
    directory += b''.join(varint(off) for off in offsets)
    directory = gzip.compress(directory, mtime=0)
    meta = gzip.compress(json.dumps(metadata, sort_keys=True, separators=(',', ':')).encode(), mtime=0)
    root, moff, toff = 127, 127 + len(directory), 127 + len(directory) + len(meta)
    header = b'PMTiles\x03' + struct.pack('<11Q', root, len(directory), moff, len(meta), toff, 0, toff, offset, len(rows), len(rows), len(rows))
    kind = 'mvt' if metadata['format'] == 'pbf' else 'raster'
    header += bytes((1, 2, 2 if kind == 'mvt' else 1, 1 if kind == 'mvt' else 2, 10, 14))
    header += struct.pack('<4iB2i', 1144800000, 301800000, 1145200000, 302200000, 12, 1145000000, 302000000)
    assert len(header) == 127
    with path.open('wb') as out:
        out.write(header)
        out.write(directory)
        out.write(meta)
        for key, data in rows:
            out.write(data)
    return path


def build_assets():
    asset = ROOT / "assets" / "sprite"
    asset.mkdir(parents=True, exist_ok=True)
    def icon(px, py, scale):
        qx, qy = px // scale, py // scale
        inside = 2 <= qx < 14 and 2 <= qy < 14
        roof = 5 <= qy < 8 and abs(qx - 8) <= qy - 4
        return (53, 92, 48, 255) if inside or roof else (0, 0, 0, 0)
    for ratio in (1, 2):
        size = 16 * ratio
        suffix = "@2x" if ratio == 2 else ""
        (asset / f"sprite{suffix}.png").write_bytes(png(size, size, lambda x, y: icon(x, y, ratio)))
        (asset / f"sprite{suffix}.json").write_text(json.dumps({"farm": {"width": size, "height": size, "x": 0, "y": 0, "pixelRatio": ratio}}, separators=(",", ":")), encoding="utf-8", newline='\n')


def build_styles():
    resource_base = ""
    common = {"version": 8, "center": [114.5, 30.2], "zoom": 12, "sources": {}, "layers": [
        {"id": "background", "type": "background", "paint": {"background-color": "#e2e9c8"}}]}
    raster_style = json.loads(json.dumps(common))
    raster_style["name"] = "Synthetic farm raster"
    raster_style["sources"]["basemap"] = {"type": "raster", "tiles": [resource_base + "tiles/{z}/{x}/{y}"], "tileSize": 256, "minzoom": 10, "maxzoom": 14}
    raster_style["layers"].append({"id": "basemap", "type": "raster", "source": "basemap"})
    mvt_style = json.loads(json.dumps(common))
    mvt_style["name"] = "Synthetic farm vector"
    mvt_style["glyphs"] = resource_base + "assets/glyphs/{fontstack}/{range}.pbf"
    mvt_style["sprite"] = resource_base + "assets/sprite/sprite"
    mvt_style["sources"]["farms"] = {"type": "vector", "tiles": [resource_base + "tiles/{z}/{x}/{y}"], "minzoom": 10, "maxzoom": 14}
    mvt_style["layers"].append({"id": "farm-symbols", "type": "symbol", "source": "farms", "source-layer": "farms", "layout": {
        "icon-image": "farm", "icon-size": 1, "text-field": ["get", "name"], "text-font": ["Noto Sans Regular"], "text-size": 13, "text-offset": [0, 1.5], "text-anchor": "top"},
        "paint": {"text-color": "#193c19", "text-halo-color": "#ffffff", "text-halo-width": 1}})
    for kind, style in (("raster", raster_style), ("mvt", mvt_style)):
        (OUT / f"style-{kind}.json").write_text(json.dumps(style, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline='\n')


def glyph_ids(data):
    # Independent protobuf walk: fontstack field 1, glyph field 3, glyph ID field 1.
    from io import BytesIO
    def read(s):
        n = shift = 0
        while True:
            b = s.read(1)
            if not b:
                raise ValueError('truncated protobuf')
            n |= (b[0] & 127) << shift
            if b[0] < 128:
                return n
            shift += 7
    def fields(data):
        s = BytesIO(data)
        while s.tell() < len(data):
            tag = read(s)
            if tag & 7 == 0:
                yield tag >> 3, read(s)
            elif tag & 7 == 2:
                yield tag >> 3, s.read(read(s))
            else:
                raise ValueError('unsupported glyph field')
    ids = set()
    for n, stack in fields(data):
        if n == 1:
            for k, glyph in fields(stack):
                if k == 3:
                    ids.update(v for key, v in fields(glyph) if key == 1)
    return sorted(ids)


def fetch_glyphs():
    from concurrent.futures import ThreadPoolExecutor
    from urllib.request import urlopen
    revision = '601ae60796ceceda2cbd2ed3d2ea92d17a84be4b'
    folder = ROOT / 'assets/glyphs/Noto Sans Regular'
    folder.mkdir(parents=True, exist_ok=True)
    starts = [0, 12288, 65280] + list(range(19968, 40960, 256))
    def download(start):
        name = f'{start}-{start+255}.pbf'
        path = folder / name
        if not path.exists():
            url = f'https://raw.githubusercontent.com/maplibre/demotiles/{revision}/font/Noto%20Sans%20Regular/{name}'
            with urlopen(url, timeout=30) as response:
                data = response.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError('glyph asset limit')
            glyph_ids(data)
            path.write_bytes(data)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, starts))


def main():
    import sys
    if '--fetch-glyphs' in sys.argv:
        fetch_glyphs()
    build_assets()
    build_styles()
    # Conventional single-vector style for D02 consumers.
    (ROOT / 'style.json').write_bytes((ROOT / 'style-mvt.json').read_bytes())
    results = {}
    for kind in ('raster', 'mvt'):
        mb = build_mbtiles(kind)
        pm = convert_pmtiles(mb)
        results[kind] = {'tiles': 45, 'zooms': list(range(10, 15))}
    coverage = set()
    for path in sorted((ROOT / 'assets/glyphs/Noto Sans Regular').glob('*.pbf')):
        coverage.update(glyph_ids(path.read_bytes()))
    (ROOT / 'glyph-coverage.json').write_text(json.dumps({'fontstack': 'Noto Sans Regular', 'codepoints': sorted(coverage)}, separators=(',', ':')) + '\n', encoding='utf8', newline='\n')
    manifest = {'generator': 'scripts/generate_demo_map.py', 'data_nature': 'synthetic',
        'description': 'Original synthetic Liangzi Lake agricultural demonstration; not surveyed geography',
        'center': [114.5, 30.2], 'bbox': [114.48, 30.18, 114.52, 30.22], 'min_zoom': 10, 'max_zoom': 14,
        'coverage': '3x3 XYZ tiles at every zoom 10 through 14; declared bbox is inside their intersection',
        'glyphs': {'fontstack': 'Noto Sans Regular', 'source_repository': 'https://github.com/maplibre/demotiles',
            'source_revision': '601ae60796ceceda2cbd2ed3d2ea92d17a84be4b',
            'source_path_template': 'font/Noto Sans Regular/{range}.pbf',
            'license': 'SIL Open Font License 1.1', 'coverage_file': 'glyph-coverage.json',
            'declared_blocks': ['U+0000-00FF', 'U+3000-30FF', 'U+4E00-9FFF', 'U+FF00-FFFF'],
            'available_codepoint_count': len(coverage),
            'missing_unified_ideographs': sorted(set(range(0x4e00, 0xa000)) - coverage),
            'client_option': 'localIdeographFontFamily: false'},
        'generator_environment': {'python': platform.python_version(), 'sqlite': sqlite3.sqlite_version, 'zlib': zlib.ZLIB_VERSION},
        'generator_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'files': {}, 'validation': results}
    for path in sorted(ROOT.rglob('*')):
        if path.is_file() and path.name != 'manifest.json':
            manifest['files'][path.relative_to(ROOT).as_posix()] = {'sha256': hashlib.sha256(path.read_bytes()).hexdigest(), 'bytes': path.stat().st_size}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + '\n', encoding='utf8', newline='\n')
    print(json.dumps({'packages': results, 'glyph_count': len(coverage), 'files': len(manifest['files'])}))


if __name__ == '__main__':
    main()
