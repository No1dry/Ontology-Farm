"""Create/check one synthetic native persistence probe; manifest stays private.

Seed uses actual HTTP commands, independent schema approval and the real native
worker. Check traverses persisted current authorization, semantic/GIS/history,
private S3 bytes, catalog, telemetry and managed RDF. SQL postmaster timestamps
and four schema heads identify the restart boundary. Identity setup is an owned
synthetic fixture, not a live OIDC browser-login test.
"""
import argparse
from dataclasses import asdict
import hashlib
import io
import json
from pathlib import Path
import time
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, insert, select, text, update
from lzo.config import Settings
from lzo.db import PgUnitOfWork
from lzo.auth import models
from lzo.auth.permissions import permission_context
from lzo.auth.session import resolve_identity, SessionService, SESSION_COOKIE
from lzo.storage.registry import StorageRegistry
from lzo.semantic.fuseki import FusekiRepository, graph_iri


def write_headers(client, revision=None):
    csrf = client.get('/api/v1/session').json()['csrf_token']
    return {'X-CSRF-Token': csrf, 'Idempotency-Key': str(uuid4()),
        **({'If-Match': f'"{revision}"'} if revision is not None else {})}


def accepted(response, status):
    if response.status_code != status:
        raise RuntimeError('Synthetic native HTTP command failed: status '+str(response.status_code))
    return response.json()


def until(command, predicate, *, seconds=180):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        value = command()
        if predicate(value):
            return value
        time.sleep(.5)
    raise RuntimeError('Owned native worker did not reach the required current state within the bound')


def provider_fingerprint(settings):
    from lzo.auth.oidc import tls_verify
    with httpx.Client(verify=tls_verify(settings), timeout=20) as client:
        discovery = accepted(client.get(settings.oidc_discovery_url), 200)
        jwks = accepted(client.get(discovery['jwks_uri']), 200)
        jwks['keys'] = sorted(jwks['keys'], key=lambda value: value.get('kid', ''))
        token = accepted(client.post(discovery['token_endpoint'], data={'grant_type': 'client_credentials',
            'client_id': settings.identity_admin_client_id, 'client_secret': settings.identity_admin_client_secret}), 200)
        response = client.get(settings.identity_admin_url+'/clients', headers={'Authorization': 'Bearer '+token['access_token']})
        assert response.status_code == 403
        return hashlib.sha256(json.dumps(jwks, sort_keys=True).encode()).hexdigest()


