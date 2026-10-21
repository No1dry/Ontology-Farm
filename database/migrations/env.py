"""Migration connection is supplied only by owner-only tooling or test fixtures."""
import re

from alembic import context
from sqlalchemy import create_engine, text

from lzo.governance.models import metadata

config = context.config
target_metadata = metadata


def run_migrations_online():
    url = config.attributes.get("connection_url")
    if not url:
        raise RuntimeError("Owner migration URL must be supplied by migration tooling")
    schema = config.attributes.get("schema", "public")
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", schema):
        raise ValueError("Invalid migration schema")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            connection.execute(text(f'SET search_path TO "{schema}"'))
            connection.commit()
            context.configure(connection=connection, target_metadata=target_metadata,
                              version_table_schema=schema, transactional_ddl=True)
            with context.begin_transaction():
                context.run_migrations()
    finally:
        engine.dispose()


run_migrations_online()
