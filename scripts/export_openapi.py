"""Export the actual application contract without network/database access."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend/src"))
from lzo.config import Settings
from lzo.main import create_app


def export():
    settings = Settings(database_url="postgresql+psycopg://unused:unused@localhost/unused",
        fuseki_url="http://localhost/unused", oidc_issuer="https://localhost/identity/realms/unused",
        oidc_discovery_url="http://localhost/unused", file_root=ROOT / ".runtime/export-unused")
    app = create_app(settings)
    try:
        return json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    finally:
        app.state.engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=ROOT / "contracts/openapi.json")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    content = export()
    if args.check:
        if args.output.read_text(encoding="utf-8") != content:
            raise SystemExit("OpenAPI snapshot differs; regenerate from this source")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content, encoding="utf-8", newline="\n")
