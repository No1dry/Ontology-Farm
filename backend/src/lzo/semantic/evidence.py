"""Evidence attachment shares the ordinary full semantic object revision path."""
from uuid import UUID
from sqlalchemy import select
from lzo.errors import Conflict
from lzo.governance.models import resource_heads, revisions
from lzo.governance.revisions import lock_ledger
from lzo.semantic import facts
from lzo.ontology.releases import get_execution_bundle


def attach_to_object(uow, actor, identifier, expected, payload):
    row = uow.session.execute(select(facts.objects).where(facts.objects.c.id == identifier)).mappings().first()
    if row is None:
        return None
    row = facts.get_object_row(uow, actor, identifier, 'edit')
    if row['revision'] != expected:
        raise Conflict('OBJECT_EVIDENCE_RECONCILIATION_REQUIRED', 'Reconcile the legacy evidence tail before editing this object')
    bundle = get_execution_bundle(actor, row['bundle_id'])
    return facts.record_state(uow, actor, row, expected, bundle, {UUID(payload['evidence_version_id'])},
        'object.evidence_attached', {'operation': 'object.evidence.attach', **payload}).revision


def reconcile_legacy_evidence(uow, actor, identifier, expected):
    """Append a governed repair only for the evidenced contiguous legacy tail."""
    from lzo.artifacts.service import ArtifactService
    from lzo.storage.blobs import get_blob_store
    from lzo.contracts import BlobRef
    from lzo.governance.history import revision_metadata, revision_visibility_dependencies
    from sqlalchemy import insert
    lock_ledger(uow)
    row = facts.get_object_row(uow, actor, identifier, 'edit')
    head = uow.session.execute(select(resource_heads).where(resource_heads.c.resource_id == identifier)).mappings().one()
    if head['revision'] != expected:
        raise Conflict('REVISION_CONFLICT', 'Object head changed')
    tail = uow.session.execute(select(revisions).where(revisions.c.resource_id == identifier,
        revisions.c.revision > row['revision']).order_by(revisions.c.revision)).mappings().all()
    if (not tail or [r['revision'] for r in tail] != list(range(row['revision']+1, expected+1)) or
            any(r['event_type'] != 'evidence.attached' for r in tail)):
        raise Conflict('LEGACY_EVIDENCE_TAIL_REQUIRED', 'Only a contiguous legacy attachment tail can be reconciled')
    baseline = uow.session.execute(select(revisions).where(revisions.c.resource_id == identifier,
        revisions.c.revision == row['revision'])).mappings().one()
    if baseline['event_type'] not in facts.OBJECT_EVENTS:
        raise Conflict('LEGACY_EVIDENCE_TAIL_REQUIRED', 'Stored semantic baseline is not a recognized snapshot')
    settings = facts.settings_for(uow)
    store = get_blob_store(settings)
    service = ArtifactService(uow, store, settings)
    evidence = set()
    for revision in tail:
        payload = revision['payload']
        try:
            if (set(payload) != {'evidence_version_id', 'sha256', 'size_bytes', 'metadata'} or
                    not isinstance(payload['evidence_version_id'], str) or not isinstance(payload['metadata'], dict) or
                    not isinstance(payload['sha256'], str) or type(payload['size_bytes']) is not int):
                raise ValueError()
            version_id = UUID(payload['evidence_version_id'])
        except (ValueError, TypeError, KeyError):
            raise Conflict('LEGACY_EVIDENCE_MISMATCH', 'Legacy attachment payload is not recognized') from None
        version = service._version(actor, version_id)
        if (version['resource_id'] != identifier or version['sha256'] != payload['sha256'] or
                version['size_bytes'] != payload['size_bytes']):
            raise Conflict('LEGACY_EVIDENCE_MISMATCH', 'Attachment differs from its immutable artifact')
        with store.open(BlobRef(version['sha256'], version['size_bytes'], version['media_type'], version['id'])):
            pass
        evidence.add(version['id'])
        for dependency in uow.session.execute(select(revision_visibility_dependencies.c.resource_id).where(
                revision_visibility_dependencies.c.event_id == revision['event_id'])).scalars():
            facts.authorize(uow, actor, dependency)
    bundle = get_execution_bundle(actor, row['bundle_id'])
    ref = facts.record_state(uow, actor, row, expected, bundle, evidence,
        'object.evidence_reconciled', {'operation': 'object.evidence.reconcile',
            'semantic_baseline': facts.json_value(facts.RevisionRef(identifier, row['revision'], baseline['event_id'])),
            'legacy_events': [str(r['event_id']) for r in tail],
            'evidence_version_ids': sorted(map(str, evidence))}).revision
    uow.session.execute(insert(revision_metadata).values(event_id=ref.event_id,
        evidence_refs=sorted(map(str, evidence)), proof_refs=[]))
    return ref
