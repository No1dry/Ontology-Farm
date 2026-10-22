"""Restore verified platform snapshots into new explicitly owned destinations.

Failed partial destinations are retained for inspection. Retry uses a fresh
namespace; this module never drops a database, source object or RDF dataset.
"""
import hashlib
import io
import json
from pathlib import Path
import re
from uuid import UUID, uuid4

import httpx
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url

from lzo.config import Settings
from lzo.governance.risk import digest_change
from lzo.storage.backup import DOMAINS, verify_backup_material
from lzo.storage.backup_format import BackupError, check_references, inventory, package_file, pg_command, read_inventory, parse_dataset
from lzo.storage.blobs import get_blob_store
from lzo.storage.migration import (_configuration, blob_identity, claim_blob_namespace,
    connection_identity, create_engine_for, identity, verify_blob_namespace)


def _rdf_digest(path):
    """Canonical quad reification retains blank-node links between named graphs."""
    from rdflib import Graph, BNode, URIRef, Literal
    from rdflib.compare import to_canonical_graph
    dataset, flattened = parse_dataset(path), Graph()
    predicates = [URIRef('urn:lzo:backup:' + term) for term in ('subject', 'predicate', 'object', 'graph')]
    ground = []
    for s, p, o, graph in dataset.quads((None, None, None, None)):
        default = graph == dataset.default_graph.identifier
        terms = (s, p, o) if default else (s, p, o, graph)
        if not any(isinstance(term, BNode) for term in terms):
            ground.append(' '.join(term.n3() for term in terms) + (' @default' if default else ''))
            continue
        quad = BNode()
        graph_value = Literal('default', datatype=URIRef('urn:lzo:backup:default-marker')) if default else graph
        for predicate, value in zip(predicates, (s, p, o, graph_value)):
            flattened.add((quad, predicate, value))
    lines = sorted(' '.join(term.n3() for term in triple) for triple in to_canonical_graph(flattened))
    return digest_change({'ground': sorted(ground), 'blank_connected': lines})


