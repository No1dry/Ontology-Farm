"""Real read-only PostgreSQL/MySQL sources; absence is a failure, never a skip."""
from dataclasses import replace
from uuid import uuid4
import pytest
from lzo.errors import ApiError, Conflict
from lzo.ingestion.schemas import ReadRequest, ConnectionConfig, SourceCredentialInput
from lzo.ingestion.connectors.postgresql import PostgreSQLConnector
from lzo.ingestion.connectors.mysql import MySQLConnector


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_full_and_incremental_read_use_stable_composite_cursor(database_case, backend):
    c = database_case(backend)
    assert c.connector.check(c.config).read_only
    for mode in ("full", "incremental"):
        request = c.request(mode=mode)
        first = c.connector.read_page(c.config, request)
        second = c.connector.read_page(c.config, request.model_copy(update={"cursor":first.next_cursor}))
        assert len(first.changes) == 2
        assert second.end_of_scan
        assert {x.key.canonical_key for x in first.changes + second.changes} == {'[1,2]', '[1,10]', '[2,1]'}
        assert first.source_snapshot_token == second.source_snapshot_token
        assert all(x.observed_at is None and x.valid_from is None for x in first.changes)


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_snapshot_is_held_across_pages_and_lost_connection_cannot_resume(database_case, backend):
    c = database_case(backend)
    request=c.request()
    first=c.connector.read_page(c.config,request)
    c.execute("DELETE FROM records WHERE a=2")
    second=c.connector.read_page(c.config,request.model_copy(update={"cursor":first.next_cursor}))
    assert second.changes[0].key.canonical_key == '[2,1]'
    request=request.model_copy(update={'page_size':1})
    first=c.connector.read_page(c.config,request)
    c.disconnect(first.source_snapshot_token)
    with pytest.raises(ApiError):
        c.connector.read_page(c.config,request.model_copy(update={"cursor":first.next_cursor}))


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_tls_verifies_certificate_and_hostname_without_fallback(database_case, backend):
    c=database_case(backend)
    assert c.connector.check(c.config).reachable
    wrong=c.config.model_copy(update={"host":"127.0.0.1"})
    assert not c.connector.check(wrong).reachable
    c.connector.ca_file=c.wrong_ca
    assert not c.connector.check(c.config).reachable


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_effective_writer_privileges_and_unsafe_identifiers_are_rejected(database_case, backend):
    c=database_case(backend)
    c.allow_write()
    assert not c.connector.check(c.config).read_only
    with pytest.raises(ApiError):c.connector.read_page(c.config,c.request())
    assert not c.connector.check(c.config.model_copy(update={"table_name":"records; DROP TABLE records"})).read_only


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_temporal_mapping_separates_source_business_and_cursor_clocks(database_case, backend):
    from lzo.ingestion.schemas import TemporalFields
    c=database_case(backend)
    config=c.config.model_copy(update={"temporal_fields":TemporalFields(valid_from_path="valid_from",observed_at_path="observed"),"source_timezone":"UTC"})
    page=c.connector.read_page(config,c.request(config=config,page_size=10))
    assert page.changes[0].valid_from.year==2020
    assert page.changes[0].observed_at.year==2021
    assert page.changes[0].position.source_version.startswith("2026")
    c.execute("UPDATE records SET valid_from='invalid'")
    with pytest.raises(ApiError):c.connector.read_page(config,c.request(config=config))


