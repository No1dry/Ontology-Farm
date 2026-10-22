"""Independent owner-only migrations for the three professional stores."""
import os
from pathlib import Path
from sqlalchemy import create_engine, text

DOMAINS = ("gis", "timeseries", "catalog")

def migrate_domain(domain, url):
    if domain not in DOMAINS: raise ValueError("Unknown domain")
    engine = create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("SELECT pg_advisory_xact_lock(401)"))
            if c.execute(text("SELECT current_user")).scalar_one() != f"lzo_{domain}_owner":
                raise RuntimeError("Domain migration requires its owner account")
            if not c.execute(text("SELECT to_regclass('store_schema_version')")).scalar_one():
                sql = Path(__file__).resolve().parents[4] / "database" / domain / "001_inbox.sql"
                c.execute(text(sql.read_text()))
            c.execute(text(f"GRANT SELECT,INSERT ON store_inbox TO lzo_{domain}_app"))
            c.execute(text(f"GRANT SELECT,INSERT,UPDATE ON store_heads TO lzo_{domain}_app"))
            c.execute(text(f"GRANT SELECT ON store_schema_version TO lzo_{domain}_app"))
    finally:
        engine.dispose()

def main():
    for domain in DOMAINS:
        url = os.environ.get(f"LZO_{domain.upper()}_MIGRATION_DATABASE_URL")
        if not url: raise RuntimeError(f"Missing explicit {domain} owner migration URL")
        migrate_domain(domain, url)

if __name__ == "__main__": main()
