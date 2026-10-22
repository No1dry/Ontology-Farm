"""Reproducible synthetic multi-store benchmark with real API authorization.

Run through multistore_acceptance.py with explicit private environment files.
Uses the checked-in isolated schema fixture; rows are committed and counted
before measurement, then the fixture removes only its own core namespace.
Seed setup is an owner-installed fixture, never ingestion/API throughput.
"""
import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import platform
import subprocess
import sys
from threading import Event, Thread, Barrier
import time
from uuid import UUID, uuid4

ROOT = Path(__file__).resolve().parents[1]


def completeness(status, value):
    if not 200 <= status < 300:
        return 'error'
    def incomplete(item):
        if isinstance(item, dict):
            return any((key in {'status', 'gis_status', 'projection_status', 'projection_state'} and
                val in ('pending', 'partial', 'incomplete', 'failed', 'unavailable')) or
                (key == 'truncated' and val is True) or incomplete(val) for key, val in item.items())
        if isinstance(item, list):
            return any(incomplete(v) for v in item)
        return False
    return 'incomplete' if incomplete(value) else 'complete'


def summarize(samples, elapsed):
    seconds = sorted(s['seconds'] for s in samples)
    def percentile(values, q):
        return values[max(0, math.ceil(len(values)*q)-1)] if values else None
    counts = Counter(s['outcome'] for s in samples)
    separated = {}
    for outcome in ('complete', 'incomplete', 'error'):
        values = sorted(s['seconds'] for s in samples if s['outcome'] == outcome)
        separated[outcome] = {'attempts': len(values), 'p50_seconds': percentile(values, .5),
            'p95_seconds': percentile(values, .95)}
    return {'attempts': len(samples), 'complete': counts['complete'], 'incomplete': counts['incomplete'],
        'http_or_transport_errors': counts['error'], 'error_rate': counts['error']/len(samples) if samples else None,
        'incomplete_rate': counts['incomplete']/len(samples) if samples else None,
        'p50_seconds': percentile(seconds, .5), 'p95_seconds': percentile(seconds, .95),
        'latency_by_outcome': separated,
        'attempt_throughput_per_second': len(samples)/elapsed,
        'complete_throughput_per_second': counts['complete']/elapsed}


