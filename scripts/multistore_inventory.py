"""Export safe physical schema evidence from the explicitly configured stores."""
import argparse
import json
from pathlib import Path
from sqlalchemy import create_engine, text
from lzo.config import Settings


def inventory():
    config = Settings()
    output = {}
    for domain in ('core', 'gis', 'timeseries', 'catalog'):
        url = config.database_url if domain == 'core' else getattr(config, domain+'_database_url')
        engine = create_engine(url)
        try:
            with engine.connect() as connection:
                database, schema, version = connection.execute(text('SELECT current_database(),current_schema(),version()')).one()
                tables = connection.execute(text("SELECT tablename FROM pg_tables WHERE schemaname=current_schema() ORDER BY tablename")).scalars().all()
                columns = [dict(row) for row in connection.execute(text("SELECT table_name,column_name,data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=current_schema() ORDER BY table_name,ordinal_position")).mappings()]
                constraints = [dict(row) for row in connection.execute(text("SELECT c.relname AS table_name,p.conname AS name,p.contype AS type,pg_get_constraintdef(p.oid) AS definition FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() ORDER BY c.relname,p.conname")).mappings()]
                indexes = [dict(row) for row in connection.execute(text("SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname=current_schema() ORDER BY tablename,indexname")).mappings()]
                head = connection.execute(text('SELECT version_num FROM alembic_version' if domain == 'core' else 'SELECT version FROM store_schema_version')).scalar_one()
                output[domain] = {'database': database, 'schema': schema, 'server_version': version,
                    'schema_version': head, 'tables': tables, 'columns': columns, 'constraints': constraints, 'indexes': indexes}
        finally:
            engine.dispose()
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(inventory(), ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    print('Four-store schema metadata exported; no connection URLs or row payloads')
