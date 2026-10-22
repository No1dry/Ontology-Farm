"""Core identities and immutable catalog projection; permissions live only in core."""
import json
from sqlalchemy import Column, String, Table, ForeignKey, text, bindparam
from sqlalchemy.dialects.postgresql import UUID
from lzo.auth.models import metadata
from lzo.synchronization.contracts import DomainReceipt
from lzo.synchronization.delivery import DeliveryConflict, register_delivery_handler

entries = Table('catalog_entries', metadata,
    Column('resource_id', UUID(as_uuid=True), ForeignKey('resource_scopes.resource_id'), primary_key=True),
    Column('kind', String, nullable=False),
    Column('source_identity', String, unique=True))
device_models = Table('catalog_device_models', metadata,
    Column('entity_id', UUID(as_uuid=True), ForeignKey('resource_scopes.resource_id'), primary_key=True),
    Column('resource_id', UUID(as_uuid=True), ForeignKey('resource_scopes.resource_id'), unique=True, nullable=False),
    Column('model_id', UUID(as_uuid=True), ForeignKey('catalog_entries.resource_id'), nullable=False))


def _write(connection, envelope):
    if envelope.operation != 'catalog.index':
        raise ValueError('Unsupported catalog operation')
    connection.execute(text('''INSERT INTO catalog_versions(resource_id,revision,event_id,kind,payload,payload_hash)
        VALUES(:id,:revision,:event,:kind,CAST(:payload AS jsonb),:hash)
        ON CONFLICT(resource_id,revision) DO NOTHING'''), dict(id=envelope.entity_id,
        revision=envelope.aggregate_revision, event=envelope.event_id, kind=envelope.payload['kind'],
        payload=json.dumps(envelope.payload), hash=envelope.payload_hash))
    row = connection.execute(text('SELECT event_id,payload_hash FROM catalog_versions WHERE resource_id=:id AND revision=:revision'),
        dict(id=envelope.entity_id, revision=envelope.aggregate_revision)).mappings().one()
    # The delivery boundary already compares all meaning-bearing envelope
    # fields. Equal-revision duplicate events retain the original version row.
    if row['payload_hash'] != envelope.payload_hash:
        raise DeliveryConflict('Immutable catalog version conflict')


def apply_catalog(connection, envelope):
    _write(connection, envelope)
    connection.execute(text('''INSERT INTO catalog_heads(resource_id,revision) VALUES(:id,:revision)
        ON CONFLICT(resource_id) DO UPDATE SET revision=excluded.revision
        WHERE catalog_heads.revision<excluded.revision'''),
        dict(id=envelope.entity_id, revision=envelope.aggregate_revision))
    return DomainReceipt(event_id=envelope.event_id, target='catalog', payload_hash=envelope.payload_hash,
        aggregate_revision=envelope.aggregate_revision, status='applied')


def apply_catalog_history(connection, envelope):
    _write(connection, envelope)
    return DomainReceipt(event_id=envelope.event_id, target='catalog', payload_hash=envelope.payload_hash,
        aggregate_revision=envelope.aggregate_revision, status='superseded')


class CatalogRepository:
    def __init__(self, registry):
        self.registry = registry

    def versions(self, authorized_ids):
        if not authorized_ids:
            return []
        with self.registry.engine('catalog').connect() as connection:
            return connection.execute(text('SELECT resource_id,revision,event_id,payload FROM catalog_versions WHERE resource_id IN :ids ORDER BY resource_id,revision')
                .bindparams(bindparam('ids', expanding=True)), {'ids': list(authorized_ids)}).mappings().all()


register_delivery_handler('catalog', apply_catalog, history_handler=apply_catalog_history)
