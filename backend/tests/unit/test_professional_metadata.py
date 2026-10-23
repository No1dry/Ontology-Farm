"""Additive provenance must not change previously accepted content representations."""
import json
from uuid import uuid4
from lzo.telemetry.schemas import ObservationInput
from lzo.telemetry.service import record_content
from lzo.governance.risk import digest_change


def old_record():
    return dict(source_record_id='old', object_id=str(uuid4()), device_id=str(uuid4()), point_id='pump',
        metric_id='flow', raw_value='1', normalized_value='1', sampled_at='2026-10-22T00:00:00Z', data_nature='synthetic')


def test_optional_provenance_roundtrips_replay_and_exact_pins():
    from lzo.telemetry.replay import parse_records
    payload = old_record() | {'measurement_method': {'name': 'ultrasonic', 'version': '2'},
        'calibration': {'version': 'cal-2026', 'method': 'bench'},
        'time_quality': {'state': 'synchronized', 'uncertainty_ms': '2'},
        'media_metadata': {'encoding': 'h264', 'resolution': {'width': 1920, 'height': 1080},
            'sequence': 4, 'gaps': [{'start': '2026-10-22T00:00:01Z', 'end': '2026-10-22T00:00:02Z'}],
            'verification': 'unverified'}}
    record = parse_records(json.dumps([payload]).encode(), 'json')[0]
    assert record_content(record)['measurement_method'] == payload['measurement_method']
    assert record_content(record)['media_metadata']['sequence'] == 4
    assert ObservationInput.model_validate(record_content(record)) == record


def test_old_payload_exact_shape_and_hash_stays_unchanged():
    value = record_content(ObservationInput.model_validate(old_record()))
    for name in ('measurement_method', 'calibration', 'time_quality', 'media_metadata'):
        assert name not in value
    assert record_content(ObservationInput.model_validate(value)) == value
    assert digest_change(record_content(ObservationInput.model_validate(value))) == digest_change(value)


def test_catalog_optional_metadata_and_old_representation():
    from lzo.catalog.schemas import ModelMetadata, DocumentMetadata
    old = ModelMetadata(exact_model='PX-1', firmware='fw-7').model_dump(mode='json')
    assert 'hardware_version' not in old and 'equipment_category' not in old
    model = ModelMetadata(exact_model='PX-1', firmware='fw-7', hardware_version='board-2', equipment_category='pump')
    assert model.model_dump(mode='json')['hardware_version'] == 'board-2'
    assert ModelMetadata.model_validate(model.model_dump(mode='json')).firmware == 'fw-7'
    document = DocumentMetadata(title='Manual', document_type='manual', artifact_version_id=uuid4(),
        source_uri='urn:synthetic:manual', source_version='1', license='synthetic', author='Author',
        organization='Organization', agricultural_scenario='pond', language='zh', manual_summary_preparer='Curator')
    assert DocumentMetadata.model_validate(document.model_dump(mode='json')) == document


def test_exact_base_contract_payload_and_acceptance_hashes():
    from pathlib import Path
    from lzo.telemetry.schemas import BatchInput
    from lzo.catalog.schemas import ModelMetadata, DocumentMetadata
    fixture = json.loads((Path(__file__).resolve().parents[3]/'tests/fixtures/telemetry/legacy-professional-contract.json').read_text())
    for key, actual in (
        ('record', record_content(ObservationInput.model_validate(fixture['record']['payload']))),
        ('batch', BatchInput.model_validate(fixture['batch']['payload']).model_dump(mode='json')),
        ('model', ModelMetadata.model_validate(fixture['model']['payload']).model_dump(mode='json')),
        ('document', DocumentMetadata.model_validate(fixture['document']['payload']).model_dump(mode='json'))):
        assert actual == fixture[key]['payload']
        assert digest_change(actual) == fixture[key]['sha256']
    assert fixture['record']['payload']['model_ref']['revision'] == 9
    assert fixture['model']['payload']['firmware'] == 'fw-7'
