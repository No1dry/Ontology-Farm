"""Immutable core acceptance identities and atomic partitioned target writes."""
import json
from sqlalchemy import Column, Table, String, Integer, DateTime, ForeignKey, text, bindparam
from sqlalchemy.dialects.postgresql import UUID
from lzo.auth.models import metadata
from lzo.ingestion.models import sources
from lzo.synchronization.contracts import DomainReceipt
from lzo.synchronization.delivery import DeliveryConflict, register_delivery_handler

batches = Table('telemetry_batches', metadata,
    Column('id', UUID(as_uuid=True), ForeignKey('resource_scopes.resource_id'), primary_key=True),
    Column('source_id', UUID(as_uuid=True), ForeignKey(sources.c.id), nullable=False),
    Column('manifest_hash', String, nullable=False),
    Column('event_id', UUID(as_uuid=True), nullable=False, unique=True))
identities = Table('telemetry_record_keys', metadata,
    Column('source_id', UUID(as_uuid=True), ForeignKey(sources.c.id), primary_key=True),
    Column('source_record_id', String, primary_key=True),
    Column('version', Integer, primary_key=True),
    Column('id', UUID(as_uuid=True), nullable=False, unique=True),
    Column('object_id', UUID(as_uuid=True), nullable=False),
    Column('content_hash', String, nullable=False),
    Column('received_at', DateTime(timezone=True), nullable=False))


def apply_batch(connection, envelope):
    if envelope.operation != 'telemetry.batch':
        raise ValueError('Unsupported timeseries operation')
    payload = envelope.payload
    for row in payload['records']:
        from lzo.telemetry.service import record_content, record_id, TelemetryService
        from lzo.governance.risk import digest_change
        from uuid import UUID as PyUUID
        record = TelemetryService._input(row)
        if (digest_change(record_content(record)) != row['content_hash']
                or str(record_id(PyUUID(payload['source_id']), record.source_record_id, record.version)) != row['id']):
            raise DeliveryConflict('Telemetry record does not match its canonical identity/content')
        values = dict(id=row['id'], source=payload['source_id'], record=row['source_record_id'],
            version=row['version'], digest=row['content_hash'], sampled=row['sampled_at'])
        connection.execute(text('''INSERT INTO telemetry_identities(id,source_id,source_record_id,version,content_hash,sampled_at)
            VALUES(:id,:source,:record,:version,:digest,:sampled) ON CONFLICT DO NOTHING'''), values)
        stored = connection.execute(text('''SELECT id,content_hash,sampled_at FROM telemetry_identities
            WHERE source_id=:source AND source_record_id=:record AND version=:version'''), values).mappings().one()
        if (str(stored['id']) != row['id'] or stored['content_hash'] != row['content_hash']
                or stored['sampled_at'] != record.sampled_at):
            raise DeliveryConflict('Global telemetry identity has different content')
        connection.execute(text('''INSERT INTO telemetry_samples(sampled_at,id,source_id,object_id,device_id,kind,payload,batch_id,event_id)
            VALUES(:sampled,:id,:source,:object,:device,:kind,CAST(:payload AS jsonb),:batch,:event)
            ON CONFLICT(sampled_at,id) DO NOTHING'''), values | dict(object=row['object_id'], device=row['device_id'],
                kind=row['kind'], payload=json.dumps(row), batch=str(envelope.entity_id), event=str(envelope.event_id)))
        if row.get('supersedes_id'):
            connection.execute(text('INSERT INTO telemetry_supersessions(old_id,new_id) VALUES(:old,:new) ON CONFLICT DO NOTHING'),
                {'old': row['supersedes_id'], 'new': row['id']})
            successor = connection.execute(text('SELECT new_id FROM telemetry_supersessions WHERE old_id=:old'), {'old': row['supersedes_id']}).scalar_one()
            if str(successor) != row['id']:
                raise DeliveryConflict('Telemetry correction chain conflict')
    connection.execute(text('''INSERT INTO telemetry_batch_receipts(batch_id,event_id,payload_hash) VALUES(:id,:event,:hash)
        ON CONFLICT DO NOTHING'''), {'id': envelope.entity_id, 'event': envelope.event_id, 'hash': envelope.payload_hash})
    stored = connection.execute(text('SELECT payload_hash FROM telemetry_batch_receipts WHERE batch_id=:id'), {'id': envelope.entity_id}).scalar_one()
    if stored != envelope.payload_hash:
        raise DeliveryConflict('Immutable telemetry batch conflict')
    return DomainReceipt(event_id=envelope.event_id, target='timeseries', payload_hash=envelope.payload_hash,
        aggregate_revision=envelope.aggregate_revision, status='applied')


class TelemetryRepository:
    def __init__(self, registry):
        self.registry = registry

    def page(self, object_ids, source_ids, *, kind, start=None, end=None, after=None, limit=100, history=False, filters=None, record_ids=None):
        if not object_ids or not source_ids:
            return []
        query = '''SELECT s.payload,s.batch_id,s.event_id FROM telemetry_samples s
            WHERE s.object_id IN :objects AND s.source_id IN :sources AND s.kind=:kind '''
        params = dict(objects=list(object_ids), sources=list(source_ids), kind=kind, limit=limit)
        if record_ids is not None:
            query += 'AND s.id=ANY(:record_ids) '
            params['record_ids'] = list(record_ids)
        if not history:
            query += "AND s.payload->>'operation'='upsert' AND NOT EXISTS(SELECT 1 FROM telemetry_supersessions x WHERE x.old_id=s.id) "
        if start is not None:
            query += ("AND CAST(s.payload->>'end_at' AS timestamptz)>:start " if kind == 'media' else 'AND s.sampled_at>=:start ')
            params['start'] = start
        if end is not None:
            query += 'AND s.sampled_at<:end '; params['end'] = end
        if after:
            query += 'AND (s.sampled_at,s.id)>(:time,:id) '; params.update(time=after[0], id=after[1])
        for key, value in (filters or {}).items():
            if key not in ('device_id', 'channel_id', 'mission_id', 'metric_id', 'quality'):
                raise ValueError('Unsupported telemetry selector')
            query += f"AND s.payload->>'{key}'=:{key} "
            params[key] = str(value)
        query += 'ORDER BY s.sampled_at,s.id LIMIT :limit'
        with self.registry.engine('timeseries').connect() as connection:
            return [dict(r) for r in connection.execute(text(query).bindparams(
                bindparam('objects', expanding=True), bindparam('sources', expanding=True)), params).mappings()]


register_delivery_handler('timeseries', apply_batch)
