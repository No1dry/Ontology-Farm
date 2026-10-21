"""One connection/transaction per scan; cursors are bound to that live snapshot."""
import io
import re
import time
from dataclasses import dataclass
from datetime import datetime,timezone,timedelta,date
from decimal import Decimal
from typing import Protocol
from uuid import UUID,uuid4,uuid5
from lzo.errors import ApiError,Conflict
from lzo.ingestion.datetimes import source_datetime
from lzo.ingestion.schemas import ConnectionConfig,ReadRequest,ConnectionCheck,ParseIssue,SourcePage,SourceChange,SourcePosition,SourceKey
from lzo.ingestion.sources import canonical,digest


def scope_digest(config):
    # Credential rotation/host changes also invalidate comparability. No client
    # can forge a smaller filter/mapping scope by supplying its own digest.
    return digest(config.model_dump(mode='json',exclude={'expected_revision'}))


def json_value(value):
    if isinstance(value,(datetime,date)):return value.isoformat()
    if isinstance(value,(Decimal,UUID)):return str(value)
    if isinstance(value,bytes):raise ValueError('Binary source fields require explicit mapping')
    return value


class SourceConnector(Protocol):
    def check(self,config:ConnectionConfig)->ConnectionCheck:...
    def read_page(self,config:ConnectionConfig,request:ReadRequest)->SourcePage:...


@dataclass
class Snapshot:
    connection: object
    epoch: UUID
    config_digest: str
    columns: dict
    order: tuple
    fence: tuple | None
    mode: str
    received_at: datetime
    deadline: float
    cursor: dict | None = None
    ordinal: int = 0
    lower: object = None


