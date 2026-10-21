"""PostgreSQL snapshot adapter; no writes used to discover privileges."""
import psycopg
from lzo.errors import ApiError
from lzo.ingestion.connectors.base import DatabaseConnector


class PostgreSQLConnector(DatabaseConnector):
    backend='postgresql'
    delimiter='"'

    def connect(self,config):
        username,password=self.credentials.resolve(config.credential_ref)
        args=dict(host=config.host,port=config.port,dbname=config.database,user=username,password=password.get_secret_value(),
            connect_timeout=5,autocommit=True,sslmode='verify-full' if config.tls_mode=='verify' else 'require',
            options='-c statement_timeout=10000 -c idle_in_transaction_session_timeout=130000')
        if self.ca_file:args['sslrootcert']=str(self.ca_file)
        conn=psycopg.connect(**args)
        try:
            conn.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
            return conn
        except BaseException:conn.close();raise

    def metadata(self,conn,config):
        # A non-inherited membership may still permit SET ROLE (or changes to
        # membership options). Until that entire closure is proven, support
        # directly granted reader accounts only, not unproven role accounts.
        memberships=self.query(conn,"SELECT rolname FROM pg_roles WHERE rolname<>current_user AND pg_has_role(current_user,oid,'MEMBER')")
        if memberships:raise ApiError('SOURCE_ROLE_UNPROVEN',422,'Source role membership has an unproven privilege closure')
        schema=config.schema_name or 'public'
        columns={r['column_name']:{'type':r['data_type'],'nullable':r['is_nullable']=='YES'} for r in self.query(conn,
            'SELECT column_name,data_type,is_nullable FROM information_schema.columns WHERE table_schema=%s AND table_name=%s',(schema,config.table_name))}
        primary=[r['attname'] for r in self.query(conn,"""SELECT a.attname FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid
            JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY k(attnum,pos)
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum=k.attnum
            WHERE n.nspname=%s AND c.relname=%s AND i.indisprimary ORDER BY k.pos""",(schema,config.table_name))]
        risky=self.query(conn,"""SELECT EXISTS(SELECT 1 FROM pg_roles r WHERE pg_has_role(current_user,r.oid,'MEMBER')
            AND (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls
                OR r.rolname IN ('pg_write_all_data','pg_write_server_files','pg_execute_server_program','pg_signal_backend','pg_checkpoint')))
            OR EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
              WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname NOT LIKE 'pg_toast%%'
              AND c.relkind IN ('r','p','v','m','f') AND (pg_has_role(current_user,c.relowner,'MEMBER')
                OR has_table_privilege(current_user,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')
                OR has_any_column_privilege(current_user,c.oid,'INSERT,UPDATE,REFERENCES')))
            OR EXISTS(SELECT 1 FROM pg_namespace n WHERE n.nspname NOT LIKE 'pg_%%' AND n.nspname<>'information_schema'
                AND (has_schema_privilege(current_user,n.oid,'CREATE') OR pg_has_role(current_user,n.nspowner,'MEMBER')))
            OR has_database_privilege(current_user,current_database(),'CREATE')
            OR EXISTS(SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                WHERE p.prosecdef AND n.nspname NOT IN ('pg_catalog','information_schema') AND has_function_privilege(current_user,p.oid,'EXECUTE')) AS risky""")[0]['risky']
        return columns,primary,bool(columns and primary and not risky)