class RestoreService:
    def __init__(self, admin, pg_bin, authority_key):
        self.admin, self.pg_bin, self.authority_key = admin, Path(pg_bin).resolve(), Path(authority_key).resolve()

    def restore(self, manifest_path, namespace, destination):
        if not isinstance(namespace, str) or not re.fullmatch(r'lzo_restore_[a-z0-9]{8,24}', namespace):
            raise BackupError('INVALID_RESTORE_NAMESPACE')
        destination = Path(destination).resolve()
        if destination.exists():
            raise BackupError('TARGET_EXISTS')
        manifest_path = Path(manifest_path).resolve()
        report = verify_backup_material(manifest_path, self.authority_key, self.pg_bin)
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        source = Settings.model_validate_json(package_file(manifest_path.parent, manifest['files']['settings']['path']).read_text(encoding='utf-8'))
        names = {domain: namespace + '_' + domain for domain in DOMAINS}
        with self.admin.connect() as c:
            if c.execute(text('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=ANY(:names))'), {'names': list(names.values())}).scalar_one():
                raise BackupError('TARGET_EXISTS')
        endpoint_base = source.fuseki_url.rsplit('/', 1)[0]
        datasets = httpx.get(endpoint_base + '/$/datasets', timeout=10, trust_env=False)
        datasets.raise_for_status()
        if any(row['ds.name'].strip('/') == namespace for row in datasets.json()['datasets']):
            raise BackupError('TARGET_EXISTS')
        restore_id = uuid4()
        overrides = {'file_root': destination / 'files', 'blob_staging_path': destination / 'staging',
            'fuseki_url': endpoint_base + '/' + namespace, 's3_key_prefix': 'restores/' + namespace}
        for domain in DOMAINS:
            for kind in ('database_url', 'migration_database_url'):
                key = ('' if domain == 'core' else domain + '_') + kind
                url = make_url(getattr(source, key))
                overrides[key] = url.set(database=names[domain]).render_as_string(hide_password=False)
        settings = source.model_copy(update=overrides)
        destination.mkdir(parents=True, exist_ok=True)
        state_path = destination / 'ownership.json'
        state = {'id': str(restore_id), 'namespace': namespace, 'databases': names,
            'rdf_dataset': namespace, 'blob': blob_identity(settings), 'backup_id': report['backup_id'], 'state': 'restoring'}
        state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
        engines = {}
        try:
            claim_blob_namespace(settings, 'restore', restore_id)
            blob_store = get_blob_store(settings)
            with self.admin.connect().execution_options(isolation_level='AUTOCOMMIT') as c:
                quote = c.dialect.identifier_preparer.quote
                for domain, name in names.items():
                    owner = 'lzo_owner' if domain == 'core' else 'lzo_' + domain + '_owner'
                    app = 'lzo_app' if domain == 'core' else 'lzo_' + domain + '_app'
                    c.exec_driver_sql('CREATE DATABASE ' + quote(name) + ' OWNER ' + quote(owner) + ' TEMPLATE template0')
                    c.exec_driver_sql('REVOKE ALL ON DATABASE ' + quote(name) + ' FROM PUBLIC')
                    c.exec_driver_sql('GRANT CONNECT ON DATABASE ' + quote(name) + ' TO ' + quote(owner) + ',' + quote(app))
                    if domain == 'timeseries':
                        c.exec_driver_sql('GRANT CONNECT ON DATABASE ' + quote(name) + ' TO lzo_timeseries_maintainer')
                    target_admin = create_engine(self.admin.url.set(database=name))
                    try:
                        with target_admin.begin() as target:
                            target.execute(text('REVOKE CREATE ON SCHEMA public FROM PUBLIC'))
                            for extension in manifest['stores'][domain]['extensions']:
                                if extension['extname'] == 'postgis':
                                    target.execute(text('CREATE EXTENSION postgis'))
                            actual = {r[0]: r[1] for r in target.execute(text('SELECT extname,extversion FROM pg_extension'))}
                            if any(actual.get(r['extname']) != r['extversion'] for r in manifest['stores'][domain]['extensions']):
                                raise BackupError('RESTORE_EXTENSION_VERSION_MISMATCH')
                    finally:
                        target_admin.dispose()
                    owner_url = getattr(settings, ('' if domain == 'core' else domain + '_') + 'migration_database_url')
                    self._restore_database(manifest_path.parent, manifest, domain, owner_url, destination)
                    engines[domain] = create_engine_for(owner_url)
            created = httpx.post(endpoint_base + '/$/datasets', data={'dbName': namespace, 'dbType': 'tdb2'}, timeout=30, trust_env=False)
            created.raise_for_status()
            empty = httpx.get(settings.fuseki_url + '/query', params={'query': 'ASK { { ?s ?p ?o } UNION { GRAPH ?g { ?s ?p ?o } } }'}, timeout=10, trust_env=False)
            empty.raise_for_status()
            if empty.json()['boolean']:
                raise BackupError('TARGET_NOT_EMPTY')
            rdf_file = package_file(manifest_path.parent, manifest['files']['rdf']['path'])
            with rdf_file.open('rb') as source_rdf:
                response = httpx.post(settings.fuseki_url + '/data', content=iter(lambda: source_rdf.read(1024 * 1024), b''),
                    headers={'Content-Type': 'application/n-quads'}, timeout=120, trust_env=False)
                response.raise_for_status()
            for ref in manifest['blob_files']:
                file = package_file(manifest_path.parent, ref['path'])
                with file.open('rb') as source_blob:
                    copied = blob_store.put(source_blob, ref['media_type'])
                if (copied.sha256, copied.size_bytes) != (ref['sha256'], ref['size_bytes']):
                    raise BackupError('RESTORED_BLOB_DIGEST_MISMATCH')
            restored_tables, targets = {}, {}
            for domain, engine in engines.items():
                with engine.begin() as c:
                    rows = destination / (domain + '.verified.jsonl')
                    actual = inventory(c, rows)
                    expected = manifest['stores'][domain]['tables']
                    if actual != expected:
                        raise BackupError('RESTORED_SQL_INVENTORY_MISMATCH:' + domain)
                    restored_tables[domain] = read_inventory(rows, expected)
                    targets[domain] = connection_identity(c)
                    functions = [dict(r) for r in c.execute(text('''SELECT p.proname,
                        pg_get_function_identity_arguments(p.oid) AS arguments,p.prosecdef,
                        pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl
                        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                        WHERE n.nspname=current_schema() ORDER BY p.proname,arguments''')).mappings()]
                    if functions != manifest['stores'][domain]['functions']:
                        raise BackupError('RESTORED_FUNCTION_SECURITY_MISMATCH:' + domain)
            restored_rdf = destination / 'rdf.verified.nq'
            with httpx.stream('GET', settings.fuseki_url + '/data', headers={'Accept': 'application/n-quads'}, timeout=120, trust_env=False) as response:
                response.raise_for_status()
                with restored_rdf.open('xb') as output:
                    for chunk in response.iter_bytes():
                        output.write(chunk)
            if _rdf_digest(rdf_file) != _rdf_digest(restored_rdf):
                raise BackupError('RESTORED_RDF_DATASET_MISMATCH')
            findings = check_references(restored_tables['core'], {d: restored_tables[d] for d in DOMAINS[1:]},
                manifest['blob_files'], restored_rdf)
            if findings != manifest['references']:
                raise BackupError('RESTORED_REFERENCES_MISMATCH')
            targets.update(blob=blob_identity(settings), rdf=settings.fuseki_url)
            with engines['core'].begin() as c:
                c.execute(text('''INSERT INTO storage_restore_receipts VALUES(:id,:backup,:manifest,:config,
                    CAST(:targets AS jsonb),CAST(:findings AS jsonb))'''), {'id': restore_id, 'backup': UUID(manifest['id']),
                    'manifest': report['manifest_digest'], 'config': _configuration(settings),
                    'targets': json.dumps(targets), 'findings': json.dumps(findings)})
            configuration = destination / 'configuration.json'
            configuration.write_text(json.dumps({'contract': 'multistore-restore-v1', 'restore_id': str(restore_id),
                'configuration_digest': _configuration(settings), 'settings': settings.model_dump(mode='json')}, indent=2), encoding='utf-8')
            state.update(state='verified', references=findings, targets=targets)
            state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
            return {'status': 'verified', 'configuration': configuration, 'restore_id': str(restore_id), 'references': findings}
        except Exception as error:
            state.update(state='failed', error=type(error).__name__)
            state_path.write_text(json.dumps(state, indent=2), encoding='utf-8')
            raise
        finally:
            for engine in engines.values():
                engine.dispose()

    def _restore_database(self, root, manifest, domain, owner_url, destination):
        archive = package_file(root, manifest['files'][domain]['path'])
        listing = pg_command(self.pg_bin, 'pg_restore', ['--list', archive]).decode('utf-8')
        # public is supplied by template0; PostGIS is provisioned/version checked
        # by the administrator. All other objects and original ACLs are restored.
        lines = [line for line in listing.splitlines() if not re.search(
            r' (SCHEMA - public |EXTENSION - postgis |COMMENT - EXTENSION postgis )', line)]
        use_list = destination / (domain + '.restore-list')
        use_list.write_text('\n'.join(lines) + '\n', encoding='utf-8')
        pg_command(self.pg_bin, 'pg_restore', ['--dbname=' + make_url(owner_url).database, '--exit-on-error', '--single-transaction', '--no-owner',
            '--use-list=' + str(use_list), archive], url=make_url(owner_url), diagnostic_path=destination / (domain + '.pg_restore.log'))


def load_restored_settings(path):
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    settings = Settings.model_validate(value['settings'])
    if value.get('contract') != 'multistore-restore-v1' or value['configuration_digest'] != _configuration(settings):
        raise BackupError('RESTORE_CONFIG_MISMATCH')
    core = create_engine_for(settings.database_url)
    try:
        with core.connect() as c:
            row = c.execute(text('SELECT * FROM storage_restore_receipts WHERE id=:id'), {'id': UUID(value['restore_id'])}).mappings().one_or_none()
            if row is None or row['configuration_digest'] != _configuration(settings) or row['targets']['core'] != connection_identity(c):
                raise BackupError('RESTORE_CONFIG_NOT_VERIFIED')
        for domain in DOMAINS[1:]:
            engine = create_engine_for(getattr(settings, domain + '_database_url'))
            try:
                if identity(engine) != row['targets'][domain]:
                    raise BackupError('RESTORE_TARGET_CHANGED')
            finally:
                engine.dispose()
        if blob_identity(settings) != row['targets']['blob'] or settings.fuseki_url != row['targets']['rdf']:
            raise BackupError('RESTORE_TARGET_CHANGED')
        verify_blob_namespace(settings, 'restore', row['id'])
        return settings
    finally:
        core.dispose()
