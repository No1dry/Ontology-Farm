"""Explicit store connections; legacy sharing is an intentional deployment mode."""
from typing import Literal
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

Domain = Literal["core", "gis", "timeseries", "catalog"]
DOMAINS = ("core", "gis", "timeseries", "catalog")

class StorageRegistry:
    def __init__(self, mode, engines):
        self.mode, self._engines = mode, engines

    @classmethod
    def from_settings(cls, settings):
        engines = {}
        try:
            engines["core"] = create_engine(settings.database_url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
            for domain in DOMAINS[1:]:
                if settings.storage_mode == "legacy":
                    engines[domain] = engines["core"]
                else:
                    url = getattr(settings, f"{domain}_database_url")
                    if not url:
                        raise ValueError("Missing split store URL")
                    engines[domain] = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 3})
            return cls(settings.storage_mode, engines)
        except Exception:
            for engine in engines.values(): engine.dispose()
            raise

    def engine(self, domain: Domain) -> Engine:
        if domain not in DOMAINS: raise ValueError("Unknown storage domain")
        return self._engines[domain]

    def close(self) -> None:
        for engine in set(self._engines.values()): engine.dispose()

    def readiness(self):
        result = {}
        for domain in DOMAINS if self.mode == "split" else ("core",):
            try:
                with self.engine(domain).connect() as connection:
                    if domain == "core":
                        version = connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                        connection.execute(text("SELECT 1 FROM store_deliveries LIMIT 1"))
                        ready = bool(version)
                    else:
                        ready = connection.execute(text("SELECT version FROM store_schema_version")).scalar_one() >= {'gis': 4, 'catalog': 3, 'timeseries': 3}[domain]
                        connection.execute(text("SELECT 1 FROM store_inbox LIMIT 1"))
                result[domain] = "ready" if ready else "migration_required"
            except Exception:
                result[domain] = "unavailable"
        return result
