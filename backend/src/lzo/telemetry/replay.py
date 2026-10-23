"""Static files and simulated increments share BatchInput and source identity rules.

These adapters describe replay capability only. They provide no ONVIF/DJI client,
network discovery, device commands, live stream, or hardware verification.
"""
import csv
import io
import json
from lzo.artifacts.service import ArtifactService
from lzo.contracts import BlobRef
from lzo.errors import ApiError
from lzo.storage.blobs import get_blob_store
from lzo.telemetry.schemas import BatchInput, ObservationInput

CAPABILITIES = {
    name: {'formats': ['csv', 'json', 'geojson'], 'incremental': 'incremental-v1',
        'live_connectivity': False, 'control': False, 'data_nature': ['synthetic', 'test', 'imported_unverified']}
    for name in ('camera-channel-timeline', 'pump-observations', 'drone-mission-track-media')
}


def parse_records(payload, format):
    if len(payload) > 2_000_000:
        raise ApiError('TELEMETRY_BATCH_TOO_LARGE', 413, 'Replay exceeds byte budget')
    try:
        content = payload.decode('utf-8-sig')
        if format == 'csv':
            reader = csv.DictReader(io.StringIO(content))
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError('Unique CSV headers required')
            rows = []
            for item in reader:
                if None in item or None in item.values():
                    raise ValueError('CSV shape differs from header')
                row = {}
                for key, value in item.items():
                    # Nullable CSV cells use the literal JSON null; empty is an
                    # actual raw string, never silently converted to numeric zero.
                    if value == 'null':
                        row[key] = None
                    elif key in ('supersedes', 'position', 'location_ref', 'device_binding_ref', 'model_ref', 'evidence_version_ids',
                                 'measurement_method', 'calibration', 'time_quality', 'media_metadata'):
                        row[key] = json.loads(value)
                    else:
                        row[key] = value
                rows.append(row)
                if len(rows) > 1000:
                    raise ValueError('Record budget exceeded')
        elif format in ('json', 'geojson'):
            value = json.loads(content)
            if format == 'geojson':
                if value.get('type') != 'FeatureCollection':
                    raise ValueError('FeatureCollection required')
                rows = []
                for feature in value['features']:
                    row = dict(feature['properties'])
                    geometry = feature.get('geometry')
                    if geometry is not None:
                        if geometry.get('type') != 'Point' or len(geometry['coordinates']) != 2:
                            raise ValueError('Explicit 2D WGS84 Point required')
                        row['position'] = {'observed_at': row['sampled_at'], 'horizontal_datum': 'EPSG:4326',
                            'longitude': geometry['coordinates'][0], 'latitude': geometry['coordinates'][1]}
                    rows.append(row)
            else:
                rows = value
            if not isinstance(rows, list) or len(rows) > 1000:
                raise ValueError('Bounded record array required')
        else:
            raise ValueError('Unsupported adapter')
        return tuple(ObservationInput.model_validate(row) for row in rows)
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError):
        raise ApiError('TELEMETRY_PARSE_FAILED', 422, 'Invalid normalized telemetry file') from None


def accept_file(service, actor, command, key):
    store = get_blob_store(service.settings)
    row = ArtifactService(service.uow, store, service.settings)._version(actor, command.artifact_version_id)
    if row['size_bytes'] > 2_000_000:
        raise ApiError('TELEMETRY_BATCH_TOO_LARGE', 413, 'Replay exceeds byte budget')
    with store.open(BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])) as stream:
        records = parse_records(stream.read(2_000_001), command.format)
    return service.accept(actor, BatchInput(source_id=command.source_id, records=records,
        adapter=command.format+'-v1', input_artifact_version_id=row['id'], mapping_version_id=command.mapping_version_id), key)
