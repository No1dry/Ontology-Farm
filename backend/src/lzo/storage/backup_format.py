"""Private snapshot serialization and independent reference-completeness checks."""
import hashlib
import json
from pathlib import Path
import subprocess
import warnings

from sqlalchemy import text


class BackupError(RuntimeError):
    pass


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def package_file(root, relative):
    root = Path(root).resolve()
    value = (root / relative).resolve()
    if not value.is_relative_to(root) or not value.is_file():
        raise BackupError('BACKUP_FILE_MISSING')
    return value


def inventory(connection, path):
    """Stream every table independently; parent/partition counts overlap by design."""
    connection.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    tables = connection.execute(text('''SELECT c.relname AS name,c.relkind,c.relispartition,
        pg_get_partkeydef(c.oid) AS partition_key FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=current_schema() AND c.relkind IN ('r','p') ORDER BY c.relname''')).mappings().all()
    role = connection.execute(text('SELECT current_user')).scalar_one()
    quote = connection.dialect.identifier_preparer.quote
    metadata = {}
    with Path(path).open('w', encoding='utf-8', newline='\n') as output:
        for table in tables:
            digest, count = hashlib.sha256(), 0
            query = 'SELECT row_to_json(t)::text FROM ' + quote(table['name']) + ' t ORDER BY row_to_json(t)::text'
            for raw in connection.exec_driver_sql(query).scalars():
                digest.update((raw + '\n').encode('utf-8'))
                output.write(json.dumps({'table': table['name'], 'raw': raw}, ensure_ascii=False) + '\n')
                count += 1
            privileges = [dict(r) for r in connection.execute(text('''SELECT grantee.rolname AS role,a.privilege_type
                FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace,
                LATERAL aclexplode(coalesce(t.relacl,acldefault('r',t.relowner))) a
                JOIN pg_roles grantee ON grantee.oid=a.grantee
                WHERE n.nspname=current_schema() AND t.relname=:table AND grantee.rolname<>:owner
                ORDER BY grantee.rolname,a.privilege_type'''), {'table': table['name'], 'owner': role}).mappings()]
            metadata[table['name']] = {**dict(table), 'rows': count, 'digest': digest.hexdigest(), 'privileges': privileges}
    return metadata


def read_inventory(path, expected):
    tables = {name: [] for name in expected}
    digests = {name: hashlib.sha256() for name in expected}
    with Path(path).open(encoding='utf-8') as source:
        for line in source:
            item = json.loads(line)
            if set(item) != {'table', 'raw'} or item['table'] not in tables:
                raise BackupError('INVENTORY_INVALID')
            digests[item['table']].update((item['raw'] + '\n').encode('utf-8'))
            tables[item['table']].append(json.loads(item['raw']))
    if any(len(tables[name]) != row['rows'] or digests[name].hexdigest() != row['digest'] for name, row in expected.items()):
        raise BackupError('INVENTORY_DIGEST_MISMATCH')
    return tables


