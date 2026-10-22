"""Coordinated, signed private snapshots with independent material verification."""
import hashlib
import hmac
import json
from pathlib import Path
import secrets
from uuid import UUID, uuid4

import httpx
from sqlalchemy import insert, text

from lzo.auth.models import resource_scopes
from lzo.auth.permissions import require_permission, validate_actor
from lzo.contracts import BlobRef, ResourceScope
from lzo.db import PgUnitOfWork
from lzo.governance.revisions import append_revision
from lzo.governance.risk import digest_change
from lzo.storage.backup_format import (BackupError, check_references, file_hash,
    inventory, package_file, pg_command, read_inventory)
from lzo.storage.blobs import get_blob_store
from lzo.storage.migration import _configuration, _snapshot, blob_identity, connection_identity, schema_state, url_identity

DOMAINS = ('core', 'gis', 'timeseries', 'catalog')


def _key(path, *, create=False):
    path = Path(path).resolve()
    if create and not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as output:
            output.write(secrets.token_bytes(32))
    value = path.read_bytes()
    if len(value) != 32:
        raise BackupError('BACKUP_AUTHORITY_KEY_INVALID')
    return value


def _signature(value, key):
    return hmac.new(key, digest_change({k: v for k, v in value.items() if k != 'signature'}).encode(), hashlib.sha256).hexdigest()


def verify_backup_material(path, authority_key, pg_bin):
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('contract') != 'multistore-backup-v1' or value.get('status') != 'complete'
            or not hmac.compare_digest(_signature(value, _key(authority_key)), value.get('signature', ''))):
        raise BackupError('BACKUP_NOT_VERIFIED')
    root = path.parent
    for entry in value['files'].values():
        file = package_file(root, entry['path'])
        if file_hash(file) != entry['sha256'] or file.stat().st_size != entry['size_bytes']:
            raise BackupError('CHECKSUM_MISMATCH')
    tables = {}
    for domain in DOMAINS:
        store = value['stores'][domain]
        dump = package_file(root, value['files'][domain]['path'])
        pg_command(pg_bin, 'pg_restore', ['--list', dump])
        tables[domain] = read_inventory(package_file(root, value['files'][domain + '_inventory']['path']), store['tables'])
        captured = (sorted(row['version_num'] for row in tables[domain]['alembic_version']) if domain == 'core'
            else tables[domain]['store_schema_version'][0]['version'])
        # Older packages remain restorable from their verified version rows.
        # New migration activation additionally requires the explicit binding.
        if 'schema_state' in store and store['schema_state'] != captured:
            raise BackupError('BACKUP_SCHEMA_STATE_MISMATCH')
    for ref in value['blob_files']:
        file = package_file(root, ref['path'])
        if file.stat().st_size != ref['size_bytes'] or file_hash(file) != ref['sha256']:
            raise BackupError('CHECKSUM_MISMATCH')
    findings = check_references(tables['core'], {d: tables[d] for d in DOMAINS[1:]}, value['blob_files'],
        package_file(root, value['files']['rdf']['path']))
    if value.get('references') is not None and findings != value['references']:
        raise BackupError('REFERENCE_FINDINGS_MISMATCH')
    return {'status': 'verified', 'backup_id': value['id'], 'references': findings,
        'manifest_digest': digest_change(value)}


def verify_migration_backup(path, *, core, source_identity, data_digest, data_position):
    """Use an authoritative receipt, never a path/status asserted by the caller."""
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    try:
        identifier = UUID(value['id'])
    except (ValueError, KeyError, TypeError):
        raise BackupError('BACKUP_NOT_VERIFIED') from None
    with PgUnitOfWork(core) as uow:
        row = uow.session.execute(text('SELECT * FROM storage_backups WHERE id=:id'), {'id': identifier}).mappings().one_or_none()
        if row is None or (row['source_identity'], row['data_digest'], row['data_position']) != (source_identity, data_digest, data_position):
            raise BackupError('BACKUP_NOT_VERIFIED')
        if row['manifest_digest'] != digest_change(value):
            raise BackupError('BACKUP_MANIFEST_CHANGED')
        return verify_backup_material(path, row['authority_key_path'], row['pg_bin'])


