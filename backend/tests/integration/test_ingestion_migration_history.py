"""A disposable schema proves downgrade cannot erase independent I04 history."""
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
from uuid import uuid4
import re
import pytest
from sqlalchemy import create_engine,text
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize('history',['semantic_geometry_supports','ingestion_source_evaluations'])
def test_downgrade_refuses_history_without_a_preview(scope_scenario,settings,history):
    s=scope_scenario
    with s.transaction() as uow:schema=uow.session.execute(text('SELECT current_schema()')).scalar_one()
    assert re.fullmatch(r'f03_[0-9a-f]{32}',schema)
    path=Path(__file__).parents[3]/'database/migrations/versions/0204_ingestion_runs_rows.py'
    spec=spec_from_file_location('i04_migration',path);module=module_from_spec(spec);spec.loader.exec_module(module)
    owner=create_engine(settings.migration_database_url)
    try:
        with owner.connect() as conn:
            transaction=conn.begin()
            try:
                conn.execute(text(f'SET LOCAL search_path TO "{schema}"'))
                if history=='semantic_geometry_supports':
                    conn.execute(text("INSERT INTO semantic_geometry_supports(id,resource_id,payload,active,revision_ref) "
                        "VALUES (:id,:resource,'{}',false,'{}')"),{'id':uuid4(),'resource':uuid4()})
                else:
                    conn.execute(text("INSERT INTO ingestion_source_evaluations(id,payload) VALUES (:id,'{}')"),
                        {'id':s.ids_by_name['object_a']})
                with Operations.context(MigrationContext.configure(conn)):
                    with pytest.raises(RuntimeError,match='history requires'):module.downgrade()
                assert conn.execute(text(f'SELECT count(*) FROM {history}')).scalar_one()==1
            finally:transaction.rollback()
    finally:owner.dispose()