def run_case(s):
    """Called by the explicit pytest bridge; no authentication overrides."""
    import psutil
    from sqlalchemy import insert, select, text, update
    from fastapi.testclient import TestClient
    from lzo.auth import models as auth
    from lzo.auth.session import SessionService, SESSION_COOKIE, resolve_identity
    from lzo.ontology.models import bundles
    from lzo.worker.runner import Runner
    from lzo.storage.registry import StorageRegistry
    from lzo.synchronization.delivery import deliver_once
    from lzo.gis.repository import apply_geometry
    from benchmark_multistore_seed import install_seed
    config = json.loads(os.environ['LZO_BENCHMARK_CONFIG'])
    from lzo.config import Settings
    deployed = Settings()
    s.config = s.config.model_copy(update={'job_batch_timeout_seconds': deployed.job_batch_timeout_seconds,
        'job_lease_seconds': deployed.job_lease_seconds})
    s.client.app.state.settings = s.config
    output = Path(config['output'])
    output.parent.mkdir(parents=True, exist_ok=True)
    result = {'format_version': 1, 'started_at': datetime.now(timezone.utc).isoformat(),
        'source_nature': 'synthetic', 'transport': 'in-process Starlette TestClient ASGI; real SQL/Fuseki/S3 HTTP',
        'identity_setup': f"{config['users']} distinct persisted fixture identities and encrypted backend sessions; no auth override; not live OIDC login performance",
        'seed_boundary': 'owner-installed committed synthetic fixture SQL/RDF, real GIS handler/receipts; excluded from API latency',
        'measurement_boundary': 'ASGI request/response including current backend authorization, native store I/O and bounded query children',
        'namespace_lifecycle': 'unique isolated core test schema removed after counts/measurement; synthetic domain/RDF evidence retained',
        'storage_configuration': {'mode': s.config.storage_mode, 'blob_provider': s.config.blob_provider},
        'runtime_limits': {name: getattr(s.config, name) for name in ('list_page_size', 'list_max_page_size',
            'query_timeout_ms', 'query_max_rows', 'query_max_quads', 'query_work_limit', 'query_memory_mb',
            'query_input_bytes', 'query_output_bytes', 'query_read_total_bytes',
            'job_batch_timeout_seconds', 'job_lease_seconds')},
        'workload': config, 'versions': {name: importlib.metadata.version(name) for name in ('httpx', 'SQLAlchemy', 'rdflib', 'fastapi', 'psycopg')},
        'hardware': {'cpu_description': config['hardware_label'] or platform.processor(), 'physical_cores': psutil.cpu_count(logical=False),
            'logical_cpus': psutil.cpu_count(), 'memory_gib': psutil.virtual_memory().total/1024**3,
            'platform': platform.platform(), 'standard_acceptance': '8 CPU cores/32 GB/SSD; current host is different'},
        'samples': [], 'state': 'preparing'}
    result['versions']['python'] = platform.python_version()
    result['versions']['pinned_runtime_manifest'] = json.loads((ROOT/'infra/versions.lock.json').read_text())['runtime']
    with s.app.connect() as connection:
        result['versions']['actual_postgresql'] = connection.execute(text('SELECT version()')).scalar_one()
        result['versions']['actual_postgis_extension'] = connection.execute(text("SELECT extversion FROM pg_extension WHERE extname='postgis'")).scalar_one()
    git = config['git_executable']
    result['code'] = {'revision': subprocess.check_output([git, 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()}
    diff = subprocess.check_output([git, 'diff', 'HEAD', '--binary'], cwd=ROOT, stderr=subprocess.DEVNULL)
    untracked = subprocess.check_output([git, 'ls-files', '--others', '--exclude-standard'], cwd=ROOT, text=True).splitlines()
    hasher = hashlib.sha256(diff)
    for name in sorted(untracked):
        hasher.update(name.encode()); hasher.update((ROOT/name).read_bytes())
    result['code'].update(dirty=bool(diff or untracked), working_tree_content_sha256=hasher.hexdigest())
    def save():
        output.write_text(json.dumps(result, indent=2, ensure_ascii=False)+'\n', encoding='utf-8')
    save()
    try:
        draft = s.create({'classes': [{'iri': 'urn:benchmark:Entity', 'label': 'Synthetic benchmark entity'}],
            'relations': [{'iri': 'urn:benchmark:next', 'label': 'Synthetic relation',
                'domain_iris': ['urn:benchmark:Entity'], 'range_iris': ['urn:benchmark:Entity']}]})
        assert s.publish(draft, s.approve(draft)).status_code == 202
        assert Runner(s.app, s.config).run_once()
        with s.transaction() as uow:
            bundle_id = uow.session.execute(select(bundles.c.id)).scalar_one()
        s.grant('alice', 'editor', scope='farm_a')
        s.login()
        created = s.client.post('/api/v1/objects', headers=s.headers(), json={'bundle_id': str(bundle_id), 'create': {
            'resource_id': str(uuid4()), 'class_iris': ['urn:benchmark:Entity'], 'display_name': 'Synthetic seed template',
            'owner_scope_id': str(s.scopes['farm_a']), 'related_scope_ids': [], 'state': 'active',
            'data_nature': 'synthetic', 'assertions': [], 'decision_ref': None}})
        assert created.status_code == 201, created.text
        registry = StorageRegistry.from_settings(s.config)
        registry._engines['core'].dispose(); registry._engines['core'] = s.app
        deliver_once(registry, 'gis', apply_geometry)
        Runner(s.app, s.config).run_once()
        seed_start = time.perf_counter()
        result['state'] = 'seeding'
        save()
        def seed_progress(value):
            result['seed_progress'] = value
            save()
        ids, seed = install_seed(s, bundle_id, UUID(created.json()['revision']['resource_id']), config, registry, seed_progress)
        result['seed'] = {**seed, 'setup_seconds': time.perf_counter()-seed_start}
        save()
        if (config['observations'] or config['media_bytes']) and seed['full_baseline_ready']:
            auxiliary_started = time.perf_counter()
            import io
            from datetime import timedelta
            from lzo.ingestion.sources import SourceService
            from lzo.ingestion.schemas import SourceCreate
            from lzo.telemetry.service import TelemetryService
            from lzo.telemetry.schemas import BatchInput, ObservationInput
            from lzo.artifacts.service import ArtifactService
            from lzo.storage.blobs import get_blob_store
            from lzo.contracts import ResourceScope
            from lzo.synchronization.delivery import poll_deliveries
            with s.transaction() as uow:
                uow.session.execute(update(auth.roles).where(auth.roles.c.id == s.roles['editor']).values(
                    actions=['view', 'edit', 'source.manage', 'source.read', 'catalog.manage', 'evidence.manage', 'evidence.read']))
                uow.session.execute(update(auth.authorization_state).values(version=auth.authorization_state.c.version+1))
                uow.commit()
            with s.transaction() as uow:
                uow.ontology_settings = s.config
                source = SourceService(uow).create(uow, s.actor('alice'), SourceCreate(source_system='synthetic-benchmark',
                    source_instance=str(uuid4()), collection='observations', kind='file',
                    scope=ResourceScope(None, s.scopes['farm_a'], (), 'data'), data_nature='synthetic'))
                uow.commit()
            records = []
            base = datetime(2026, 1, 15, tzinfo=timezone.utc)
            for number in range(config['observations']):
                records.append(ObservationInput(source_record_id=str(number), object_id=ids[number % len(ids)], device_id=ids[number % len(ids)],
                    point_id='synthetic', metric_id='synthetic-temperature', raw_value='12.5', normalized_value='12.5',
                    sampled_at=base+timedelta(seconds=number), data_nature='synthetic'))
            if config['media_bytes']:
                with s.transaction() as uow:
                    store = get_blob_store(s.config)
                    artifact = ArtifactService(uow, store, s.config).register_upload(s.actor('alice'), s.ids_by_name['object_a'],
                        store.put(io.BytesIO(b'x'*config['media_bytes']), 'application/octet-stream'),
                        filename='synthetic-nonplayable-media.bin', metadata={'synthetic': True, 'non_playable': True})
                    uow.commit()
                records.append(ObservationInput(kind='media', source_record_id='synthetic-media', object_id=ids[0], device_id=ids[0],
                    point_id='synthetic', raw_value=None, normalized_value=None, sampled_at=base,
                    end_at=base+timedelta(seconds=10), data_nature='synthetic', artifact_version_id=artifact.version_id, channel_id='synthetic-channel'))
            for offset in range(0, len(records), 1000):
                with s.transaction() as uow:
                    uow.ontology_settings = s.config
                    TelemetryService(uow, registry).accept(s.actor('alice'), BatchInput(source_id=source.id,
                        records=tuple(records[offset:offset+1000])), str(uuid4()))
                    uow.commit()
            poll_deliveries(registry)
            with s.transaction() as uow:
                uow.ontology_settings = s.config
                page = TelemetryService(uow, registry).query(s.actor('alice'), object_id=ids[0], history=True)
                result['seed']['observation_media_input'] = {'requested_observations': config['observations'],
                    'requested_media_bytes': config['media_bytes'], 'sample_object_read_status': page['status'],
                    'sample_object_visible_records': len(page['items']), 'non_playable_synthetic_media': True}
            with registry.engine('timeseries').connect() as connection:
                actual = dict(connection.execute(text('SELECT kind,count(*) FROM telemetry_samples WHERE source_id=:source GROUP BY kind'),
                    {'source': source.id}).all())
            result['seed']['observation_media_input']['persisted_by_kind'] = actual
            if config['media_bytes']:
                result['seed']['observation_media_input']['artifact'] = {'version_id': str(artifact.version_id),
                    'sha256': artifact.sha256, 'size_bytes': artifact.size_bytes}
            result['seed']['observation_media_setup_seconds'] = time.perf_counter()-auxiliary_started
            save()
        elif config['observations'] or config['media_bytes']:
            result['seed']['observation_media_input'] = {'state': 'not_executed', 'reason': 'entity projection baseline incomplete',
                'requested_observations': config['observations'], 'requested_media_bytes': config['media_bytes']}
            save()
        clients = []
        with s.transaction() as uow:
            role = uuid4()
            uow.session.execute(insert(auth.roles).values(id=role, name='benchmark-current-view-edit', actions=['view', 'edit'], revision=1))
            for number in range(config['users']):
                subject = resolve_identity(uow, 'urn:benchmark:fixture-issuer', f'benchmark-{number}', f'Benchmark {number}', f'benchmark-{number}@example.invalid')
                uow.session.execute(insert(auth.grants).values(id=uuid4(), user_id=subject, role_id=role,
                    scope_id=s.scopes['farm_a'], security_class='data', revision=1))
                # Query definitions remain governed by the same current schema access.
                schema_role = uuid4()
                uow.session.execute(insert(auth.roles).values(id=schema_role, name=f'benchmark-schema-{number}', actions=['view'], revision=1))
                uow.session.execute(insert(auth.grants).values(id=uuid4(), user_id=subject, role_id=schema_role,
                    security_class='schema', global_scope=True, revision=1))
                cookie = SessionService(uow, s.config).create(subject, {'synthetic': True})
                client = TestClient(s.client.app, base_url='https://testserver')
                client.cookies.set(SESSION_COOKIE, cookie)
                clients.append((str(subject), client))
            uow.session.execute(update(auth.authorization_state).values(version=auth.authorization_state.c.version+1))
            uow.commit()
        result['authorized_subject_ids'] = [subject for subject, _ in clients]
        result['state'] = 'authorizing_users'
        save()
        for _, client in clients:
            assert client.get(f'/api/v1/objects/{ids[0]}').status_code == 200
        operation_defs = {
            'object': 'GET /api/v1/objects/{id}', 'map': 'GET /api/v1/geometry?bbox=113,29,116,32; first authorized page at recorded list_page_size',
            'graph': 'GET /api/v1/graph/neighborhood?resource_id={id}&hops=1',
            'sparql': 'POST /api/v1/query/sparql ASK { <id> <urn:benchmark:next> ?target } with configured bounded timeout',
            'history': 'GET /api/v1/history/{id}/revisions/1', 'domain_history': 'GET /api/v1/objects/{id}/data',
            'accept_sync': 'POST a synthetic semantic object; real GIS delivery and one normal RDF worker tick; GET its map result'}
        result['operation_definitions'] = operation_defs
        result['state'] = 'measuring'
        save()
        resources = []
        done = Event()
        monitored = [psutil.Process(), *[psutil.Process(pid) for pid in config['service_pids'] if psutil.pid_exists(pid)]]
        for process in monitored:
            process.cpu_percent(None)
        def sample():
            while not done.wait(.5):
                values = []
                for process in monitored:
                    try:
                        values.append({'pid': process.pid, 'rss_bytes': process.memory_info().rss, 'cpu_percent': process.cpu_percent(None)})
                    except psutil.NoSuchProcess:
                        pass
                resources.append({'host_cpu_percent': psutil.cpu_percent(None), 'host_used_memory_bytes': psutil.virtual_memory().used,
                    'selected_processes': values})
        thread = Thread(target=sample, daemon=True); thread.start()
        def perform(client, operation, identifier):
            csrf = client.get('/api/v1/session').json()['csrf_token'] if operation in ('sparql', 'accept_sync') else None
            headers = {'X-CSRF-Token': csrf, 'Idempotency-Key': str(uuid4())}
            start = time.perf_counter()
            try:
                combined_value = None
                if operation == 'object': response = client.get(f'/api/v1/objects/{identifier}')
                elif operation == 'map': response = client.get('/api/v1/geometry', params={'bbox': '113,29,116,32'})
                elif operation == 'graph': response = client.get('/api/v1/graph/neighborhood', params={'resource_id': str(identifier), 'hops': 1})
                elif operation == 'sparql': response = client.post('/api/v1/query/sparql', headers=headers, json={
                    'query': f'ASK {{ <urn:lzo:object:{identifier}> <urn:benchmark:next> ?target }}', 'timeout_ms': config['query_timeout_ms'], 'max_rows': 10})
                elif operation == 'history': response = client.get(f'/api/v1/history/{identifier}/revisions/1')
                elif operation == 'domain_history': response = client.get(f'/api/v1/objects/{identifier}/data')
                else:
                    new_id = uuid4()
                    response = client.post('/api/v1/objects', headers=headers, json={'bundle_id': str(bundle_id), 'create': {
                        'resource_id': str(new_id), 'class_iris': ['urn:benchmark:Entity'], 'display_name': 'Synthetic measured acceptance',
                        'owner_scope_id': str(s.scopes['farm_a']), 'related_scope_ids': [], 'state': 'active',
                        'data_nature': 'synthetic', 'assertions': [], 'decision_ref': None}})
                    if response.status_code == 201:
                        deliver_once(registry, 'gis', apply_geometry)
                        Runner(s.app, s.config).run_once()
                        response = client.get(f'/api/v1/geometry/{new_id}')
                        detail = client.get(f'/api/v1/objects/{new_id}')
                        if detail.status_code == 200:
                            combined_value = {'map': response.json(), 'object': detail.json()}
                        else:
                            response = detail
                value = combined_value if combined_value is not None else response.json()
                return {'seconds': time.perf_counter()-start, 'status': response.status_code,
                    'outcome': completeness(response.status_code, value), 'error_code': value.get('code') if isinstance(value, dict) else None}
            except Exception as error:
                return {'seconds': time.perf_counter()-start, 'status': None, 'outcome': 'error', 'error_type': type(error).__name__}
        operations = config['operations']
        for _ in range(config['warmup']):
            for operation in operations:
                perform(clients[0][1], operation, ids[0])
        barrier = Barrier(len(clients))
        def user(index):
            subject, client = clients[index]
            samples = []
            barrier.wait()
            for repetition in range(config['repetitions']):
                for operation in operations:
                    entry = perform(client, operation, ids[(index*17+repetition) % len(ids)])
                    entry.update(operation=operation, user=subject, repetition=repetition)
                    samples.append(entry)
            return samples
        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(clients)) as executor:
            samples = [entry for batch in executor.map(user, range(len(clients))) for entry in batch]
        elapsed = time.perf_counter()-started
        done.set(); thread.join()
        grouped = defaultdict(list)
        for entry in samples: grouped[entry['operation']].append(entry)
        result.update(state='measured', elapsed_seconds=elapsed, samples=samples,
            summary=summarize(samples, elapsed), operations={name: summarize(values, elapsed) for name,values in grouped.items()},
            resource_measurement={'scope': 'client Python process plus explicit service PIDs; host CPU/memory includes unrelated workloads; warmup plus measured operations, .5s sampling',
                'service_pids': config['service_pids'], 'samples': resources})
        save()
        for _, client in clients: client.close()
        registry._engines.pop('core'); registry.close()
    except Exception as error:
        result.update(state='incomplete', failure_type=type(error).__name__, failure_message='See private pytest log; no success inferred')
        save()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--entities', type=int, default=10000)
    parser.add_argument('--relations', type=int, default=100000)
    parser.add_argument('--users', type=int, default=10)
    parser.add_argument('--seed', type=int, default=20261023)
    parser.add_argument('--seed-seconds', type=float, default=900)
    parser.add_argument('--warmup', type=int, default=1)
    parser.add_argument('--repetitions', type=int, default=3)
    parser.add_argument('--query-timeout-ms', type=int, default=10000)
    parser.add_argument('--operations', nargs='+', default=['object', 'map', 'graph', 'sparql', 'history', 'domain_history', 'accept_sync'])
    parser.add_argument('--observations', type=int, default=0, help='Synthetic telemetry records installed via the real accept path')
    parser.add_argument('--media-bytes', type=int, default=0, help='Synthetic non-playable private artifact input size')
    parser.add_argument('--service-pid', type=int, action='append', default=[], dest='service_pids')
    parser.add_argument('--hardware-label', default='')
    parser.add_argument('--git-executable', default='git')
    parser.add_argument('--output', required=True)
    parser.add_argument('--basetemp', required=True)
    args = parser.parse_args()
    if args.entities < 2 or not 1 <= args.users <= 100 or args.relations < 0 or args.relations > args.entities*(args.entities-1):
        parser.error('Invalid distinct-entity/relation/user workload')
    if args.repetitions < 1 or args.warmup < 0 or args.seed_seconds <= 0 or args.observations < 0 or args.media_bytes < 0:
        parser.error('Use positive repetitions/seed budget and nonnegative warmup/input sizes')
    if set(args.operations) - {'object', 'map', 'graph', 'sparql', 'history', 'domain_history', 'accept_sync'}:
        parser.error('Unknown operation')
    if Path(args.basetemp).exists() or not Path(args.basetemp).resolve().is_relative_to(ROOT/'.runtime'):
        parser.error('Use a NEW owned .runtime basetemp')
    os.environ['LZO_BENCHMARK_CONFIG'] = json.dumps(vars(args))
    sys.path.insert(0, str(ROOT/'scripts'))
    import pytest
    return pytest.main([str(ROOT/'backend/tests/integration/benchmark_multistore_case.py'), '-q', '-s', '--basetemp', args.basetemp])


if __name__ == '__main__':
    raise SystemExit(main())
