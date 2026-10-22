"""Replay original synthetic fixtures through the authenticated backend API.

No database, S3, device-network, or control credentials are used. Session and
CSRF secrets are read from private files and never written to output.
"""
import argparse
import json
from pathlib import Path
import sys
from uuid import UUID, uuid4
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend/src'))
from lzo.telemetry.replay import parse_records
from lzo.telemetry.schemas import BatchInput, FileReplayInput


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-id', type=UUID, required=True)
    parser.add_argument('--input', type=Path)
    parser.add_argument('--format', choices=['csv', 'json', 'geojson'], required=True)
    parser.add_argument('--object-id', type=UUID, help='Bind synthetic placeholder objects to an existing authorized object')
    parser.add_argument('--device-id', type=UUID)
    parser.add_argument('--artifact-version-id', type=UUID, help='Replay already registered private static bytes through /replay')
    parser.add_argument('--base-url', help='Omit to print the validated synthetic manifest only')
    parser.add_argument('--session-file', type=Path)
    parser.add_argument('--csrf-file', type=Path)
    parser.add_argument('--ca-file', type=Path)
    parser.add_argument('--idempotency-key', default=None)
    args = parser.parse_args()
    if args.artifact_version_id:
        if args.input or args.object_id or args.device_id:
            parser.error('Registered bytes cannot be rewritten; use the exact stored file')
        command = FileReplayInput(source_id=args.source_id, artifact_version_id=args.artifact_version_id, format=args.format)
        endpoint = '/api/v1/telemetry/replay'
    else:
        if args.input is None:
            parser.error('--input or --artifact-version-id is required')
        records = parse_records(args.input.read_bytes(), args.format)
        if any(record.data_nature != 'synthetic' for record in records):
            parser.error('This example replay command accepts synthetic fixtures only')
        updates = {key: value for key, value in {'object_id': args.object_id, 'device_id': args.device_id}.items() if value}
        records = tuple(record.model_copy(update=updates) for record in records)
        command = BatchInput(source_id=args.source_id, records=records)
        endpoint = '/api/v1/telemetry/batches'
    if not args.base_url:
        print(json.dumps(command.model_dump(mode='json'), indent=2))
        return
    if not args.session_file or not args.csrf_file:
        parser.error('Authenticated replay requires private --session-file and --csrf-file')
    from lzo.auth.session import SESSION_COOKIE
    import ssl
    verify = ssl.create_default_context(cafile=str(args.ca_file)) if args.ca_file else True
    with httpx.Client(base_url=args.base_url, verify=verify, timeout=60) as client:
        response = client.post(endpoint, json=command.model_dump(mode='json'), headers={
            'Cookie': SESSION_COOKIE+'='+args.session_file.read_text().strip(),
            'X-CSRF-Token': args.csrf_file.read_text().strip(), 'Origin': args.base_url.rstrip('/'),
            'Idempotency-Key': args.idempotency_key or str(uuid4())})
        if response.status_code != 202:
            raise SystemExit('Replay rejected: HTTP '+str(response.status_code))
        result = response.json()
        print(json.dumps({key: result[key] for key in ('id', 'event_id', 'status', 'accepted_count', 'record_count')}))


if __name__ == '__main__':
    main()
