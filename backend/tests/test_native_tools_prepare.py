"""Exercise portable archive preparation on a fresh local root."""
import hashlib
import json
import sys
from pathlib import Path
from zipfile import ZipFile

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import native_dev  # noqa: E402


def test_prepare_tools_creates_tools_and_overlays_postgis(tmp_path, monkeypatch):
    sources = tmp_path / "sources"
    sources.mkdir()
    entries = {
        "postgres": {"pgsql/bin/postgres.exe": b"postgres"},
        "postgis": {
            "bundle/share/extension/postgis.control": b"postgis",
            "bundle/bin/postgis.dll": b"extension",
        },
        "fuseki": {"apache-jena-fuseki-6.2.0/fuseki-server.jar": b"fuseki"},
        "keycloak": {"keycloak-26.8.1/bin/kc.bat": b"keycloak"},
    }
    archives = {}
    for name, files in entries.items():
        source = sources / f"{name}.zip"
        with ZipFile(source, "w") as bundle:
            for path, content in files.items():
                bundle.writestr(path, content)
        archives[name] = {"url": source.as_uri(), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}

    (tmp_path / "infra").mkdir()
    (tmp_path / "infra/versions.lock.json").write_text(json.dumps({"native": {"archives": archives}}))
    monkeypatch.setattr(native_dev, "ROOT", tmp_path)
    monkeypatch.setattr(native_dev, "TOOLS", tmp_path / ".tools")

    native_dev.prepare_tools()

    assert (tmp_path / ".tools/postgres/pgsql/bin/postgres.exe").read_bytes() == b"postgres"
    assert (tmp_path / ".tools/postgres/pgsql/share/extension/postgis.control").read_bytes() == b"postgis"
    assert (tmp_path / ".tools/postgres/pgsql/bin/postgis.dll").read_bytes() == b"extension"
    assert (tmp_path / ".tools/fuseki/apache-jena-fuseki-6.2.0/fuseki-server.jar").exists()
    assert (tmp_path / ".tools/keycloak/keycloak-26.8.1/bin/kc.bat").exists()
