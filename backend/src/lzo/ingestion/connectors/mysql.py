"""MySQL InnoDB repeatable-read, consistent read-only snapshot adapter."""
import ssl
import pymysql
from lzo.ingestion.connectors.base import DatabaseConnector


class MySQLConnector(DatabaseConnector):
    backend='mysql'
    delimiter='`'

    def connect(self,config):
        username,password=self.credentials.resolve(config.credential_ref)
        context=ssl.create_default_context(cafile=str(self.ca_file) if self.ca_file else None)
        if config.tls_mode=='internal_trust':context.check_hostname=False;context.verify_mode=ssl.CERT_NONE
        conn=pymysql.connect(host=config.host,port=config.port,database=config.database,user=username,password=password.get_secret_value(),
            ssl=context,autocommit=True,charset='utf8mb4',connect_timeout=5,read_timeout=10,write_timeout=10,local_infile=False)
        try:
            self.query(conn,"SET SESSION time_zone = '+00:00'")
            self.query(conn,'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            self.query(conn,'START TRANSACTION WITH CONSISTENT SNAPSHOT, READ ONLY')
            return conn
        except BaseException:conn.close();raise

    def metadata(self,conn,config):
        columns={r['COLUMN_NAME']:{'type':r['DATA_TYPE'],'nullable':r['IS_NULLABLE']=='YES'} for r in self.query(conn,
            'SELECT COLUMN_NAME,DATA_TYPE,IS_NULLABLE FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(config.database,config.table_name))}
        primary=[r['COLUMN_NAME'] for r in self.query(conn,"SELECT COLUMN_NAME FROM information_schema.KEY_COLUMN_USAGE WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s AND CONSTRAINT_NAME='PRIMARY' ORDER BY ORDINAL_POSITION",(config.database,config.table_name))]
        engine=self.query(conn,'SELECT ENGINE FROM information_schema.TABLES WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s',(config.database,config.table_name))
        readonly=bool(engine and engine[0]['ENGINE']=='InnoDB' and columns and primary)
        for row in self.query(conn,'SHOW GRANTS'):
            grant=next(iter(row.values()))
            if ' ON ' not in grant or ' WITH GRANT OPTION' in grant:readonly=False;continue
            privileges=grant.split(' ON ',1)[0].removeprefix('GRANT ').split(', ')
            if any(x not in ('USAGE','SELECT','SHOW VIEW') for x in privileges):readonly=False
        # A role-bearing account is indeterminate unless its entire closure is
        # inspected. Reject such accounts instead of assuming inactive means safe.
        if self.query(conn,'SELECT * FROM information_schema.APPLICABLE_ROLES'):readonly=False
        return columns,primary,readonly