class BackupService:
    def __init__(self, settings, pg_bin, authority_key):
        self.settings, self.pg_bin, self.authority_key = settings, Path(pg_bin).resolve(), Path(authority_key).resolve()

    def verify(self, path):
        return verify_backup_material(path, self.authority_key, self.pg_bin)

    def create(self, destination, window, actor, *, migration_plan=None):
        window.verify()
        if set(window.connections) != set(DOMAINS):
            raise BackupError('ALL_STORE_QUIESCENCE_REQUIRED')
        for domain, c in window.connections.items():
            configured = getattr(self.settings, ('' if domain == 'core' else domain + '_') + 'database_url')
            if url_identity(configured) != connection_identity(c):
                raise BackupError('BACKUP_CONFIGURATION_TARGET_MISMATCH')
        destination = Path(destination).resolve()
        if destination.exists():
            raise BackupError('BACKUP_DESTINATION_EXISTS')
        if self.authority_key.is_relative_to(destination):
            raise BackupError('AUTHORITY_KEY_MUST_BE_OUTSIDE_BACKUP')
        destination.mkdir(parents=True)
        identifier = uuid4()
        state_path = destination / 'state.json'
        state_path.write_text(json.dumps({'id': str(identifier), 'state': 'writing'}))
        try:
            exported_snapshots = {domain: c.execute(text('SELECT pg_export_snapshot()')).scalar_one()
                for domain, c in window.connections.items()}
            with PgUnitOfWork(window.core) as uow:
                validate_actor(uow, actor)
                require_permission(actor, 'storage.manage', ResourceScope(migration_plan, None, (), 'storage'), uow=uow)
                values, snapshot = _snapshot(uow)
                source_identity = {'core': connection_identity(window.core), 'blob': blob_identity(self.settings)}
                if migration_plan:
                    row = uow.session.execute(text('SELECT * FROM storage_migration_plans WHERE id=:id'), {'id': migration_plan}).mappings().one()
                    source_identity = row['source_identity']
                    if (_configuration(self.settings) != row['configuration_digest']
                            or source_identity['core'] != connection_identity(window.core)
                            or any(row['target_identity'][d] != connection_identity(window.connections[d]) for d in DOMAINS)
                            or any(row['target_identity']['schema_state'][d] != schema_state(window.connections[d], d) for d in DOMAINS)
                            or row['target_identity']['blob'] != blob_identity(self.settings)):
                        raise BackupError('BACKUP_CONFIGURATION_TARGET_MISMATCH')
                    if not row['reconciliation'] or any(snapshot[k] != row['reconciliation'][k] for k in ('data_digest', 'data_position')):
                        raise BackupError('MIGRATION_RECONCILIATION_STALE')
                value = {'contract': 'multistore-backup-v1', 'status': 'complete', 'id': str(identifier),
                    'source_identity': source_identity, 'migration_plan_id': str(migration_plan) if migration_plan else None,
                    'data_digest': snapshot['data_digest'], 'data_position': snapshot['data_position'],
                    'watermark': snapshot, 'maintenance': window.evidence, 'stores': {}, 'files': {}, 'blob_files': []}
                def entry(name, path):
                    value['files'][name] = {'path': path.relative_to(destination).as_posix(),
                        'sha256': file_hash(path), 'size_bytes': path.stat().st_size}
                for domain, connection in window.connections.items():
                    current = connection_identity(connection)
                    dump, rows = destination / (domain + '.dump'), destination / (domain + '.jsonl')
                    tables = inventory(connection, rows)
                    exported = exported_snapshots[domain]
                    pg_command(self.pg_bin, 'pg_dump', ['--format=custom', '--no-owner',
                        '--schema=' + current['schema'], '--snapshot=' + exported, '--file=' + str(dump)], url=connection.engine.url)
                    value['stores'][domain] = {'identity': current, 'tables': tables, 'schema_state': schema_state(connection, domain),
                        'server_version': connection.execute(text('SHOW server_version')).scalar_one(),
                        'extensions': [dict(r) for r in connection.execute(text('SELECT extname,extversion FROM pg_extension ORDER BY extname')).mappings()],
                        'functions': [dict(r) for r in connection.execute(text('''SELECT p.proname,
                            pg_get_function_identity_arguments(p.oid) AS arguments,p.prosecdef,
                            pg_get_userbyid(p.proowner) AS owner,p.proacl::text AS acl
                            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
                            WHERE n.nspname=current_schema() ORDER BY p.proname,arguments''')).mappings()]}
                    entry(domain, dump)
                    entry(domain + '_inventory', rows)
                settings_path = destination / 'runtime-settings.json'
                settings_path.write_text(json.dumps(self.settings.model_dump(mode='json'), ensure_ascii=False), encoding='utf-8')
                entry('settings', settings_path)
                store = get_blob_store(self.settings)
                (destination / 'blobs').mkdir()
                core = read_inventory(destination / 'core.jsonl', value['stores']['core']['tables'])
                refs = [('version', r['id'], BlobRef(r['sha256'], r['size_bytes'], r['media_type'], UUID(r['id']))) for r in core['artifact_versions']]
                for session in core['artifact_upload_sessions']:
                    refs.extend(('part', session['id'] + ':' + number, BlobRef(**{**ref, 'version_id': UUID(ref['version_id'])}))
                        for number, ref in session['parts'].items())
                for kind, key, ref in refs:
                    blob_path = destination / 'blobs' / ref.sha256
                    if not blob_path.exists():
                        with store.open(ref) as source, blob_path.open('xb') as target:
                            for chunk in iter(lambda: source.read(1024 * 1024), b''):
                                target.write(chunk)
                    value['blob_files'].append({'kind': kind, 'key': key, 'version_id': str(ref.version_id),
                        'sha256': ref.sha256, 'size_bytes': ref.size_bytes, 'media_type': ref.media_type,
                        'path': blob_path.relative_to(destination).as_posix()})
                    entry('blob:' + kind + ':' + key, blob_path)
                rdf = destination / 'rdf.nq'
                self._export_rdf(rdf)
                entry('rdf', rdf)
                second = destination / 'rdf-confirm.nq'
                self._export_rdf(second)
                if file_hash(second) != file_hash(rdf):
                    raise BackupError('RDF_CHANGED_DURING_BACKUP')
                second.unlink()
                key = _key(self.authority_key, create=True)
                value['signature'] = _signature(value, key)
                manifest = destination / 'manifest.json'
                manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                findings = self.verify(manifest)['references']
                value['references'] = findings
                value['signature'] = _signature(value, key)
                manifest.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
                window.verify()
                uow.session.execute(insert(resource_scopes).values(resource_id=identifier, resource_kind='storage.backup', security_class='storage'))
                append_revision(uow, actor, identifier, 0, {'manifest_digest': digest_change(value), 'watermark': snapshot,
                    'references': findings}, 'storage.backup.completed')
                uow.session.execute(text('''INSERT INTO storage_backups VALUES(:id,:digest,:key,:bin,
                    CAST(:source AS jsonb),:data,:position,CAST(:watermark AS jsonb),CAST(:findings AS jsonb))'''),
                    {'id': identifier, 'digest': digest_change(value), 'key': str(self.authority_key), 'bin': str(self.pg_bin),
                        'source': json.dumps(source_identity), 'data': snapshot['data_digest'], 'position': snapshot['data_position'],
                        'watermark': json.dumps(snapshot), 'findings': json.dumps(findings)})
                uow.commit()
            state_path.write_text(json.dumps({'id': str(identifier), 'state': 'verified'}))
            return manifest
        except Exception as error:
            state_path.write_text(json.dumps({'id': str(identifier), 'state': 'failed', 'error': type(error).__name__}))
            raise

    def _export_rdf(self, path):
        with httpx.stream('GET', self.settings.fuseki_url + '/data', headers={'Accept': 'application/n-quads'},
                timeout=120, trust_env=False) as response:
            response.raise_for_status()
            if response.headers.get('content-type', '').split(';')[0] != 'application/n-quads':
                raise BackupError('RDF_DATASET_EXPORT_REQUIRED')
            with path.open('xb') as output:
                for chunk in response.iter_bytes():
                    output.write(chunk)
