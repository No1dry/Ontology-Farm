"""Dedicated owner-credential migration entry point."""
import os
from pathlib import Path

from alembic import command
from alembic.config import Config


def main():
    url = os.environ.get("LZO_MIGRATION_DATABASE_URL")
    if not url:
        raise RuntimeError("LZO_MIGRATION_DATABASE_URL is required for migrations")
    config = Config(str(Path(__file__).resolve().parents[3] / "database/alembic.ini"))
    config.attributes["connection_url"] = url
    command.upgrade(config, "head")
    if os.environ.get("LZO_STORAGE_MODE", "legacy") == "split":
        from lzo.storage.migrate import main as migrate_domains
        migrate_domains()


if __name__ == "__main__":
    main()