def owned_process_snapshot():
    """Safe lifecycle evidence: identifiers/times only, never command lines."""
    import psutil
    root = Path(__file__).resolve().parents[1]
    result = {}
    for label in ('api', 'worker', 'keycloak'):
        record = json.loads((root/'.runtime/acceptance-services'/f'{label}.json').read_text())
        process = psutil.Process(record['pid'])
        assert abs(process.create_time()-record['created']) < .01
        assert Path(process.cwd()).resolve() == Path(record['cwd']).resolve()
        result[label] = {'pid': process.pid, 'created_unix': process.create_time()}
        if label in ('api', 'worker'):
            environment = process.environ()
            result[label]['declared_batch_seconds'] = int(environment.get('LZO_JOB_BATCH_TIMEOUT_SECONDS', '30'))
            result[label]['declared_lease_seconds'] = int(environment.get('LZO_JOB_LEASE_SECONDS', '60'))
        if label == 'keycloak':
            result[label]['java_children'] = [{'pid': child.pid, 'created_unix': child.create_time()}
                for child in process.children(recursive=True) if child.name().lower() == 'java.exe']
    for label, pid in (
            ('s3', int((root/'.runtime/object-store/server.pid').read_text())),
            ('fuseki', json.loads((root/'.runtime/multistore-fuseki/ownership.json').read_text())['pid'])):
        process = psutil.Process(pid)
        expected = root/('.runtime/object-store' if label == 's3' else '.runtime/multistore-fuseki')
        assert Path(process.cwd()).resolve() == expected.resolve()
        result[label] = {'pid': process.pid, 'created_unix': process.create_time()}
    result['writers'] = {}
    for record in (root/'.runtime/acceptance-services/writers').glob('*.json'):
        value = json.loads(record.read_text())
        process = psutil.Process(value['pid'])
        assert process.create_time() == value['created']
        assert Path(value['workspace']).resolve() == root
        result['writers'][value['service']] = {'pid': value['pid'], 'created_unix': value['created']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['seed', 'check', 'outage'])
    parser.add_argument('--manifest', type=Path, required=True, help='Private NEW manifest for seed; contains a session cookie')
    parser.add_argument('--unavailable', choices=['core', 's3'], help='Expected dependency during an explicitly owned bounded outage')
    parser.add_argument('--api', default='http://127.0.0.1:28000')
    parser.add_argument('--result', type=Path, required=True, help='Sanitized result JSON')
    args = parser.parse_args()
    if args.action == 'outage' and not args.unavailable:
        parser.error('outage requires --unavailable')
    from lzo.storage.migration import runtime_settings
    settings = runtime_settings()
    if settings.storage_mode != 'split' or settings.blob_provider != 's3':
        raise RuntimeError('Persistence probe requires explicit split stores and private S3 configuration')
    registry = StorageRegistry.from_settings(settings)
    engine = registry.engine('core')
    repository = FusekiRepository(settings.fuseki_url)
    try:
        if args.action == 'outage':
            value = json.loads(args.manifest.read_text())
            dependency = 'store_core' if args.unavailable == 'core' else 'files'
            started = time.perf_counter()
            with httpx.Client(base_url=args.api, cookies={SESSION_COOKIE: value['cookie']}, timeout=30) as client:
                ready = client.get('/health/ready')
                assert ready.status_code == 503 and ready.json()['dependencies'][dependency] == 'unavailable'
                output = {'synthetic': True, 'expected_unavailable': args.unavailable, 'ready_http_status': ready.status_code,
                    'dependencies': ready.json()['dependencies']}
                if args.unavailable == 's3':
                    content = client.get(f"/api/v1/artifacts/{value['artifact_version_id']}/content")
                    assert content.status_code == 503
                    output['private_artifact_http_status'] = content.status_code
            output['probe_seconds'] = time.perf_counter()-started
            args.result.write_text(json.dumps(output, indent=2)+'\n', encoding='utf-8')
            print('Owned outage returned unavailable; no fallback or persistence success inferred')
            return
        if args.action == 'seed':
            if args.manifest.exists():
                raise RuntimeError('Probe manifest must be new')
            identifier, scope_id, role_id = uuid4(), uuid4(), uuid4()
            with PgUnitOfWork(engine) as uow, permission_context(uow):
                user = resolve_identity(uow, 'urn:lzo:acceptance:fixture', str(uuid4()), 'Synthetic persistence probe', 'probe@example.invalid')
                reviewer = resolve_identity(uow, 'urn:lzo:acceptance:fixture', str(uuid4()), 'Independent synthetic reviewer', 'reviewer@example.invalid')
                uow.session.execute(insert(models.roles).values(id=role_id, name='native-persistence-probe-'+identifier.hex[:8],
                    actions=['view', 'edit', 'catalog.manage', 'source.manage', 'source.read'], revision=1))
                uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=user, role_id=role_id, scope_id=scope_id, security_class='data', revision=1))
                for subject, actions in ((user, ['view', 'schema.edit', 'schema.publish']), (reviewer, ['view', 'schema.review'])):
                    role = uuid4()
                    uow.session.execute(insert(models.roles).values(id=role, name='native-schema-'+role.hex[:8], actions=actions, revision=1))
                    uow.session.execute(insert(models.grants).values(id=uuid4(), user_id=subject, role_id=role, security_class='schema', global_scope=True, revision=1))
                uow.session.execute(update(models.authorization_state).values(version=models.authorization_state.c.version+1))
                cookie = SessionService(uow, settings).create(user, {'synthetic': True})
                review_cookie = SessionService(uow, settings).create(reviewer, {'synthetic': True})
                uow.commit()
            with httpx.Client(base_url=args.api, cookies={SESSION_COOKIE: cookie}, timeout=60) as client, \
                    httpx.Client(base_url=args.api, cookies={SESSION_COOKIE: review_cookie}, timeout=60) as review:
                draft = accepted(client.post('/api/v1/schema/drafts', headers=write_headers(client), json={'base_release_id': None,
                    'document': {'classes': [{'iri': 'urn:lzo:acceptance:NativeEntity', 'label': 'Synthetic native restart entity'}]}}), 201)
                impact = accepted(client.post(f"/api/v1/schema/drafts/{draft['id']}/validate", headers=write_headers(client, draft['revision'])), 200)
                approval = accepted(client.post(f"/api/v1/schema/drafts/{draft['id']}/submit", headers=write_headers(client, draft['revision']),
                    json={'impact_digest': impact['digest']}), 201)
                approval = accepted(review.get(f"/api/v1/approvals/{approval['id']}"), 200)
                approval = accepted(review.post(f"/api/v1/approvals/{approval['id']}/decisions", headers=write_headers(review, approval['revision']),
                    json={'decision': 'approve', 'comment': 'Independent synthetic native acceptance'}), 200)
                accepted(client.post('/api/v1/schema/releases', headers=write_headers(client, draft['revision']),
                    json={'draft_id': draft['id'], 'approval_id': approval['id']}), 202)
                until(lambda: accepted(client.get('/api/v1/schema/releases'), 200), lambda value: any(v['state']=='published' for v in value['items']))
                with engine.connect() as connection:
                    bundle_id = connection.execute(text('SELECT active_bundle_id FROM ontology_catalog')).scalar_one()
                accepted(client.post('/api/v1/objects', headers=write_headers(client), json={'bundle_id': str(bundle_id), 'create': {
                    'resource_id': str(identifier), 'class_iris': ['urn:lzo:acceptance:NativeEntity'], 'display_name': 'Synthetic native persistence entity',
                    'owner_scope_id': str(scope_id), 'related_scope_ids': [], 'state': 'active', 'data_nature': 'synthetic', 'assertions': [], 'decision_ref': None}}), 201)
                geometry = {'resource_id': str(identifier), 'expected_revision': 1, 'source_crs': 'EPSG:4326',
                    'geojson': {'type': 'Point', 'coordinates': [114.25, 30.25]}, 'source_support': {
                        'support_id': str(uuid4()), 'source_id': None, 'source_version': 'synthetic-native-v1', 'raw_value': {}}}
                accepted(client.put(f'/api/v1/geometry/{identifier}', headers=write_headers(client, 1),
                    json={'bundle_id': str(bundle_id), 'geometry': geometry}), 200)
                content = b'Original synthetic native restart persistence probe\n'
                artifact = accepted(client.post(f'/api/v1/artifacts?resource_id={identifier}&filename=synthetic-probe.txt',
                    headers={**write_headers(client, 2), 'Content-Type': 'text/plain'}, content=content), 201)
                until(lambda: accepted(client.get(f'/api/v1/objects/{identifier}'), 200), lambda value: value['projection_state']=='synced')
                scope = {'resource_id': None, 'owner_scope_id': str(scope_id), 'related_scope_ids': [], 'security_class': 'data'}
                model = accepted(client.post('/api/v1/catalog/models', headers=write_headers(client), json={'scope': scope,
                    'metadata': {'exact_model': 'Synthetic native persistence model', 'synthetic': True}}), 201)
                until(lambda: accepted(client.get(f"/api/v1/catalog/models/{model['id']}"), 200), lambda value: value['projection_status']=='current')
                source = accepted(client.post('/api/v1/sources', headers=write_headers(client), json={'source_system': 'synthetic-native',
                    'source_instance': str(identifier), 'collection': 'restart-probe', 'kind': 'file', 'scope': scope, 'data_nature': 'synthetic'}), 201)
                batch = accepted(client.post('/api/v1/telemetry/batches', headers=write_headers(client), json={'source_id': source['id'], 'records': [{
                    'source_record_id': 'one', 'object_id': str(identifier), 'device_id': str(identifier), 'point_id': 'synthetic',
                    'metric_id': 'synthetic-temperature', 'raw_value': '12.5', 'normalized_value': '12.5',
                    'sampled_at': '2026-01-15T12:00:00Z', 'data_nature': 'synthetic'}]}), 202)
                until(lambda: accepted(client.get('/api/v1/telemetry/observations', params={'object_id': str(identifier)}), 200),
                    lambda value: value['status']=='current' and len(value['items'])==1)
            args.manifest.write_text(json.dumps({'resource_id': str(identifier), 'artifact_version_id': artifact['version_id'],
                'sha256': artifact['sha256'], 'cookie': cookie, 'catalog_id': model['id'], 'batch_id': batch['id'],
                'idp_jwks_sha256': provider_fingerprint(settings)}), encoding='utf-8')
        value = json.loads(args.manifest.read_text())
        output = {'probe_id': value['resource_id'], 'synthetic': True, 'action': args.action,
            'scope': 'normal public core/domain deployment, actual HTTP semantic/GIS/history/S3/catalog/telemetry reads and managed RDF after real worker progress; fixture sessions, not live OIDC browser login'}
        with httpx.Client(base_url=args.api, cookies={SESSION_COOKIE: value['cookie']}, timeout=20) as client:
            ready = client.get('/health/ready')
            assert ready.status_code == 200
            history = client.get(f"/api/v1/history/{value['resource_id']}/revisions/1")
            content = client.get(f"/api/v1/artifacts/{value['artifact_version_id']}/content")
            assert history.status_code == 200 and content.status_code == 200
            assert hashlib.sha256(content.content).hexdigest() == value['sha256']
            obj = accepted(client.get(f"/api/v1/objects/{value['resource_id']}"), 200)
            geometry = accepted(client.get(f"/api/v1/geometry/{value['resource_id']}"), 200)
            model = accepted(client.get(f"/api/v1/catalog/models/{value['catalog_id']}"), 200)
            observations = accepted(client.get('/api/v1/telemetry/observations', params={'object_id': value['resource_id']}), 200)
            assert obj['revision']==3 and obj['projection_state']=='synced'
            assert geometry['gis_status']=='current' and geometry['features'][0]['geometry']['coordinates']==[114.25,30.25]
            assert model['projection_status']=='current' and observations['status']=='current' and len(observations['items'])==1
            output.update(api_ready=True, current_authorized_history=True, private_artifact_sha256=value['sha256'],
                object_revision=obj['revision'], gis_coordinates=[114.25,30.25], catalog_revision=model['revision'],
                observation_ids=[v['id'] for v in observations['items']])
        generation = repository.current_revision(UUID(value['resource_id']))
        assert generation['revision']==3
        triples = repository.read_graph(graph_iri(value['resource_id'], 'accepted'))
        assert any(t.object.value=='urn:lzo:acceptance:NativeEntity' for t in triples)
        output['managed_rdf_generation'] = {'revision': generation['revision'], 'digest': generation['digest'], 'accepted_triples': len(triples)}
        fingerprint = provider_fingerprint(settings)
        assert fingerprint == value['idp_jwks_sha256']
        output['identity_service'] = {'persistence_backend': 'owned native Keycloak dev-file database; not Compose PostgreSQL identity DB', 'jwks_sha256': fingerprint, 'service_account_token_issued': True,
            'client_administration_denied': True, 'fixture_session_preserved': True}
        output['worker_budgets'] = {'batch_seconds': settings.job_batch_timeout_seconds,
            'lease_seconds': settings.job_lease_seconds, 'production_defaults': {'batch_seconds': 30, 'lease_seconds': 60}}
        assert settings.job_lease_seconds > settings.job_batch_timeout_seconds
        output['owned_processes'] = owned_process_snapshot()
        output['stores'] = {}
        for domain in ('core', 'gis', 'timeseries', 'catalog'):
            with registry.engine(domain).connect() as connection:
                output['stores'][domain] = {'database': connection.execute(text('SELECT current_database()')).scalar_one(),
                    'postmaster_started_at': connection.execute(text('SELECT pg_postmaster_start_time()')).scalar_one().isoformat(),
                    'schema_version': connection.execute(text('SELECT version_num FROM alembic_version' if domain=='core' else 'SELECT version FROM store_schema_version')).scalar_one()}
        args.result.write_text(json.dumps(output, indent=2)+'\n', encoding='utf-8')
        print('Native persistence probe verified; private session manifest excluded from public result')
    finally:
        registry.close()


if __name__ == '__main__':
    main()
