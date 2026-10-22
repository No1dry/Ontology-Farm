"""Real PostgreSQL and HTTP fixtures for the unified source/evidence contract."""
import json
from uuid import uuid4
import pytest
from sqlalchemy import insert, select, update
from test_artifacts import artifact_http


@pytest.fixture
def evidence_case(artifact_http):
    from lzo.auth import models
    s=artifact_http
    with s.transaction() as uow:
        for name, actions in (("source_manager", ["view","source.manage","source.read"]),
                              ("evidence_manager", ["view","evidence.manage","evidence.read"]),
                              ("evidence_reader", ["view","evidence.read"])):
            s.roles[name]=uuid4()
            uow.session.execute(insert(models.roles).values(id=s.roles[name], name=name, actions=actions, revision=1))
        uow.commit()
    s.source_grant=s.grant("alice", "source_manager", scope="farm_a")
    s.evidence_grant=s.grant("alice", "evidence_manager", scope="farm_a")
    s.grant("admin", "editor", scope="farm_b")
    s.grant("admin", "source_manager", scope="farm_b")
    s.grant("admin", "evidence_manager", scope="farm_b")
    def source(name="alice", farm="farm_a", nature="synthetic"):
        return s.http.post("/api/v1/sources", headers=s.headers(name,key=str(uuid4())), json={
            "source_system":"farm", "source_instance":"one", "collection":"pond", "kind":"file",
            "scope":{"resource_id":None,"owner_scope_id":str(s.scopes[farm]),"related_scope_ids":[],"security_class":"data"},
            "data_nature":nature})
    def evidence(source_id=None, name="alice", farm="farm_a", payload=b"original", key=None, targets=(), replaces=None, revision=1, nature=None):
        body={"source_id":source_id, "scope":{"resource_id":None,"owner_scope_id":str(s.scopes[farm]),
            "related_scope_ids":[],"security_class":"data"}, "target_refs":list(targets), "title":"Original",
            "locator":"field/one", "data_nature":nature or ("synthetic" if source_id else "imported_unverified"), "replaces_id":replaces}
        return s.http.post("/api/v1/artifacts/evidence", headers=s.headers(name,key=key or str(uuid4()),revision=revision),
            files={"file":("same.pdf",payload,"application/pdf")}, data={"metadata":json.dumps(body)})
    s.source=source; s.evidence=evidence
    return s


@pytest.fixture(scope='session')
def source_servers():
    yield from owned_source_servers(tls_enabled=True)


@pytest.fixture(scope='session')
def source_servers_without_tls():
    yield from owned_source_servers(tls_enabled=False)