@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_source_credentials_are_writeonly_encrypted_and_scoped(database_case, backend, caplog):
    c=database_case(backend)
    c.assert_credentials(caplog)


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_incremental_overlap_and_full_tuple_upper_fence(database_case,backend):
    from lzo.ingestion.schemas import RuleExpr
    c=database_case(backend)
    c.execute("UPDATE records SET updated_at='2026-01-01 12:00:01' WHERE a=1 AND b=2")
    request=c.request(mode='incremental',page_size=1)
    first=c.connector.read_page(c.config,request)
    values=list(first.changes);page=first
    while page.next_cursor:
        page=c.connector.read_page(c.config,request.model_copy(update={'cursor':page.next_cursor}));values.extend(page.changes)
    assert [x.key.canonical_key for x in values]==['[1,10]','[2,1]','[1,2]']
    assert page.watermark['keys']==[1,2]
    request=request.model_copy(update={'cursor':{'watermark':page.watermark},'page_size':10})
    again=c.connector.read_page(c.config,request)
    assert {x.idempotency_key for x in again.changes}=={x.idempotency_key for x in values}
    config=c.config.model_copy(update={'filter_expression':RuleExpr(op='eq',args=(RuleExpr(op='path',path='value'),RuleExpr(op='literal',value={'value':"x' OR 1=1 --"})))})
    empty=c.connector.read_page(config,c.request(config=config))
    assert empty.end_of_scan and not empty.changes
    invalid=c.config.model_copy(update={'key_columns':('value',)})
    assert not c.connector.check(invalid).read_only


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_unknown_ordering_falls_back_and_finalization_failure_cannot_complete(database_case,backend):
    c=database_case(backend)
    fallback=c.config.model_copy(update={'updated_at_column':'value'})
    assert c.connector.check(fallback).incremental_mode=='snapshot_diff'
    request=c.request(config=fallback,page_size=1)
    first=c.connector.read_page(fallback,request)
    altered=first.next_cursor.copy();altered['after']=[900,900]
    with pytest.raises(ApiError):c.connector.read_page(fallback,request.model_copy(update={'cursor':altered}))
    # Read a real final page, then lose the real socket before COMMIT.
    query=c.connector.query
    def lose_before_commit(conn,sql,params=()):
        rows=query(conn,sql,params)
        if sql.startswith('SELECT * FROM '+c.connector.table(c.config)+' '):conn.close()
        return rows
    c.connector.query=lose_before_commit
    with pytest.raises(ApiError):c.connector.read_page(c.config,c.request(page_size=10))


def test_postgres_elevated_predefined_role_is_not_readonly(database_case):
    from psycopg import sql
    c=database_case('postgresql')
    c.admin.execute(sql.SQL('GRANT pg_execute_server_program TO {}').format(sql.Identifier(c.username)))
    assert not c.connector.check(c.config).read_only


def test_postgres_noninherited_activatable_writer_is_rejected(database_case):
    from psycopg import sql
    c=database_case('postgresql');writer='w_'+uuid4().hex[:12]
    c.admin.execute(sql.SQL('CREATE ROLE {} NOLOGIN').format(sql.Identifier(writer)))
    try:
        c.seed.execute(sql.SQL('GRANT UPDATE ON records TO {}').format(sql.Identifier(writer)))
        c.admin.execute(sql.SQL('GRANT {} TO {} WITH INHERIT FALSE, SET TRUE').format(sql.Identifier(writer),sql.Identifier(c.username)))
        conn=c.connector.connect(c.config)
        try:
            proof=c.connector.query(conn,"SELECT has_table_privilege(current_user,'records','UPDATE') AS immediate_write, pg_has_role(current_user,%s,'SET') AS can_activate",(writer,))[0]
            assert proof=={'immediate_write':False,'can_activate':True}
        finally:conn.close()
        assert not c.connector.check(c.config).read_only
        assert c.connector.check(c.config).issues[0].code=='SOURCE_ROLE_UNPROVEN'
        with pytest.raises(ApiError):c.connector.read_page(c.config,c.request())
    finally:
        c.admin.execute(sql.SQL('REVOKE {} FROM {}').format(sql.Identifier(writer),sql.Identifier(c.username)))
        c.seed.execute(sql.SQL('REVOKE UPDATE ON records FROM {}').format(sql.Identifier(writer)))
        c.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(writer)))


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_credential_resolution_rechecks_revocation(database_case,backend):
    c=database_case(backend)
    first=c.connector.read_page(c.config,c.request())
    c.s.revoke(c.s.source_grant)
    with pytest.raises(ApiError):c.connector.read_page(c.config,c.request().model_copy(update={'cursor':first.next_cursor}))
    assert not c.connector.snapshots


