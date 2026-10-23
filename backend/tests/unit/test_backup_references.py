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


def gis_snapshot(monkeypatch, *, effect='observation', status='applied'):
    from lzo.storage import backup_format
    from lzo.synchronization.contracts import SyncEnvelope
    monkeypatch.setattr(backup_format, 'verify_rdf', lambda *args: 0)
    identifier, event, artifact, survey = map(str, (uuid4(), uuid4(), uuid4(), uuid4()))
    payload = ({'observation_id': identifier, 'position': {'longitude': 114, 'latitude': 30,
        'observed_at': '2026-10-22T00:00:00Z', 'horizontal_datum': 'EPSG:4326'}, 'source': 'synthetic'}
        if effect == 'observation' else {'asset_id': identifier, 'artifact_version_id': artifact,
            'survey_evidence_version_id': survey, 'sha256': 'a'*64, 'normalized': {'type': 'Point', 'coordinates': [114, 30]}})
    envelope = SyncEnvelope(event_id=event, entity_id=uuid4(), aggregate_revision=2,
        target='gis', operation='geometry.index', payload={'geometry': None, 'active': True, effect: payload},
        data_nature='synthetic')
    artifacts = [{'id': value, 'sha256': 'a'*64, 'size_bytes': 1, 'media_type': 'text/plain'} for value in (artifact, survey)]
    core = {'artifact_versions': artifacts, 'artifact_upload_sessions': [], 'outbox_events': [],
        'projection_heads': [], 'projection_events': [], 'revisions': [],
        'store_deliveries': [{'event_id': event, 'envelope': envelope.wire(), 'envelope_hash': envelope.envelope_hash,
            'payload_hash': envelope.payload_hash, 'status': status, 'target': 'gis'}]}
    domain = {'store_inbox': [{'event_id': event, 'envelope_hash': envelope.envelope_hash,
        'receipt': {'payload_hash': envelope.payload_hash}}],
        'gis_geometry_versions': [{'resource_id': str(envelope.entity_id), 'event_id': event, 'revision': 2,
            'payload_hash': envelope.payload_hash, 'payload': None, 'active': True}],
        'gis_spatial_assets': [], 'gis_location_observations': []}
    domain['gis_location_observations' if effect == 'observation' else 'gis_spatial_assets'].append({
        ('observation_id' if effect == 'observation' else 'asset_id'): identifier,
        'resource_id': str(envelope.entity_id), 'event_id': event, 'revision': 2, 'payload': payload,
        **({'artifact_version_id': artifact} if effect == 'asset' else {})})
    blobs = [dict(row, kind='version', key=row['id']) for row in artifacts]
    return core, {'gis': domain}, blobs


def test_location_backup_has_no_invented_blob_dependency(monkeypatch, tmp_path):
    from lzo.storage.backup_format import check_references
    assert check_references(*gis_snapshot(monkeypatch), tmp_path/'unused')['gis_versions'] == 1


@pytest.mark.parametrize('effect', ['asset', 'observation'])
@pytest.mark.parametrize('damage', ['missing', 'resource_id', 'event_id', 'revision', 'payload'])
def test_gis_backup_requires_exact_accepted_effect(monkeypatch, tmp_path, effect, damage):
    from lzo.storage.backup_format import check_references, BackupError
    core, domains, blobs = gis_snapshot(monkeypatch, effect=effect)
    rows = domains['gis']['gis_spatial_assets' if effect == 'asset' else 'gis_location_observations']
    if damage == 'missing': rows.clear()
    else: rows[0][damage] = {} if damage == 'payload' else 'altered'
    with pytest.raises(BackupError, match='GIS_EFFECT_REFERENCE_MISSING'):
        check_references(core, domains, blobs, tmp_path/'unused')


@pytest.mark.parametrize('status', ['pending', 'applied'])
@pytest.mark.parametrize('reference', ['primary', 'survey'])
def test_gis_backup_checks_asset_refs_even_pending(monkeypatch, tmp_path, status, reference):
    from lzo.storage.backup_format import check_references, BackupError
    core, domains, blobs = gis_snapshot(monkeypatch, effect='asset', status=status)
    core['artifact_versions'].pop(0 if reference == 'primary' else 1)
    with pytest.raises(BackupError, match='GIS_ARTIFACT_REFERENCE_MISSING'):
        check_references(core, domains, blobs, tmp_path/'unused')


@pytest.mark.parametrize('effect', ['asset', 'observation'])
def test_pending_gis_needs_refs_but_not_yet_applied_rows(monkeypatch, tmp_path, effect):
    from lzo.storage.backup_format import check_references
    core, domains, blobs = gis_snapshot(monkeypatch, effect=effect, status='pending')
    assert check_references(core, {}, blobs, tmp_path/'unused')['gis_versions'] == 0


def test_gis_backup_ignores_unrelated_retained_rows_but_checks_primary_identity(monkeypatch, tmp_path):
    from lzo.storage.backup_format import check_references, BackupError
    core, domains, blobs = gis_snapshot(monkeypatch, effect='asset')
    rows = domains['gis']['gis_spatial_assets']
    rows.append({'asset_id': 'unrelated-retained-negative-fixture'})
    assert check_references(core, domains, blobs, tmp_path/'unused')['gis_versions'] == 1
    rows[0]['artifact_version_id'] = str(uuid4())
    with pytest.raises(BackupError, match='GIS_EFFECT_REFERENCE_MISSING'):
        check_references(core, domains, blobs, tmp_path/'unused')
