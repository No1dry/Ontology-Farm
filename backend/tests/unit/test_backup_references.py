"""Accepted references must close over retained identities and immutable bytes."""
from uuid import uuid4

import pytest


@pytest.mark.parametrize('kind,metadata', [
    ('document', {'artifact_version_id': 'missing'}),
    ('model', {'attachment_version_ids': ['missing']}),
    ('model', {'capabilities': [{'evidence_version_ids': ['missing']}]}),
])
def test_catalog_backup_rejects_missing_accepted_artifact(monkeypatch, tmp_path, kind, metadata):
    from lzo.storage import backup_format
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.governance.risk import digest_change
    monkeypatch.setattr(backup_format, 'verify_rdf', lambda *args: 0)
    envelope = SyncEnvelope(event_id=uuid4(), entity_id=uuid4(), aggregate_revision=1,
        target='catalog', operation='catalog.index', payload={'kind': kind, 'metadata': metadata, 'sha256': None},
        data_nature='synthetic', recorded_at='2026-10-22T00:00:00Z')
    core = {'artifact_versions': [], 'artifact_upload_sessions': [], 'outbox_events': [],
        'projection_heads': [], 'projection_events': [],
        'revisions': [{'event_id': str(envelope.event_id), 'payload': envelope.payload,
            'content_digest': digest_change(envelope.payload)}],
        'store_deliveries': [{'envelope': envelope.wire(), 'envelope_hash': envelope.envelope_hash,
            'payload_hash': envelope.payload_hash, 'status': 'pending', 'target': 'catalog'}]}
    with pytest.raises(backup_format.BackupError, match='CATALOG_ARTIFACT_REFERENCE_MISSING'):
        backup_format.check_references(core, {}, [], tmp_path / 'unused.nq')