def test_mysql_timestamp_session_utc_is_not_reinterpreted_as_business_zone(database_case):
    c=database_case('mysql')
    c.execute("SET SESSION time_zone='+00:00'")
    c.execute('ALTER TABLE records MODIFY updated_at TIMESTAMP(6) NOT NULL')
    config=c.config.model_copy(update={'source_timezone':'Asia/Shanghai'})
    page=c.connector.read_page(config,c.request(config=config,page_size=10))
    assert all(x.position.source_version=='2026-01-01T12:00:00+00:00' for x in page.changes)


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_fence_read_failure_closes_actual_source_connection(database_case,backend):
    c=database_case(backend);opened=[];query=c.connector.query
    def fail_after_real_fence(conn,sql,params=()):
        result=query(conn,sql,params)
        if sql.startswith('SELECT '+c.connector.quote('updated_at')+','):
            opened.append(conn)
            raise RuntimeError('Injected failure after real fence query')
        return result
    c.connector.query=fail_after_real_fence
    try:
        with pytest.raises(ApiError):c.connector.read_page(c.config,c.request())
        assert len(opened)==1
        assert bool(getattr(opened[0],'closed',False)) or not getattr(opened[0],'open',True)
    finally:
        for conn in opened:
            if not getattr(conn,'closed',False) and getattr(conn,'open',True):conn.close()


def test_source_failure_does_not_break_core_ready_and_missing_key_fails_closed(database_case):
    c=database_case('postgresql')
    settings=c.s.http.app.state.settings
    c.s.http.app.state.settings=settings.model_copy(update={'source_encryption_key':None})
    try:
        response=c.s.http.post(f'/api/v1/sources/{c.source.id}/test',headers=c.s.headers())
        assert response.status_code==200 and not response.json()['reachable']
        response=c.s.http.get('/health/ready')
        assert response.status_code==200
    finally:c.s.http.app.state.settings=settings


@pytest.mark.parametrize('backend',['postgresql','mysql'])
def test_explicit_internal_trust_uses_actual_encrypted_source(database_case,backend):
    c=database_case(backend)
    config=c.config.model_copy(update={'tls_mode':'internal_trust','host':'127.0.0.1' if c.config.host=='localhost' else c.config.host})
    # No private test CA is needed for explicit internal trust; verify mode
    # continues to reject this untrusted/mismatched endpoint in its own cases.
    c.connector.ca_file=None
    assert c.connector.check(config).read_only
    page=c.connector.read_page(config,c.request(config=config,page_size=1))
    connection=c.connector.snapshots[page.source_snapshot_token].connection
    if backend=='postgresql':assert c.connector.query(connection,'SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')[0]['ssl']
    else:assert c.connector.query(connection,"SHOW SESSION STATUS LIKE 'Ssl_cipher'")[0]['Value']
    assert page.changes and not page.end_of_scan


@pytest.mark.parametrize('backend',['postgresql','mysql'])
@pytest.mark.parametrize('mode',['verify','internal_trust'])
def test_both_tls_modes_reject_actual_unencrypted_server(database_case_without_tls,backend,mode):
    c=database_case_without_tls(backend)
    if backend=='postgresql':
        assert c.seed.execute('SHOW ssl').fetchone()[0]=='off'
        assert c.seed.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()').fetchone()[0] is False
    else:
        with c.seed.cursor() as cursor:
            cursor.execute('SELECT @@tls_version');assert cursor.fetchone()[0]==''
            cursor.execute("SHOW SESSION STATUS LIKE 'Ssl_cipher'");assert cursor.fetchone()[1]==''
    config=c.config.model_copy(update={'tls_mode':mode})
    check=c.connector.check(config)
    assert not check.reachable and not check.read_only
    with pytest.raises(ApiError):c.connector.read_page(config,c.request(config=config))
    assert not c.connector.snapshots