def pg_command(pg_bin, tool, arguments, *, url=None, timeout=600, diagnostic_path=None):
    import os
    env = dict(os.environ)
    if url is not None:
        env.update(PGHOST=url.host or '', PGPORT=str(url.port or 5432), PGUSER=url.username or '',
            PGPASSWORD=url.password or '', PGDATABASE=url.database or '')
        env['PGOPTIONS'] = url.query.get('options', '')
    executable = Path(pg_bin) / (tool + ('.exe' if os.name == 'nt' else ''))
    result = subprocess.run([str(executable), *map(str, arguments)], env=env, capture_output=True,
        timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    if result.returncode:
        if diagnostic_path is not None:
            Path(diagnostic_path).write_bytes(result.stderr)
        raise BackupError(tool.upper() + '_FAILED')
    return result.stdout


def check_references(core, domains, blobs, rdf_path):
    """Check accepted references; unrelated retained physical fixtures are preserved."""
    from lzo.synchronization.contracts import SyncEnvelope
    from lzo.governance.risk import digest_change
    by_blob = {(r['kind'], r['key']): r for r in blobs}
    artifact_ids = {r['id'] for r in core['artifact_versions']}
    for row in core['artifact_versions']:
        entry = by_blob.get(('version', row['id']))
        if entry is None or any(entry[key] != row[key] for key in ('sha256', 'size_bytes', 'media_type')):
            raise BackupError('BLOB_REFERENCE_MISSING')
    parts = 0
    for session in core['artifact_upload_sessions']:
        for number, ref in session['parts'].items():
            entry = by_blob.get(('part', session['id'] + ':' + number))
            if entry is None or any(entry[key] != ref[key] for key in ('sha256', 'size_bytes', 'media_type', 'version_id')):
                raise BackupError('UPLOAD_PART_REFERENCE_MISSING')
            parts += 1
    revisions = {r['event_id']: r for r in core['revisions']}
    for row in core['revisions']:
        if digest_change(row['payload']) != row['content_digest']:
            raise BackupError('CORE_REVISION_DIGEST_MISMATCH')
    for row in core['outbox_events']:
        if row['event_id'] not in revisions:
            raise BackupError('CORE_EVENT_REFERENCE_MISSING')
    counts = {'blob_versions': len(artifact_ids), 'upload_parts': parts,
        'gis_versions': 0, 'catalog_versions': 0, 'telemetry_records': 0}
    for row in core['store_deliveries']:
        envelope = SyncEnvelope.model_validate(row['envelope'])
        if envelope.envelope_hash != row['envelope_hash'] or envelope.payload_hash != row['payload_hash']:
            raise BackupError('ENVELOPE_DIGEST_MISMATCH')
        # Accepted inputs must survive even while a domain delivery is pending.
        if envelope.operation == 'catalog.index':
            metadata = envelope.payload['metadata']
            refs = set(metadata.get('attachment_version_ids', ())) | set(metadata.get('evidence_version_ids', ()))
            if metadata.get('artifact_version_id'):
                refs.add(metadata['artifact_version_id'])
            for capability in metadata.get('capabilities', ()):
                refs.update(capability.get('evidence_version_ids', ()))
            if not refs <= artifact_ids:
                raise BackupError('CATALOG_ARTIFACT_REFERENCE_MISSING')
            if envelope.payload['kind'] == 'document':
                artifact = next(r for r in core['artifact_versions'] if r['id'] == metadata['artifact_version_id'])
                if artifact['sha256'] != envelope.payload['sha256']:
                    raise BackupError('CATALOG_ARTIFACT_DIGEST_MISMATCH')
        if envelope.operation == 'telemetry.batch':
            payload = envelope.payload
            if payload.get('input_artifact_version_id') and payload['input_artifact_version_id'] not in artifact_ids:
                raise BackupError('TELEMETRY_BLOB_REFERENCE_MISSING')
            for record in payload['records']:
                refs = set(record.get('evidence_version_ids', ()))
                if record.get('artifact_version_id'):
                    refs.add(record['artifact_version_id'])
                if not refs <= artifact_ids:
                    raise BackupError('TELEMETRY_BLOB_REFERENCE_MISSING')
                for key in ('location_ref', 'device_binding_ref', 'model_ref'):
                    pin = record.get(key)
                    if pin:
                        revision = revisions.get(pin['event_id'])
                        if revision is None or (revision['resource_id'], revision['revision']) != (pin['resource_id'], pin['revision']):
                            raise BackupError('TELEMETRY_REVISION_REFERENCE_MISSING')
        if row['status'] not in ('applied', 'superseded'):
            continue  # Pending and committed-but-unacknowledged states stay pending.
        domain = domains[row['target']]
        inbox = next((r for r in domain['store_inbox'] if r['event_id'] == row['event_id']), None)
        if inbox is None or inbox['envelope_hash'] != envelope.envelope_hash or inbox['receipt']['payload_hash'] != envelope.payload_hash:
            raise BackupError('DOMAIN_RECEIPT_REFERENCE_MISSING')
        if envelope.operation == 'geometry.index':
            version = next((r for r in domain['gis_geometry_versions'] if r['resource_id'] == str(envelope.entity_id)
                and r['revision'] == envelope.aggregate_revision), None)
            if version is None or version['payload_hash'] != envelope.payload_hash or version['payload'] != envelope.payload['geometry']:
                raise BackupError('GIS_VERSION_REFERENCE_MISSING')
            counts['gis_versions'] += 1
            for name in ('asset', 'observation'):
                ref = envelope.payload.get(name)
                if ref and ref.get('artifact_version_id') not in artifact_ids:
                    raise BackupError('GIS_ARTIFACT_REFERENCE_MISSING')
        elif envelope.operation == 'catalog.index':
            version = next((r for r in domain['catalog_versions'] if r['resource_id'] == str(envelope.entity_id)
                and r['revision'] == envelope.aggregate_revision), None)
            if version is None or version['payload_hash'] != envelope.payload_hash or version['payload'] != envelope.payload:
                raise BackupError('CATALOG_VERSION_REFERENCE_MISSING')
            counts['catalog_versions'] += 1
        elif envelope.operation == 'telemetry.batch':
            samples = {r['id']: r for r in domain['telemetry_samples']}
            identities = {r['id']: r for r in domain['telemetry_identities']}
            for ref in envelope.payload['records']:
                if (ref['id'] not in samples or samples[ref['id']]['payload'] != ref
                        or ref['id'] not in identities or identities[ref['id']]['content_hash'] != ref['content_hash']):
                    raise BackupError('TELEMETRY_VERSION_REFERENCE_MISSING')
                for key in ('artifact_version_id', 'media_artifact_version_id'):
                    if ref.get(key) and ref[key] not in artifact_ids:
                        raise BackupError('TELEMETRY_BLOB_REFERENCE_MISSING')
                counts['telemetry_records'] += 1
        else:
            raise BackupError('UNKNOWN_ACCEPTED_DOMAIN_OPERATION')
    counts['rdf_heads'] = verify_rdf(rdf_path, core['projection_heads'], core['projection_events'])
    return counts


def verify_rdf(path, heads, events):
    from rdflib import URIRef, Literal, XSD
    from lzo.semantic.fuseki import FusekiRepository, V, GraphPair, ROLES, graph_iri, generation_digest, _one
    from lzo.semantic.rdf import INTERNAL
    dataset = parse_dataset(path)
    class Snapshot(FusekiRepository):
        def __init__(self):
            super().__init__('snapshot://private')
        def _get(self, iri):
            return dataset.graph(URIRef(iri))
        def select(self, query, timeout_ms):
            with warnings.catch_warnings():
                warnings.filterwarnings('ignore', message='Dataset.default_context is deprecated.*', category=DeprecationWarning, module=r'rdflib\..*')
                return json.loads(dataset.query(query).serialize(format='json'))
        def _verify_physical(self, iri, expected):
            actual = self._get(iri)
            def equivalent(left, right):
                if left == right:
                    return True
                return (isinstance(left, Literal) and isinstance(right, Literal)
                    and (left.datatype or XSD.string) == (right.datatype or XSD.string)
                    and left.language == right.language and left.eq(right) is True)
            if (any(not any(equivalent(o, value) for value in actual.objects(s, p)) for s, p, o in expected)
                    or any(not any(equivalent(o, value) for value in expected.objects(s, p)) for s, p, o in actual)):
                raise BackupError('RDF_PHYSICAL_REFERENCE_MISMATCH')
    snapshot = Snapshot()
    for row in heads:
        current = snapshot.current_revision(row['resource_id'])
        if current is None or any(current[key] != row[key] for key in ('revision', 'position', 'digest')):
            raise BackupError('RDF_HEAD_REFERENCE_MISSING')
        snapshot._verify_generation(current)
    for row in events:
        if row['state'] != 'synced':
            continue
        generation = URIRef(INTERNAL + f"generation:{row['resource_id']}:{row['revision']}:{row['digest']}")
        manifest = dataset.graph(generation)
        graphs = {}
        for role in ROLES:
            metadata = _one(manifest, generation, V[role])
            meta = dataset.graph(metadata)
            pair = GraphPair(graph_iri(row['resource_id'], role), str(_one(meta, metadata, V.physical)),
                str(metadata), str(_one(meta, metadata, V.digest)), int(_one(meta, metadata, V['count'])))
            graphs[role] = snapshot.read_pair(pair)
        if generation_digest(graphs) != row['digest']:
            raise BackupError('RDF_HISTORY_REFERENCE_MISSING')
    return len(heads)


def parse_dataset(path):
    from rdflib import Dataset, Graph, BNode
    dataset = Dataset()
    # The RDFLib default URI is also a legal named graph identifier. Give the
    # default graph a private identity so those two dataset states cannot merge.
    dataset.default_graph = Graph(store=dataset.store, identifier=BNode())
    # RDFLib's pinned NQuads parser internally uses its own deprecated alias.
    # Limit suppression to that upstream compatibility warning during parsing.
    with warnings.catch_warnings():
        warnings.filterwarnings('ignore', message='Dataset.default_context is deprecated.*', category=DeprecationWarning, module=r'rdflib\..*')
        dataset.parse(str(path), format='nquads')
    return dataset
