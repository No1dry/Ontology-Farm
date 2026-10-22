"""Independent owner-only migrations for the three professional stores."""
import os
from pathlib import Path
from sqlalchemy import create_engine, text
from lzo.synchronization.contracts import SyncEnvelope

DOMAINS = ("gis", "timeseries", "catalog")

def _upgrade_aggregate_content(connection, domain, core_url):
    # Block old/new writers while deriving the identity of previously accepted
    # heads. The complete original envelope is available only in the core ledger.
    connection.execute(text("LOCK TABLE store_heads IN SHARE ROW EXCLUSIVE MODE"))
    rows = connection.execute(text("""SELECT h.*,i.envelope_hash FROM store_heads h
      JOIN store_inbox i ON i.event_id=h.event_id""")).mappings().all()
    if rows and not core_url:
        raise RuntimeError("Existing heads require explicit core URL for verified aggregate-content backfill")
    sql = Path(__file__).resolve().parents[4] / "database" / domain / "002_aggregate_content.sql"
    connection.execute(text(sql.read_text()))
    core = create_engine(core_url) if rows else None
    try:
        if core is not None:
            with core.connect() as source:
                for row in rows:
                    wire = source.execute(text("SELECT envelope FROM store_deliveries WHERE event_id=:id AND target=:target"),
                                          {"id":row["event_id"],"target":domain}).scalar_one_or_none()
                    if wire is None:
                        raise RuntimeError("Original core envelope missing; aggregate-content upgrade aborted")
                    envelope = SyncEnvelope.model_validate(wire)
                    if (envelope.envelope_hash != row["envelope_hash"] or envelope.payload_hash != row["payload_hash"]
                            or envelope.entity_id != row["entity_id"] or envelope.aggregate_revision != row["aggregate_revision"]):
                        raise RuntimeError("Original envelope does not match accepted domain head; upgrade aborted")
                    connection.execute(text("UPDATE store_heads SET aggregate_content_hash=:digest WHERE entity_id=:entity"),
                                       {"digest":envelope.aggregate_content_hash,"entity":row["entity_id"]})
        connection.execute(text("ALTER TABLE store_heads ALTER COLUMN aggregate_content_hash SET NOT NULL"))
        connection.execute(text("UPDATE store_schema_version SET version=2 WHERE version=1"))
    finally:
        if core is not None: core.dispose()


def migrate_domain(domain, url, core_url=None):
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
            version = c.execute(text("SELECT version FROM store_schema_version")).scalar_one()
            if version == 1:
                _upgrade_aggregate_content(c, domain, core_url)
                version = 2
            if domain == "gis" and version == 2:
                sql = Path(__file__).resolve().parents[4] / "database/gis/003_geometry.sql"
                c.execute(text(sql.read_text()))
                version = 3
            if domain == "gis" and version == 3:
                sql = Path(__file__).resolve().parents[4] / "database/gis/004_assets_locations.sql"
                c.execute(text(sql.read_text()))
            if domain == "catalog" and version == 2:
                sql = Path(__file__).resolve().parents[4] / "database/catalog/003_catalog.sql"
                c.execute(text(sql.read_text()))
            if domain == "timeseries" and version == 2:
                sql = Path(__file__).resolve().parents[4] / "database/timeseries/003_telemetry.sql"
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
        migrate_domain(domain, url, core_url=os.environ.get("LZO_DATABASE_URL"))

if __name__ == "__main__": main()
