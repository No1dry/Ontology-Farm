"""Governed retry of one exact exhausted immutable catalog delivery."""
from dataclasses import asdict
from uuid import UUID, uuid4
from sqlalchemy import text, select
from lzo.artifacts.service import _permission, _scope
from lzo.contracts import ResourceScope, RevisionRef
from lzo.errors import Conflict
from lzo.governance.idempotency import execute_idempotent
from lzo.governance.models import resource_heads
from lzo.governance.revisions import append_revision, lock_ledger
from lzo.governance.risk import digest_change
from lzo.governance.history import HistoryService
from lzo.ingestion.sources import add_scoped, bind
from lzo.synchronization.contracts import SyncEnvelope
from lzo.synchronization.delivery import DeliveryConflict


def retry(service, actor, identifier, kind, command, key):
    uow = service.uow
    pin = command.accepted_revision
    row = service._row(actor, identifier, 'catalog.manage', kind=kind, revision=pin.revision)
    if pin != RevisionRef(identifier, row['revision'], row['event_id']) or row['event_type'] != 'catalog.version.accepted':
        raise Conflict('CATALOG_REFERENCE_MISMATCH', 'Retry requires the exact accepted catalog version')
    metadata = service.get(actor, identifier, revision=pin.revision, kind=kind).metadata
    dependencies = service._references(actor, metadata, identifier)

    def accept():
        lock_ledger(uow)
        head = uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id == identifier)).scalar_one()
        if head != command.expected_revision:
            raise Conflict('REVISION_CONFLICT', 'Catalog resource baseline changed')
        stored = uow.session.execute(text("SELECT * FROM store_deliveries WHERE event_id=:event AND target='catalog' FOR UPDATE"), {'event': pin.event_id}).mappings().one()
        envelope = SyncEnvelope.model_validate(stored['envelope'])
        if (envelope.event_id != pin.event_id or envelope.entity_id != identifier or envelope.aggregate_revision != pin.revision or
                envelope.target != 'catalog' or envelope.operation != 'catalog.index' or envelope.payload != row['payload'] or
                envelope.envelope_hash != stored['envelope_hash'] or envelope.payload_hash != stored['payload_hash']):
            raise DeliveryConflict('Stored catalog envelope differs from exact acceptance')
        if stored['status'] != 'failed':
            raise Conflict('CATALOG_DELIVERY_NOT_FAILED', 'Only exhausted catalog delivery can be retried')
        owner = _scope(uow, identifier)
        audit_id = uuid4()
        add_scoped(uow, actor, audit_id, ResourceScope(None, owner['owner_scope_id'],
            tuple(UUID(v) for v in owner['related_scope_ids']), owner['security_class']),
            'catalog.delivery_retry', 'catalog.manage')
        audit = append_revision(uow, actor, audit_id, 0, {
            'accepted_revision': {**asdict(pin), 'resource_id': str(pin.resource_id), 'event_id': str(pin.event_id)},
            'catalog_baseline': head, 'envelope_hash': stored['envelope_hash'],
            'previous_attempts': stored['attempts'], 'previous_failure': stored['error']}, 'catalog.delivery.retried')
        bind(uow, audit, dependencies | {identifier})
        uow.session.execute(text("UPDATE store_deliveries SET status='retry',attempts=0,error=NULL,available_at=clock_timestamp() WHERE event_id=:event AND target='catalog' AND status='failed'"), {'event': pin.event_id})
        return {'accepted_revision': {**asdict(pin), 'resource_id': str(pin.resource_id), 'event_id': str(pin.event_id)},
            'audit': {'resource_id': str(audit.resource_id), 'event_id': str(audit.event_id), 'revision': audit.revision}, 'status': 'pending'}
    result = execute_idempotent(uow, actor, key, digest_change({'operation': 'catalog.retry',
        'id': str(identifier), 'kind': kind, 'command': command.model_dump(mode='json')}), accept)
    HistoryService(uow).get(actor, UUID(result['audit']['resource_id']), result['audit']['revision'])
    return result