def owned_source_servers(*,tls_enabled):
    """Owned native source clusters, exact process cleanup; never populated services.

    Portable tools are acquired by the existing native manifest workflow. The
    certificates/keys and synthetic account secrets stay in ignored test scratch.
    """
    import os,secrets,socket,subprocess,time,hashlib
    from pathlib import Path
    from datetime import datetime,timezone,timedelta
    from types import SimpleNamespace
    import psycopg,pymysql
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    root=Path(__file__).resolve().parents[3]
    if os.name!='nt':
        # Compose test-only source administrators; no configured external source
        # is ever used as a writable acceptance seed destination.
        ca=Path(os.environ['LZO_TEST_SOURCE_CA'])
        import ssl
        tls=ssl.create_default_context(cafile=str(ca))
        pg_host='source-postgres' if tls_enabled else 'source-postgres-no-tls'
        mysql_host='source-mysql' if tls_enabled else 'source-mysql-no-tls'
        secret_suffix='' if tls_enabled else '_NO_TLS'
        yield SimpleNamespace(pg=lambda:psycopg.connect(host=pg_host,dbname='synthetic_source',user='postgres',
            password=os.environ['SOURCE_POSTGRES_PASSWORD'+secret_suffix],autocommit=True,sslmode='verify-full' if tls_enabled else 'disable',sslrootcert=str(ca)),
            mysql=lambda:pymysql.connect(host=mysql_host,user='root',password=os.environ['SOURCE_MYSQL_PASSWORD'+secret_suffix],autocommit=True,ssl=tls if tls_enabled else None,ssl_disabled=not tls_enabled),
            pg_port=5432,mysql_port=3306,ca=ca,wrong_ca=Path(os.environ['LZO_TEST_SOURCE_WRONG_CA']),
            pg_host=pg_host,mysql_host=mysql_host)
        return
    work=root/'.runtime'/('i03-src-'+uuid4().hex[:8]);work.mkdir()
    def port():
        with socket.socket() as s:s.bind(('127.0.0.1',0));return s.getsockname()[1]
    pg_port,mysql_port=port(),port()
    def private(key,path):
        path.write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.TraditionalOpenSSL,serialization.NoEncryption()))
    now=datetime.now(timezone.utc)
    ca_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'I03 isolated test CA')])
    ca=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(ca_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(ca_key,hashes.SHA256())
    (work/'ca.pem').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    cert=x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2)).add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(ca_key,hashes.SHA256())
    (work/'server.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));private(key,work/'server-key.pem')
    wrong_key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
    wrong=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(wrong_key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(days=1)).not_valid_after(now+timedelta(days=2)).add_extension(x509.BasicConstraints(ca=True,path_length=None),critical=True).sign(wrong_key,hashes.SHA256())
    (work/'wrong-ca.pem').write_bytes(wrong.public_bytes(serialization.Encoding.PEM))
    tools=Path(os.environ.get('LZO_TEST_TOOL_ROOT', root/'.tools')).resolve()
    pg=tools/'postgres/pgsql/bin';mysql=tools/'mysql/mysql-8.4.11-winx64/bin/mysqld.exe'
    assert hashlib.sha256(mysql.read_bytes()).hexdigest()=='b83561f885785133b79cfe53f225dffbdbdec160ea54b5245cfb30cc7a440fa7'
    secret=secrets.token_urlsafe(32);(work/'pg-password').write_text(secret)
    flags=subprocess.CREATE_NO_WINDOW
    def run(args):
        with (work/'initialize.log').open('ab') as log:
            done=subprocess.run(list(map(str,args)),stdout=log,stderr=log,timeout=120,creationflags=flags)
            assert done.returncode==0
    run([pg/'initdb.exe','-D',work/'pg','--username=postgres','--pwfile',work/'pg-password','--auth=scram-sha-256','--no-locale','--encoding=UTF8'])
    (work/'pg-password').unlink()
    with (work/'pg/postgresql.conf').open('a') as f:
        f.write(f"\nlisten_addresses='127.0.0.1,::1'\nport={pg_port}\nssl={'on' if tls_enabled else 'off'}\nssl_cert_file='{(work/'server.pem').as_posix()}'\nssl_key_file='{(work/'server-key.pem').as_posix()}'\n")
    run([mysql,'--no-defaults','--initialize-insecure','--basedir='+str(mysql.parent.parent),'--datadir='+str(work/'mysql')])
    processes=[];logs=[];metadata={'work':str(work),'pg_port':pg_port,'mysql_port':mysql_port,'tls_enabled':tls_enabled,'processes':[]}
    def launch(args,label):
        log=(work/(label+'.log')).open('ab');logs.append(log)
        p=subprocess.Popen(list(map(str,args)),stdout=log,stderr=log,stdin=subprocess.DEVNULL,creationflags=flags)
        processes.append((label,p));metadata['processes'].append({'label':label,'pid':p.pid});return p
    try:
        launch([pg/'postgres.exe','-D',work/'pg'],'postgres')
        tls_options=['--ssl-ca='+str(work/'ca.pem'),'--ssl-cert='+str(work/'server.pem'),
            '--ssl-key='+str(work/'server-key.pem'),'--require-secure-transport=ON'] if tls_enabled else ['--tls-version=','--require-secure-transport=OFF']
        launch([mysql,'--no-defaults','--basedir='+str(mysql.parent.parent),'--datadir='+str(work/'mysql'),
            '--bind-address=127.0.0.1,::1','--port='+str(mysql_port),'--mysqlx=0',*tls_options],'mysql')
        def pg_admin():return psycopg.connect(host='localhost',port=pg_port,dbname='postgres',user='postgres',password=secret,autocommit=True,connect_timeout=3,sslmode='verify-full' if tls_enabled else 'disable',sslrootcert=str(work/'ca.pem'))
        import ssl
        tls=ssl.create_default_context(cafile=str(work/'ca.pem'))
        def mysql_admin():return pymysql.connect(host='localhost',port=mysql_port,user='root',password='',autocommit=True,ssl=tls if tls_enabled else None,ssl_disabled=not tls_enabled,connect_timeout=3)
        for connect in (pg_admin,mysql_admin):
            deadline=time.monotonic()+60
            while True:
                try:
                    conn=connect();conn.close();break
                except Exception:
                    if time.monotonic()>deadline:raise RuntimeError('Owned source server did not become ready') from None
                    time.sleep(.2)
        metadata['ready']=True;(work/'lifecycle.json').write_text(json.dumps(metadata,indent=2))
        yield SimpleNamespace(pg=pg_admin,mysql=mysql_admin,pg_port=pg_port,mysql_port=mysql_port,
            pg_host='localhost',mysql_host='localhost',ca=work/'ca.pem',wrong_ca=work/'wrong-ca.pem')
    finally:
        for label,p in reversed(processes):
            if p.poll() is None:
                if label=='postgres':run([pg/'pg_ctl.exe','-D',work/'pg','-m','fast','-w','stop'])
                else:
                    try:
                        conn=mysql_admin()
                        with conn.cursor() as cursor:cursor.execute('SHUTDOWN')
                        conn.close()
                    except Exception:pass
                try:p.wait(timeout=20)
                except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=10)
            metadata[label+'_exit']=p.returncode
        for log in logs:log.close()
        metadata['stopped']=True;(work/'lifecycle.json').write_text(json.dumps(metadata,indent=2))


