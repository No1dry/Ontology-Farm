# Synthetic Liangzi Lake map fixture

All geography, agricultural names, raster pixels, MVT features, sprite artwork,
styles and generated MBTiles/PMTiles datasets are original synthetic examples.
They are not surveyed geography or production agricultural observations.
The project dedicates these generated datasets and artwork to the public domain
under [CC0 1.0](https://creativecommons.org/publicdomain/zero/1.0/).
Suggested attribution: “Liangzi Lake synthetic agricultural demonstration”.

The local `assets/glyphs/Noto Sans Regular/*.pbf` files are unmodified copies of
[MapLibre demotiles](https://github.com/maplibre/demotiles/tree/601ae60796ceceda2cbd2ed3d2ea92d17a84be4b/font/Noto%20Sans%20Regular)
at revision `601ae60796ceceda2cbd2ed3d2ea92d17a84be4b`. That project credits
OpenMapTiles fonts. Redistribute their complete adjacent
`assets/glyphs/Noto Sans Regular/LICENSE.md` (SIL Open Font License 1.1).
The exact digest and size of each PBF and license are in `manifest.json`.

PMTiles is a public-domain/CC0 specification. The bounded directory/Hilbert
implementation in `lzo/maps/tiles.py` and the fixture writer were adapted with
reference to the BSD-3-Clause PMTiles Python 3.6.0 implementation, copyright
2021 Protomaps LLC. Its full upstream license is retained in
`LICENSE-PMTILES.txt`, obtained from
`https://raw.githubusercontent.com/protomaps/PMTiles/main/LICENSE` on
2026-10-21; the captured bytes are fixed by the manifest digest. No PMTiles
Python package is needed to generate or serve these fixtures.

The glyph pack has 85 locally registered range files. `glyph-coverage.json`
records every actual Unicode glyph ID. It includes all unified CJK characters
U+4E00–U+9FD0 (20,945 characters), Latin U+0000–00FF, CJK punctuation/kana
U+3000–30FF, and fullwidth characters U+FF00–FFFF where present in the source.
The manifest lists absent U+9FD1–9FFF explicitly. CJK extensions, emoji and other
scripts are not declared covered. Consumers must detect labels outside the
recorded IDs and report unsupported glyphs. Use MapLibre
`localIdeographFontFamily: false`; no OS font or CSS fallback proves coverage.

The declared usable coverage is 114.48–114.52°E / 30.18–30.22°N, at every
zoom 10–14. Each archive includes a real 3×3 tile neighborhood at each zoom
(45 tiles), so the declared area can be panned and zoomed. Outside coverage and
missing tiles return explicit API errors. No screenshot substitutes for data.

D02 must copy this directory unchanged, including both license files, all
glyphs, styles, sprites, archives, coverage inventory and manifest. Upload the
chosen package/style and **every** used asset through F04; register their
version IDs in `asset_versions` using paths relative to `assets/`. Do not
publish this directory as an unauthenticated static mount. Generated style
references are relative tokens resolved only by the authorized resource API.

Reproduce from the repository root with Python 3.12.7:
`python scripts/generate_demo_map.py`. This uses only the standard library and
the checked-in licensed PBF inputs. To acquire missing PBF inputs from the
fixed public revision, use `--fetch-glyphs` (four bounded download workers).
`manifest.json` includes all generated file hashes and byte sizes, plus the
exact Python/SQLite/zlib generation versions. D02 packages the captured bytes;
regeneration on a different SQLite/zlib build must be revalidated for hashes.