class DatabaseConnector:
    def __init__(self,credentials,source,store,*,ca_file=None,timeout_seconds=120):
        self.credentials,self.source,self.store=credentials,source,store
        self.ca_file,self.timeout_seconds=ca_file,timeout_seconds
        self.snapshots={}

    def query(self,connection,sql,params=()):
        with connection.cursor() as cursor:
            cursor.execute(sql,params)
            if cursor.description:
                names=[x[0] for x in cursor.description]
                return [dict(zip(names,row)) for row in cursor.fetchall()]
            return []

    def quote(self,name):
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}',name):raise ValueError('Invalid identifier')
        return self.delimiter+name+self.delimiter

    def table(self,config):
        schema=config.schema_name or (config.database if self.backend=='mysql' else 'public')
        if self.backend=='mysql' and schema!=config.database:raise ValueError('Cross-database source unsupported')
        return self.quote(schema)+'.'+self.quote(config.table_name)

    def filter(self,expression,columns,depth=0):
        if expression is None:return 'TRUE',[]
        if depth>8:raise ValueError('Filter budget')
        if expression.op in ('and','or') and 1<=len(expression.args)<=16:
            parts=[self.filter(x,columns,depth+1) for x in expression.args]
            return '('+f' {expression.op.upper()} '.join(p[0] for p in parts)+')',[v for p in parts for v in p[1]]
        if expression.op=='eq' and len(expression.args)==2:
            path,literal=expression.args
            if path.op!='path' or path.path not in columns or literal.op!='literal' or literal.value is None or set(literal.value)!={'value'}:
                raise ValueError('Invalid filter')
            value=literal.value['value']
            if value is not None and not isinstance(value,(str,int,bool,float)):raise ValueError('Scalar filter required')
            return (self.quote(path.path)+' IS NULL',[]) if value is None else (self.quote(path.path)+' = %s',[value])
        raise ValueError('Unsupported structured filter')

    def metadata(self,conn,config):raise NotImplementedError
    def connect(self,config):raise NotImplementedError

    def _open(self,config):
        if config.source_id!=self.source.id:raise ValueError('Source mismatch')
        self.table(config)
        conn=self.connect(config)
        try:
            columns,primary,readonly=self.metadata(conn,config)
            if not readonly:raise ApiError('SOURCE_NOT_READONLY',422,'Source account has write or indeterminate privileges')
            if tuple(primary)!=config.key_columns or any(columns[k]['nullable'] for k in primary):raise ValueError('Exact non-null primary key required')
            supported={'smallint','integer','bigint','int','tinyint','mediumint','varchar','character varying','char','character','text','uuid'}
            if any(columns[k]['type'] not in supported for k in primary):raise ValueError('Unsupported key type')
            for name in (config.updated_at_column,config.deleted_column):
                if name and name not in columns:raise ValueError('Unknown column')
            if config.temporal_fields:
                for name in config.temporal_fields.model_dump().values():
                    if name and name not in columns:raise ValueError('Unknown temporal column')
            self.filter(config.filter_expression,columns)
            timestamp=config.updated_at_column
            reliable=bool(timestamp and not columns[timestamp]['nullable'] and columns[timestamp]['type'] in
                {'timestamp with time zone','timestamp without time zone','timestamp','datetime'})
            native_utc=self.backend=='mysql' and columns.get(timestamp,{}).get('type')=='timestamp'
            if reliable and columns[timestamp]['type']!='timestamp with time zone' and not config.source_timezone and not native_utc:reliable=False
            return conn,columns,reliable
        except BaseException:
            conn.close();raise

    def check(self,config):
        conn=None
        try:
            conn,columns,reliable=self._open(config)
            return ConnectionCheck(source_id=config.source_id,reachable=True,read_only=True,
                incremental_mode='timestamp_key' if reliable else 'snapshot_diff',issues=())
        except Exception as error:
            code=error.code if isinstance(error,ApiError) else 'SOURCE_CONNECTION_INVALID'
            return ConnectionCheck(source_id=config.source_id,reachable=code in ('SOURCE_NOT_READONLY','SOURCE_ROLE_UNPROVEN'),read_only=False,
                incremental_mode='snapshot_diff',issues=(ParseIssue(locator='connection',field_path=None,code=code,message='Source connection or policy validation failed'),))
        finally:
            if conn:conn.close()

    def close(self,token=None):
        for identifier in list(self.snapshots):
            if token is None or token==identifier:
                snapshot=self.snapshots.pop(identifier)
                # PyMySQL close is not idempotent; a lost/closed socket must not
                # replace the sanitized read failure with a raw driver error.
                if not getattr(snapshot.connection,'closed',False) and getattr(snapshot.connection,'open',True):
                    snapshot.connection.close()

    def read_page(self,config,request):
        token=(request.cursor or {}).get('snapshot')
        snapshot=None;opening=None
        try:
            # Recheck task authority even while retaining a connection.
            self.credentials.authorize()
            if request.source_id!=config.source_id or request.scope_digest!=scope_digest(config):raise ValueError('Scope mismatch')
            for old,state in list(self.snapshots.items()):
                if time.monotonic()>state.deadline:self.close(old)
            if token:
                snapshot=self.snapshots.get(token)
                if snapshot is None or snapshot.cursor!=request.cursor or snapshot.config_digest!=scope_digest(config):
                    raise Conflict('SNAPSHOT_LOST','Source snapshot cannot be resumed')
                if snapshot.mode!=request.mode:raise ValueError('Mode changed')
            else:
                if request.cursor and set(request.cursor)!={'watermark'}:raise ValueError('Invalid scan cursor')
                conn,columns,reliable=self._open(config)
                opening=conn
                order=((config.updated_at_column,) if reliable else ())+config.key_columns
                where,params=self.filter(config.filter_expression,columns)
                expression=','.join(self.quote(k) for k in order)
                rows=self.query(conn,f'SELECT {expression} FROM {self.table(config)} WHERE {where} ORDER BY '+','.join(self.quote(k)+' DESC' for k in order)+' LIMIT 1',params)
                fence=tuple(rows[0][k] for k in order) if rows else None
                snapshot=Snapshot(conn,uuid4(),scope_digest(config),columns,order,fence,request.mode,
                    datetime.now(timezone.utc),time.monotonic()+self.timeout_seconds)
                token=str(snapshot.epoch);self.snapshots[token]=snapshot
                opening=None
                if request.upper_fence is not None:raise ValueError('Upper fence is selected inside the snapshot')
                if request.mode=='incremental' and request.cursor and reliable:
                    watermark=request.cursor['watermark']
                    if watermark.get('scope_digest')!=scope_digest(config):raise ValueError('Watermark scope changed')
                    lower=source_datetime(watermark['updated_at'],config.source_timezone)-timedelta(seconds=config.overlap_seconds)
                    snapshot.lower=self.timestamp_parameter(lower,columns[config.updated_at_column]['type'],config)
            where,params=self.filter(config.filter_expression,snapshot.columns)
            expression='('+','.join(self.quote(k) for k in snapshot.order)+')'
            placeholders='('+','.join(['%s']*len(snapshot.order))+')'
            if snapshot.fence is not None:
                where+=f' AND {expression} <= {placeholders}';params.extend(snapshot.fence)
            if snapshot.cursor:
                values=snapshot.cursor['after']
                decoded=[self.decode(v,snapshot.columns[k],config) for k,v in zip(snapshot.order,values)]
                where+=f' AND {expression} > {placeholders}';params.extend(decoded)
            elif snapshot.lower is not None:
                where+=f' AND {self.quote(config.updated_at_column)} >= %s';params.append(snapshot.lower)
            order=','.join(self.quote(k) for k in snapshot.order)
            rows=self.query(snapshot.connection,f'SELECT * FROM {self.table(config)} WHERE {where} ORDER BY {order} LIMIT %s',params+[request.page_size+1])
            end=len(rows)<=request.page_size;rows=rows[:request.page_size]
            raw=[{k:json_value(v) for k,v in r.items()} for r in rows]
            blob=self.store.put(io.BytesIO(canonical(raw).encode()),'application/json')
            changes=[]
            for row,fields in zip(rows,raw):
                snapshot.ordinal+=1
                key=canonical([json_value(row[k]) for k in config.key_columns])
                if config.deleted_column and row[config.deleted_column] not in (True,False,0,1,None):raise ValueError('Invalid deleted flag')
                operation='delete' if config.deleted_column and row[config.deleted_column] in (True,1) else 'upsert'
                times={}
                for name in ('valid_from','valid_to','observed_at'):
                    path=getattr(config.temporal_fields,name+'_path') if config.temporal_fields else None
                    times[name]=self.clock_value(row[path],snapshot.columns[path],config) if path and row[path] is not None else None
                if times['valid_from'] and times['valid_to'] and times['valid_to']<=times['valid_from']:raise ValueError('Invalid interval')
                version=self.clock_value(row[config.updated_at_column],snapshot.columns[config.updated_at_column],config).isoformat() if snapshot.order[0]==config.updated_at_column else None
                content=digest({'fields':fields,'times':{k:v.isoformat() if v else None for k,v in times.items()},'operation':operation})
                identity=digest({'scope':snapshot.config_digest,'key':key,'version':version,'content':content})
                source=self.source.configuration
                changes.append(SourceChange(change_id=uuid5(snapshot.epoch,key),key=SourceKey(source_id=config.source_id,
                    source_system=source.source_system,source_instance=source.source_instance,collection=source.collection,canonical_key=key),
                    operation=operation,position=SourcePosition(stream_id=str(config.source_id),epoch=snapshot.epoch,ordinal=snapshot.ordinal,
                    source_version=version,cursor={'scope_digest':snapshot.config_digest,'order':[json_value(row[k]) for k in snapshot.order]}),
                    **times,received_at=snapshot.received_at,snapshot=blob,content_sha256=content,idempotency_key=identity,
                    raw_fields=fields,geometry=None,data_nature=source.data_nature,locator='database/'+key))
            watermark=None
            if snapshot.fence and snapshot.order[0]==config.updated_at_column:
                watermark={'updated_at':self.clock_value(snapshot.fence[0],snapshot.columns[config.updated_at_column],config).isoformat(),
                    'keys':[json_value(x) for x in snapshot.fence[1:]],'scope_digest':snapshot.config_digest}
            next_cursor=None if end else {'snapshot':token,'after':[json_value(rows[-1][k]) for k in snapshot.order]}
            snapshot.cursor=next_cursor
            if end:
                snapshot.connection.commit() # complete only AFTER successful real transaction finalization
                self.close(token)
            return SourcePage(changes=tuple(changes),next_cursor=next_cursor,seen_keys=tuple(c.key.canonical_key for c in changes),
                source_snapshot_token=token,end_of_scan=end,watermark=watermark)
        except Exception as error:
            if token:self.close(token)
            if isinstance(error,ApiError):raise
            raise ApiError('SOURCE_READ_FAILED',422,'Source read failed; snapshot is not resumable') from None
        finally:
            # Metadata succeeded but a fence query can fail before this
            # connection becomes an owned, resumable Snapshot.
            if opening is not None and not getattr(opening,'closed',False) and getattr(opening,'open',True):opening.close()

    def timestamp_parameter(self,value,column_type,config):
        if column_type=='timestamp with time zone':return value
        if self.backend=='mysql' and column_type=='timestamp':return value.astimezone(timezone.utc).replace(tzinfo=None)
        from zoneinfo import ZoneInfo
        return value.astimezone(ZoneInfo(config.source_timezone)).replace(tzinfo=None)

    def decode(self,value,column,config):
        if column['type'] in {'timestamp','datetime','timestamp without time zone','timestamp with time zone'}:
            return self.timestamp_parameter(self.clock_value(value,column,config),column['type'],config)
        if column['type']=='uuid':return UUID(value)
        return value

    def clock_value(self,value,column,config):
        # MySQL TIMESTAMP is already rendered in the connector's explicit UTC
        # session. DATETIME and PG timestamp-without-zone need the declared zone.
        zone='UTC' if self.backend=='mysql' and column['type']=='timestamp' else config.source_timezone
        return source_datetime(value,zone)