@pytest.fixture
def database_case_without_tls(evidence_case,source_servers_without_tls):
    yield from database_case.__wrapped__(evidence_case,source_servers_without_tls)


@pytest.fixture
def database_case(evidence_case,source_servers):
    from types import SimpleNamespace
    import secrets,base64,os
    from datetime import datetime,timezone,timedelta
    from lzo.contracts import ResourceScope
    from lzo.ingestion.schemas import SourceCreate,SourceCredentialInput,ConnectionConfig,ReadRequest,SourceChange,ScheduleCommand
    from lzo.ingestion.sources import SourceService,ConnectionService,current_source_change
    from lzo.ingestion.credentials import SourceCredentialStore
    from lzo.ingestion.connectors.base import scope_digest
    from lzo.ingestion.sync import connector_for,SyncService
    from lzo.ingestion.models import source_credentials,source_watermarks,source_records,source_positions,scan_members,source_scans
    from lzo.auth import models as auth_models
    from lzo.worker.runner import Runner
    from lzo.worker.service import enqueue,register_handler
    from lzo.worker.models import jobs
    from lzo.errors import ApiError,Conflict
    s=evidence_case;cases=[]
    with s.transaction() as uow:
        role=uuid4();uow.session.execute(insert(auth_models.roles).values(id=role,name='source-secrets',actions=['view','sources.credentials.manage'],revision=1))
        s.roles['source-secrets']=role;uow.commit()
    s.grant('alice','source-secrets',scope='farm_a')
    settings=s.http_settings.model_copy(update={'source_encryption_key':base64.urlsafe_b64encode(os.urandom(32)).decode(),
        'source_ca_bundle':source_servers.ca,'job_max_batches_per_run':500,'job_lease_seconds':120})
    s.http.app.state.settings=settings
    def create(backend):
        name='i03_'+uuid4().hex[:12];username='r_'+uuid4().hex[:12];password=secrets.token_urlsafe(28)
        account_host='localhost' if os.name=='nt' else '%'
        admin=(source_servers.pg if backend=='postgresql' else source_servers.mysql)()
        if backend=='postgresql':
            from psycopg import sql
            admin.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
            admin.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(username),sql.Literal(password)))
            import psycopg
            # Reuse the owned source admin connection parameters, changing only DB.
            params=admin.info.get_parameters();params['dbname']=name
            seed=psycopg.connect(**params,password=admin.info.password,autocommit=True)
            seed.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
            seed.execute('CREATE TABLE records(a INTEGER NOT NULL,b INTEGER NOT NULL, updated_at TIMESTAMPTZ NOT NULL, deleted BOOLEAN NOT NULL, value TEXT,valid_from TEXT,observed TEXT,PRIMARY KEY(a,b))')
            seed.execute(sql.SQL('GRANT USAGE ON SCHEMA public TO {}').format(sql.Identifier(username)))
            seed.execute(sql.SQL('GRANT SELECT ON records TO {}').format(sql.Identifier(username)))
        else:
            with admin.cursor() as cur:
                cur.execute(f'CREATE DATABASE `{name}`')
                cur.execute('CREATE USER %s@%s IDENTIFIED BY %s',(username,account_host,password))
                cur.execute(f'GRANT SELECT ON `{name}`.* TO %s@%s',(username,account_host))
                cur.execute(f'USE `{name}`')
                cur.execute('CREATE TABLE records(a INTEGER NOT NULL,b INTEGER NOT NULL,updated_at DATETIME(6) NOT NULL,deleted BOOLEAN NOT NULL,value TEXT,valid_from TEXT,observed TEXT,PRIMARY KEY(a,b)) ENGINE=InnoDB')
            seed=admin
        def execute(statement,params=()):
            with seed.cursor() as cur:cur.execute(statement,params)
        for a,b in ((1,2),(1,10),(2,1)):
            execute('INSERT INTO records VALUES(%s,%s,%s,%s,%s,%s,%s)',(a,b,'2026-01-01 12:00:00',False,'original','2020-01-01T00:00:00Z','2021-01-01T00:00:00Z'))
        actor=s.actor('alice')
        with s.transaction() as uow:
            source=SourceService(uow).create(uow,actor,SourceCreate(source_system='acceptance',source_instance=name,collection='records',
                kind=backend,scope=ResourceScope(None,s.scopes['farm_a'],(),'data'),data_nature='synthetic'))
            credential=SourceCredentialStore(settings).set(uow,actor,source.id,SourceCredentialInput(username=username,password=password,expected_revision=1))
            config=ConnectionConfig(source_id=source.id,expected_revision=2,host=getattr(source_servers,'pg_host' if backend=='postgresql' else 'mysql_host'),
                port=source_servers.pg_port if backend=='postgresql' else source_servers.mysql_port,database=name,
                schema_name='public' if backend=='postgresql' else None,table_name='records',credential_ref=credential.ref,tls_mode='verify',
                key_columns=('a','b'),updated_at_column='updated_at',deleted_column='deleted',source_timezone='UTC')
            config=ConnectionService(uow).configure(uow,actor,config);uow.commit()
        class Credentials:
            def authorize(self):
                with s.transaction() as uow:return SourceService(uow).read(s.actor('alice'),source.id,'source.manage').id
            def resolve(self,ref):
                with s.transaction() as uow:return SourceCredentialStore(settings,uow,authorize=self.authorize).resolve(ref)
        with s.transaction() as uow:source=SourceService(uow).read(actor,source.id)
        connector=connector_for(source,Credentials(),s.store,settings)
        c=SimpleNamespace(backend=backend,source=source,config=config,connector=connector,execute=execute,wrong_ca=source_servers.wrong_ca,
            s=s,settings=settings,admin=admin,seed=seed,name=name,username=username,password=password,account_host=account_host)
        c.request=lambda mode='full',config=config,page_size=2:ReadRequest(source_id=source.id,mode=mode,page_size=page_size,scope_digest=scope_digest(config))
        c.disconnect=lambda token:connector.snapshots[token].connection.close()
        def allow_write():
            if backend=='postgresql':seed.execute(sql.SQL('GRANT UPDATE ON records TO {}').format(sql.Identifier(username)))
            else:
                with admin.cursor() as cur:cur.execute(f'GRANT UPDATE ON `{name}`.* TO %s@%s',(username,account_host))
        c.allow_write=allow_write
        def assert_credentials(caplog):
            assert connector.check(config).read_only
            with s.transaction() as uow:
                raw=uow.session.execute(select(source_credentials.c.ciphertext).where(source_credentials.c.ref==credential.ref)).scalar_one()
                assert password.encode() not in raw and username.encode() not in raw
                wrong=settings.model_copy(update={'source_encryption_key':base64.urlsafe_b64encode(os.urandom(32)).decode()})
                with pytest.raises(ApiError):SourceCredentialStore(wrong,uow,authorize=lambda:source.id).resolve(credential.ref)
                with pytest.raises(ApiError):SourceCredentialStore(settings,uow,authorize=lambda:uuid4()).resolve(credential.ref)
            response=s.http.get(f'/api/v1/sources/{source.id}/connection',headers=s.headers())
            assert response.status_code==200
            assert password not in response.text
            key=str(uuid4());body={'username':username,'password':password,'expected_revision':3}
            response=s.http.put(f'/api/v1/sources/{source.id}/credentials',headers=s.headers(key=key,revision=3),json=body)
            assert response.status_code==200
            assert password not in response.text
            replay=s.http.put(f'/api/v1/sources/{source.id}/credentials',headers=s.headers(key=key,revision=3),json=body)
            assert replay.status_code==200
            changed=s.http.put(f'/api/v1/sources/{source.id}/credentials',headers=s.headers(key=key,revision=3),json={**body,'password':'changed-secret'})
            assert changed.status_code==409
            denied=s.http.put(f'/api/v1/sources/{source.id}/credentials',headers=s.headers('admin',key=str(uuid4()),revision=4),json={**body,'expected_revision':4})
            assert denied.status_code in (403,404)
            from lzo.governance.models import revisions
            with s.transaction() as uow:
                history=uow.session.execute(select(revisions.c.payload).where(revisions.c.resource_id==source.id)).scalars().all()
                assert password not in json.dumps(history) and raw.hex() not in json.dumps(history)
            assert password not in caplog.text
            schema=s.http.get('/openapi.json').json()['components']['schemas']['SourceCredentialInput']
            assert schema['properties']['password']['writeOnly'] is True
        c.assert_credentials=assert_credentials
        cases.append(c);return c
    yield create
    for c in reversed(cases):
        c.connector.close()
        if c.backend=='postgresql':
            from psycopg import sql
            c.seed.close()
            c.admin.execute(sql.SQL('DROP DATABASE {} WITH (FORCE)').format(sql.Identifier(c.name)))
            c.admin.execute(sql.SQL('DROP ROLE {}').format(sql.Identifier(c.username)))
        else:
            with c.admin.cursor() as cur:
                cur.execute(f'DROP DATABASE `{c.name}`');cur.execute('DROP USER %s@%s',(c.username,c.account_host))
        c.admin.close()


@pytest.fixture
def scan_case(database_case):
    from datetime import datetime,timezone,timedelta
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from lzo.ingestion.sync import SyncService,connector_for
    from lzo.ingestion.sources import SourceService,current_source_change
    from lzo.ingestion.models import source_records,source_scans,source_watermarks,source_positions
    from lzo.ingestion.schemas import SourceChange,ScheduleCommand
    from lzo.ingestion.scheduler import Scheduler,ScheduleService
    from lzo.worker.service import enqueue,register_handler
    from lzo.worker.runner import Runner
    from lzo.worker.models import jobs
    from lzo.errors import Conflict
    def create(backend):
        c=database_case(backend);s=c.s
        def run(condition=None,mode='full'):
            results=[]
            request=c.request(mode=mode,config=c.config,page_size=1)
            if condition=='scope_changed':
                from lzo.ingestion.sources import ConnectionService
                from lzo.ingestion.schemas import RuleExpr
                with s.transaction() as uow:
                    source=SourceService(uow).read(s.actor('alice'),c.source.id)
                    updated=c.config.model_copy(update={'expected_revision':source.revision,'filter_expression':RuleExpr(op='eq',args=(RuleExpr(op='path',path='a'),RuleExpr(op='literal',value={'value':1})))})
                    c.config=ConnectionService(uow).configure(uow,s.actor('alice'),updated);uow.commit()
                request=c.request(mode=mode,config=c.config,page_size=1)
            def factory(*args):
                conn=connector_for(*args);original=conn.read_page;count=0
                def page(config,request):
                    nonlocal count
                    result=original(config,request);count+=1
                    if count==1 and getattr(c,'after_first_page',None):c.after_first_page()
                    if count==1 and condition in ('page_failure','snapshot_lost') and result.next_cursor:
                        conn.snapshots[result.source_snapshot_token].connection.close()
                        if condition=='snapshot_lost':conn.snapshots.pop(result.source_snapshot_token)
                    if count==1 and condition=='partial_keys':result=result.model_copy(update={'seen_keys':()})
                    return result
                conn.read_page=page
                return conn
            failures=[]
            def handler(ctx):
                if condition=='final_batch_timeout':
                    from contextlib import contextmanager
                    from sqlalchemy import text,func
                    original_batch=ctx.batch
                    @contextmanager
                    def batch():
                        with original_batch():
                            yield ctx
                            if ctx.cursor.get('state')=='complete':
                                c.tentative_final={
                                    'members':ctx.uow.session.execute(select(func.count()).select_from(scan_members).where(scan_members.c.scan_id==UUID(ctx.cursor['scan_id']))).scalar_one(),
                                    'watermark':ctx.uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==c.source.id)).scalar_one()['watermark']}
                                # Each real query stays below statement/idle timeout;
                                # their combined duration triggers F05's actual final
                                # elapsed-time check AFTER tentative final-page work.
                                for _ in range(2):ctx.uow.session.execute(text('SELECT pg_sleep(1.1)'))
                    from uuid import UUID
                    from lzo.ingestion.models import scan_members
                    ctx.batch=batch
                try:results.append(SyncService(ctx,connector_factory=factory).scan(ctx.actor,request,ctx.job_id,ctx.lease_token))
                except Exception as error:
                    import traceback
                    failures.append((type(error).__name__,[(f.name,f.lineno) for f in traceback.extract_tb(error.__traceback__)]))
                    raise
            kind='test.i03.'+uuid4().hex
            register_handler(kind,handler,action='source.manage')
            with s.transaction() as uow:
                source=SourceService(uow).read(s.actor('alice'),c.source.id)
                ref=enqueue(uow,s.actor('alice'),kind,{'resource_id':str(c.source.id),'configuration_revision':source.connection_revision,
                    'mapping_version_id':str(source.mapping_version_id) if source.mapping_version_id else None},None)
                uow.commit()
            runner=Runner(s.app,c.settings)
            for _ in range(100):
                assert runner.run_once()
                if results or failures:break
            if not results:
                with s.transaction() as uow:row=uow.session.execute(select(jobs.c.error_code).where(jobs.c.id==ref.id)).scalar_one()
                raise AssertionError('Scan handler failed: '+str(row)+' '+str(failures))
            return results[0]
        c.run=run
        def deletions(current,previous):
            with s.transaction() as uow:
                result=SyncService(uow=uow).deletions(s.actor('alice'),current.id,previous.id);uow.commit();return result
        c.deletions=deletions
        def assert_no_deletion_side_effect(previous):
            with s.transaction() as uow:
                records=uow.session.execute(select(source_records.c.payload)).scalars().all()
                assert all(r['operation']=='upsert' for r in records)
                positions=uow.session.execute(select(source_positions.c.payload)).scalars().all()
                assert all(r['operation']=='upsert' for r in positions)
                water=uow.session.execute(select(source_watermarks.c.payload).where(source_watermarks.c.source_id==c.source.id)).scalar_one()
                if water.get('last_complete_scan')==str(previous.id):assert water['watermark']==previous.last_success_watermark
        c.assert_no_deletion_side_effect=assert_no_deletion_side_effect
        def assert_source_ordering():
            first=run()
            c.execute("UPDATE records SET updated_at='2026-03-01 12:00:00',value='new'")
            latest=run()
            with s.transaction() as uow:
                original=SourceChange.model_validate(uow.session.execute(select(source_records.c.payload).where(source_records.c.scan_id==first.id)).scalars().first())
                selected=current_source_change(uow,original)
                assert selected.raw_fields['value']=='new'
            c.execute("UPDATE records SET updated_at='2026-02-01 12:00:00',value='late old'")
            late=run()
            assert late.last_success_watermark==latest.last_success_watermark
            with s.transaction() as uow:
                from lzo.ingestion.rulings import _current_change
                assert current_source_change(uow,original)==selected
                assert _current_change(uow,original)==selected
                assert len(uow.session.execute(select(source_records.c.id)).all())==9
            c.execute("UPDATE records SET updated_at='2026-03-01 12:00:00',value='different equal position'")
            run()
            with s.transaction() as uow:
                with pytest.raises(ValueError):current_source_change(uow,original)
        c.assert_source_ordering=assert_source_ordering
        def assert_scheduler_contention():
            with s.transaction() as uow:
                schedule=ScheduleService(uow).configure(uow,s.actor('alice'),ScheduleCommand(source_id=c.source.id,expected_revision=3,enabled=True,interval_seconds=60));uow.commit()
            barrier=Barrier(2)
            def tick():barrier.wait(timeout=10);return Scheduler(s.app,c.settings).tick(schedule.next_run_at+timedelta(seconds=1))
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures=[pool.submit(tick) for _ in range(2)];results=[f.result(timeout=20) for f in futures]
            assert sum(map(len,results))==1
            with s.transaction() as uow:
                row=uow.session.execute(select(jobs)).mappings().one()
                assert row['initiator_id']==s.users['alice'] and row['payload']['configuration_revision']==3
                assert row['payload']['schedule_revision']==4
        c.assert_scheduler_contention=assert_scheduler_contention
        return c
    return create
