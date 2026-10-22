"""Checked legacy GIS/blob replay; the semantic core never changes identity.

Activation is an offline operator operation, not a live router. Every accepted
version is checked; checkpoints are progress hints, never integrity evidence.
"""
import hashlib
import json
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import insert, select, text
from sqlalchemy.engine import Connection

from lzo.auth.models import authorization_state, resource_scopes
from lzo.auth.permissions import require_permission, validate_actor
from lzo.contracts import BlobRef, ResourceScope
from lzo.db import PgUnitOfWork
from lzo.governance.approvals import ApprovalService
from lzo.governance.models import resource_heads
from lzo.governance.revisions import append_revision
from lzo.governance.risk import ChangeDescriptor, digest_change, register_policy
from lzo.storage.blobs import get_blob_store
from lzo.synchronization.contracts import SyncEnvelope
from lzo.synchronization.delivery import _apply, _acknowledge, enqueue_delivery


class MigrationError(RuntimeError):
    """Sanitized typed operational refusal; never includes a DSN."""


def identity(engine):
    with nullcontext(engine) if isinstance(engine, Connection) else engine.connect() as c:
        return connection_identity(c)


def connection_identity(c):
    return dict(c.execute(text('''SELECT current_database() AS database,
        current_schema() AS schema, inet_server_addr()::text AS address,
        inet_server_port() AS port, d.oid::bigint AS database_oid,
        n.oid::bigint AS schema_oid FROM pg_database d,pg_namespace n
        WHERE d.datname=current_database() AND n.nspname=current_schema()''')).mappings().one())


def blob_identity(settings):
    return ({'provider': 'local', 'root': str(settings.file_root.resolve())}
        if settings.blob_provider == 'local' else
        {'provider': 's3', 'endpoint': settings.s3_endpoint, 'bucket': settings.s3_bucket, 'prefix': settings.s3_key_prefix})


def claim_blob_namespace(settings, kind, identifier):
    """Exclusively claim a NEW destination; a partial claim is never recycled."""
    value = {'contract': 'multistore-blob-namespace-v1', 'kind': kind, 'id': str(identifier)}
    if settings.blob_provider == 'local':
        root = settings.file_root.resolve()
        try:
            root.mkdir(parents=True, exist_ok=False)
            with (root / '._lzo_namespace.json').open('x', encoding='utf-8') as output:
                json.dump(value, output)
        except FileExistsError:
            raise MigrationError('TARGET_BLOB_NAMESPACE_NOT_NEW') from None
        return
    from botocore.exceptions import ClientError
    store = get_blob_store(settings)
    if not settings.s3_key_prefix or store.client.list_objects_v2(
            Bucket=store.bucket, Prefix=settings.s3_key_prefix + '/', MaxKeys=1).get('Contents'):
        raise MigrationError('TARGET_BLOB_NAMESPACE_NOT_NEW')
    try:
        store.client.put_object(Bucket=store.bucket, Key=settings.s3_key_prefix + '/_lzo_namespace.json',
            Body=json.dumps(value, sort_keys=True).encode(), ContentType='application/json', IfNoneMatch='*')
    except ClientError as error:
        if error.response['ResponseMetadata']['HTTPStatusCode'] in (409, 412):
            raise MigrationError('TARGET_BLOB_NAMESPACE_NOT_NEW') from None
        raise


def verify_blob_namespace(settings, kind, identifier):
    from botocore.exceptions import BotoCoreError, ClientError
    try:
        if settings.blob_provider == 'local':
            value = json.loads((settings.file_root / '._lzo_namespace.json').read_text(encoding='utf-8'))
        else:
            store = get_blob_store(settings)
            with store.client.get_object(Bucket=store.bucket,
                    Key=settings.s3_key_prefix + '/_lzo_namespace.json')['Body'] as stream:
                value = json.loads(stream.read(4097))
        if value != {'contract': 'multistore-blob-namespace-v1', 'kind': kind, 'id': str(identifier)}:
            raise MigrationError('TARGET_BLOB_NAMESPACE_CHANGED')
    except (BotoCoreError, ClientError, OSError, ValueError):
        raise MigrationError('TARGET_BLOB_NAMESPACE_CHANGED') from None


def _configuration(settings):
    # Bind every runtime option, including limits/auth configuration. Raw values
    # live only in the ignored private activation artifact, never ledger/audit.
    return digest_change(settings.model_dump(mode='json'))


def _plan(uow, identifier, actor):
    validate_actor(uow, actor)
    require_permission(actor, 'storage.manage', ResourceScope(identifier, None, (), 'storage'), uow=uow)
    row = uow.session.execute(text('SELECT * FROM storage_migration_plans WHERE id=:id'), {'id': identifier}).mappings().one_or_none()
    if row is None:
        raise MigrationError('PLAN_NOT_FOUND')
    return row


def _snapshot(uow):
    result = []
    rows = uow.session.execute(text('''SELECT r.*,d.envelope FROM revisions r
        JOIN semantic_objects o ON o.id=r.resource_id
        LEFT JOIN store_deliveries d ON d.event_id=r.event_id AND d.target='gis'
        WHERE r.payload ? 'geometry' AND r.payload ? 'object'
        ORDER BY r.resource_id,r.revision''')).mappings()
    for row in rows:
        payload = row['payload']
        effect = payload['effect']
        expected = {'geometry': payload['geometry'], 'active': payload['object']['state'] != 'inactive',
            'asset': effect.get('asset'), 'observation': effect.get('observation')}
        envelope = (SyncEnvelope.model_validate(row['envelope']) if row['envelope'] else
            SyncEnvelope(event_id=row['event_id'], entity_id=row['resource_id'], aggregate_revision=row['revision'],
                target='gis', operation='geometry.index', payload=expected,
                data_nature=payload['object']['data_nature'], recorded_at=row['recorded_at'],
                source_refs=tuple(sorted(str(p['resource_id']) for p in payload['dependency_revisions']))))
        if (envelope.payload != expected or envelope.event_id != row['event_id']
                or envelope.entity_id != row['resource_id'] or envelope.aggregate_revision != row['revision']
                or envelope.operation != 'geometry.index' or envelope.target != 'gis'):
            raise MigrationError('SOURCE_ENVELOPE_MISMATCH')
        result.append({'kind': 'geometry', 'key': str(row['event_id']), 'digest': envelope.envelope_hash, 'value': envelope})
    for row in uow.session.execute(text('SELECT id,sha256,size_bytes,media_type FROM artifact_versions ORDER BY id')).mappings():
        blob = BlobRef(row['sha256'], row['size_bytes'], row['media_type'], row['id'])
        result.append({'kind': 'blob', 'key': str(row['id']), 'digest': digest_change({**asdict(blob), 'version_id': str(blob.version_id)}), 'value': blob})
    sessions = list(uow.session.execute(text('SELECT * FROM artifact_upload_sessions ORDER BY id')).mappings())
    for session in sessions:
        for number, ref in sorted(session['parts'].items()):
            blob = BlobRef(**{**ref, 'version_id': UUID(ref['version_id'])})
            result.append({'kind': 'blob', 'key': f"part:{session['id']}:{number}",
                'digest': digest_change(ref), 'value': blob})
    # Upload parts/session changes may have no ledger revision. Include their
    # exact state as well as the relevant accepted ledger position.
    session_digest = digest_change(json.loads(json.dumps([dict(r) for r in sessions], default=str)))
    data_position = uow.session.execute(text("""SELECT coalesce(max(position),0) FROM outbox_events
        WHERE event_type NOT LIKE 'approval.%' AND event_type NOT LIKE 'storage.%'""")).scalar_one()
    watermark = uow.session.execute(text('SELECT event_position FROM platform_watermarks')).scalar_one()
    manifest = [{'kind': r['kind'], 'key': r['key'], 'digest': r['digest']} for r in result]
    delivery_state = uow.session.execute(text('''SELECT row_to_json(d)::text
        FROM store_deliveries d ORDER BY target,event_id''')).scalars().all()
    projection_state = uow.session.execute(text('''SELECT row_to_json(p)::text
        FROM projection_events p JOIN outbox_events e ON e.event_id=p.event_id
        WHERE e.event_type NOT LIKE 'approval.%' AND e.event_type NOT LIKE 'storage.%'
        ORDER BY p.event_id''')).scalars().all()
    projection_heads = uow.session.execute(text('''SELECT row_to_json(p)::text
        FROM projection_heads p ORDER BY resource_id''')).scalars().all()
    return result, {'data_digest': digest_change({'versions': manifest, 'sessions': session_digest,
        'deliveries': delivery_state, 'projections': projection_state, 'rdf_heads': projection_heads}),
        'data_position': data_position, 'event_position': watermark,
        'counts': {kind: sum(r['kind'] == kind for r in result) for kind in ('geometry', 'blob')}}


def _policy(uow, targets, command):
    if set(command) != {'plan_id', 'action', 'reconciliation_digest', 'backup_digest', 'configuration_digest'}:
        raise ValueError('Invalid activation command')
    identifier = UUID(command['plan_id'])
    row = uow.session.execute(text('SELECT * FROM storage_migration_plans WHERE id=:id'), {'id': identifier}).mappings().one()
    if tuple(targets) != (identifier,) or command['action'] not in ('cutover', 'rollback'):
        raise ValueError('Invalid activation target')
    _, snapshot = _snapshot(uow)
    if (row['reconciliation'] is None or snapshot['data_digest'] != row['reconciliation']['data_digest']
            or snapshot['data_position'] != row['reconciliation']['data_position']):
        raise MigrationError('RECONCILIATION_STALE')
    if command != _command(row, command['action']):
        raise ValueError('Activation command changed')
    return ChangeDescriptor(kind='storage.activate', target_ids=(identifier,), command=command,
        baseline={'epoch': row['epoch'], 'data_position': snapshot['data_position'],
            'authorization': uow.session.execute(select(authorization_state.c.version)).scalar_one()},
        impact={'action': command['action'], 'data_digest': snapshot['data_digest']},
        submit_action='storage.manage', review_action='storage.review', operation_key='storage.activate:' + str(identifier))


def _command(row, action):
    if not row['reconciliation']:
        raise MigrationError('RECONCILIATION_REQUIRED')
    if not row['backup']:
        raise MigrationError('BACKUP_REQUIRED')
    return {'plan_id': str(row['id']), 'action': action,
        'reconciliation_digest': digest_change(row['reconciliation']),
        'backup_digest': row['backup']['manifest_digest'], 'configuration_digest': row['configuration_digest']}


register_policy('storage.activate', _policy)
from lzo.semantic.projection import register_nonsemantic
for _event in ('storage.migration.created', 'storage.migration.activated', 'storage.migration.rolled_back'):
    register_nonsemantic(_event, 'storage-migration-v1')
register_nonsemantic('storage.backup.completed', 'storage-backup-v1')


class MigrationService:
    def __init__(self, core, gis, source, target):
        self.core, self.gis, self.source, self.target = core, gis, source, target

    def _identities(self):
        return ({'core': identity(self.core), 'blob': blob_identity(self.source), 'configuration_digest': _configuration(self.source)},
            {'gis': identity(self.gis), 'blob': blob_identity(self.target)})

    def _check_legacy_heads(self):
        # Generic evidence attachment can advance the ledger head without a
        # corresponding semantic snapshot. Do not certify its older geometry
        # as the current semantic head or attempt an implicit history repair.
        with self.core.connect() as c:
            inconsistent = c.execute(text('''SELECT EXISTS(SELECT 1
                FROM semantic_objects o JOIN resource_heads h ON h.resource_id=o.id
                WHERE o.revision <> h.revision)''')).scalar_one()
        if inconsistent:
            raise MigrationError('LEGACY_HEAD_REPAIR_REQUIRED')

    def _check(self, row):
        self._check_legacy_heads()
        source, target = self._identities()
        if source != row['source_identity'] or target != row['target_identity'] or _configuration(self.target) != row['configuration_digest']:
            raise MigrationError('TARGET_CHANGED')
        if url_identity(self.target.gis_database_url) != target['gis'] or url_identity(self.source.database_url) != source['core']:
            raise MigrationError('TARGET_CHANGED')
        verify_blob_namespace(self.target, 'migration', row['id'])
        with self.gis.connect() as c:
            value = c.execute(text('SELECT plan_id FROM storage_migration_namespace')).scalar_one()
        if value != row['id']:
            raise MigrationError('TARGET_CHANGED')

    def preview(self, actor):
        if self.source.storage_mode != 'legacy' or self.source.blob_provider != 'local' or self.target.storage_mode != 'split':
            raise MigrationError('EXPLICIT_LEGACY_TO_SPLIT_REQUIRED')
        self._check_legacy_heads()
        source, target = self._identities()
        if identity(self.core) != url_identity(self.target.database_url):
            raise MigrationError('CORE_IDENTITY_MUST_REMAIN')
        if self.target.blob_provider == 's3':
            store = get_blob_store(self.target)
            if not self.target.s3_key_prefix or store.client.list_objects_v2(
                    Bucket=store.bucket, Prefix=self.target.s3_key_prefix + '/', MaxKeys=1).get('Contents'):
                raise MigrationError('TARGET_BLOB_NAMESPACE_NOT_NEW')
        elif self.target.file_root.resolve() == self.source.file_root.resolve():
            raise MigrationError('TARGET_BLOB_NAMESPACE_NOT_NEW')
        identifier = uuid4()
        with PgUnitOfWork(self.core) as uow:
            validate_actor(uow, actor)
            require_permission(actor, 'storage.manage', ResourceScope(None, None, (), 'storage'), uow=uow)
            claim_blob_namespace(self.target, 'migration', identifier)
            with self.gis.begin() as c:
                if c.execute(text('''SELECT EXISTS(
                    SELECT 1 FROM store_inbox UNION ALL SELECT 1 FROM store_heads
                    UNION ALL SELECT 1 FROM gis_geometry_versions UNION ALL SELECT 1 FROM gis_geometry_heads
                    UNION ALL SELECT 1 FROM gis_spatial_assets UNION ALL SELECT 1 FROM gis_location_observations
                    )''')).scalar_one() or c.execute(text("SELECT to_regclass('storage_migration_namespace')")).scalar_one():
                    raise MigrationError('TARGET_NOT_NEW')
                c.execute(text('CREATE TABLE storage_migration_namespace(plan_id uuid PRIMARY KEY)'))
                c.execute(text('INSERT INTO storage_migration_namespace VALUES(:id)'), {'id': identifier})
                c.execute(text('GRANT SELECT ON storage_migration_namespace TO lzo_gis_app'))
            uow.session.execute(insert(resource_scopes).values(resource_id=identifier, resource_kind='storage.migration', security_class='storage'))
            append_revision(uow, actor, identifier, 0, {'source': source, 'target': target}, 'storage.migration.created')
            _, initial = _snapshot(uow)
            uow.session.execute(text('''INSERT INTO storage_migration_plans
                (id,source_identity,target_identity,configuration_digest,initial_watermark)
                VALUES(:id,CAST(:source AS jsonb),CAST(:target AS jsonb),:digest,:watermark)'''),
                {'id': identifier, 'source': json.dumps(source), 'target': json.dumps(target),
                    'digest': _configuration(self.target), 'watermark': initial['event_position']})
            uow.commit()
        return identifier

    def backfill(self, identifier, actor, limit=1000):
        if type(limit) is not int or not 1 <= limit <= 10000:
            raise MigrationError('INVALID_BATCH_LIMIT')
        from lzo.gis.repository import apply_geometry
        with PgUnitOfWork(self.core) as uow:
            row = _plan(uow, identifier, actor)
            self._check(row)
            if row['state'] not in ('backfill', 'reconciled'):
                raise MigrationError('PLAN_ALREADY_ACTIVATED')
            values, _ = _snapshot(uow)
            done = {(r['kind'], r['version_key']): r['digest'] for r in uow.session.execute(
                text('SELECT * FROM storage_migration_items WHERE plan_id=:id'), {'id': identifier}).mappings()}
        pending = [r for r in values if (r['kind'], r['key']) not in done]
        if any(done.get((r['kind'], r['key']), r['digest']) != r['digest'] for r in values):
            raise MigrationError('CHECKPOINT_CONFLICT')
        source, target = get_blob_store(self.source), get_blob_store(self.target)
        copied = 0
        for item in pending[:limit]:
            with PgUnitOfWork(self.core) as uow:
                current = _plan(uow, identifier, actor)
                if current['state'] not in ('backfill', 'reconciled'):
                    raise MigrationError('PLAN_ALREADY_ACTIVATED')
                if item['kind'] == 'geometry':
                    envelope = item['value']
                    enqueue_delivery(uow, envelope)
                    from lzo.storage.registry import StorageRegistry
                    registry = StorageRegistry('split', {'gis': self.gis})
                    receipt = _apply(registry, envelope, apply_geometry)
                    _acknowledge(uow.session, receipt)
                else:
                    ref = item['value']
                    with source.open(ref) as stream:
                        accepted = target.put(stream, ref.media_type)
                    if (accepted.sha256, accepted.size_bytes) != (ref.sha256, ref.size_bytes):
                        raise MigrationError('DIGEST_MISMATCH')
                uow.session.execute(text('''INSERT INTO storage_migration_items VALUES(:plan,:kind,:key,:digest)
                    ON CONFLICT DO NOTHING'''), {'plan': identifier, 'kind': item['kind'], 'key': item['key'], 'digest': item['digest']})
                changed = uow.session.execute(text('''UPDATE storage_migration_plans
                    SET state='backfill',reconciliation=NULL,backup=NULL
                    WHERE id=:id AND state IN ('backfill','reconciled')'''), {'id': identifier})
                if changed.rowcount != 1:
                    raise MigrationError('PLAN_ALREADY_ACTIVATED')
                uow.commit()
                copied += 1
        return {'copied': copied, 'remaining': len(pending) - copied}

    def _verify(self, values):
        store = get_blob_store(self.target)
        for item in values:
            if item['kind'] == 'blob':
                with store.open(item['value']):
                    pass
                continue
            envelope = item['value']
            with self.gis.connect() as c:
                row = c.execute(text('''SELECT v.*,i.envelope_hash,
                    CASE WHEN CAST(:geo AS text) IS NULL THEN v.geom IS NULL ELSE public.ST_Equals(v.geom,
                    public.ST_SetSRID(public.ST_GeomFromGeoJSON(CAST(:geo AS text)),4326)) END AS geometry_matches
                    FROM gis_geometry_versions v
                    JOIN store_inbox i USING(event_id) WHERE v.resource_id=:id AND v.revision=:rev'''),
                    {'id': envelope.entity_id, 'rev': envelope.aggregate_revision,
                        'geo': json.dumps(envelope.payload['geometry']['normalized']) if envelope.payload['geometry'] else None}).mappings().first()
                if (row is None or row['event_id'] != envelope.event_id or row['payload'] != envelope.payload['geometry']
                        or row['active'] != envelope.payload['active'] or row['payload_hash'] != envelope.payload_hash
                        or row['envelope_hash'] != envelope.envelope_hash or row['geometry_matches'] is not True):
                    raise MigrationError('DIGEST_MISMATCH')
                for name, table, key in [('asset', 'gis_spatial_assets', 'asset_id'),
                        ('observation', 'gis_location_observations', 'observation_id')]:
                    metadata = envelope.payload.get(name)
                    if metadata:
                        stored = c.execute(text(f'SELECT resource_id,payload FROM {table} WHERE {key}=:id'),
                            {'id': metadata[key]}).mappings().first()
                        if stored is None or stored['resource_id'] != envelope.entity_id or stored['payload'] != metadata:
                            raise MigrationError('DIGEST_MISMATCH')
        with self.gis.connect() as c:
            if c.execute(text('SELECT count(*) FROM store_inbox')).scalar_one() != sum(r['kind'] == 'geometry' for r in values):
                raise MigrationError('TARGET_HAS_UNEXPECTED_VERSIONS')
            heads = {}
            for item in values:
                if item['kind'] == 'geometry':
                    envelope = item['value']
                    if envelope.entity_id not in heads or envelope.aggregate_revision > heads[envelope.entity_id].aggregate_revision:
                        heads[envelope.entity_id] = envelope
            for identifier, envelope in heads.items():
                row = c.execute(text('''SELECT h.aggregate_revision,h.event_id,h.aggregate_content_hash,g.revision
                    FROM store_heads h JOIN gis_geometry_heads g ON g.resource_id=h.entity_id WHERE h.entity_id=:id'''),
                    {'id': identifier}).mappings().first()
                if row is None or (row['aggregate_revision'], row['revision'], row['event_id'], row['aggregate_content_hash']) != (
                        envelope.aggregate_revision, envelope.aggregate_revision, envelope.event_id, envelope.aggregate_content_hash):
                    raise MigrationError('DIGEST_MISMATCH')

    def reconcile(self, identifier, actor, backup_manifest=None):
        with PgUnitOfWork(self.core) as uow:
            row = _plan(uow, identifier, actor)
            self._check(row)
            values, manifest = _snapshot(uow)
            checkpoints = {(r['kind'], r['version_key'], r['digest']) for r in uow.session.execute(
                text('SELECT * FROM storage_migration_items WHERE plan_id=:id'), {'id': identifier}).mappings()}
            if row['state'] != 'activated' and any((r['kind'], r['key'], r['digest']) not in checkpoints for r in values):
                raise MigrationError('BACKFILL_REQUIRED')
            self._verify(values)
            manifest.update(status='reconciled', source_identity=row['source_identity'], target_identity=row['target_identity'])
            backup = verify_backup(backup_manifest, row, manifest, core=self.core) if backup_manifest else None
            uow.session.execute(text('''UPDATE storage_migration_plans SET reconciliation=CAST(:value AS jsonb),
                backup=CAST(:backup AS jsonb),epoch=epoch+1,state=CASE WHEN state='activated' THEN state ELSE 'reconciled' END WHERE id=:id'''),
                {'value': json.dumps(manifest), 'backup': json.dumps(backup), 'id': identifier})
            uow.commit()
            return manifest

    def command(self, identifier, actor, action='cutover'):
        with PgUnitOfWork(self.core) as uow:
            return _command(_plan(uow, identifier, actor), action)

    def _activate(self, identifier, actor, approval_id, window, path, action):
        window.verify()
        with PgUnitOfWork(window.core) as uow:
            row = _plan(uow, identifier, actor)
            self._check(row)
            if (connection_identity(window.core) != row['source_identity']['core']
                    or window.evidence['gis']['identity'] != row['target_identity']['gis']):
                raise MigrationError('MAINTENANCE_IDENTITY_MISMATCH')
            if row['state'] != ('reconciled' if action == 'cutover' else 'activated'):
                raise MigrationError('ACTIVATION_STATE_CONFLICT')
            values, current = _snapshot(uow)
            if not row['reconciliation'] or any(current[key] != row['reconciliation'][key] for key in ('data_digest', 'data_position')):
                raise MigrationError('RECONCILIATION_STALE')
            self._verify(values)
            command = _command(row, action)
            backup = verify_backup(row['backup']['manifest_path'], row, current, core=window.core)
            if backup != row['backup']:
                raise MigrationError('BACKUP_CHANGED')
            change = _policy(uow, (identifier,), command)
            ApprovalService(uow).require_executable(actor, approval_id, change.change_digest, change.baseline)
            if action == 'rollback':
                # Global legacy mode maps catalog/timeseries into core. A GIS-only
                # replay cannot represent those accepted versions or GIS extras.
                if (uow.session.execute(text('SELECT EXISTS(SELECT 1 FROM catalog_entries) OR EXISTS(SELECT 1 FROM telemetry_batches)')).scalar_one()
                        or any(r['kind'] == 'geometry' and (r['value'].payload.get('asset') or r['value'].payload.get('observation')) for r in values)):
                    raise MigrationError('RESTORE_REQUIRED: accepted specialized data is incompatible with global legacy mode')
                source, target = get_blob_store(self.source), get_blob_store(self.target)
                for item in values:
                    if item['kind'] == 'blob':
                        ref = item['value']
                        with target.open(ref) as stream:
                            copied = source.put(stream, ref.media_type)
                        if (copied.sha256, copied.size_bytes) != (ref.sha256, ref.size_bytes):
                            raise MigrationError('DIGEST_MISMATCH')
                        with source.open(ref):
                            pass
                from lzo.gis.service import legacy_index
                from lzo.contracts import RevisionRef
                uow.ontology_settings = self.source
                heads = {}
                for item in values:
                    if item['kind'] == 'geometry':
                        envelope = item['value']
                        if envelope.entity_id not in heads or envelope.aggregate_revision > heads[envelope.entity_id].aggregate_revision:
                            heads[envelope.entity_id] = envelope
                for resource, envelope in heads.items():
                    legacy_index(uow, resource, RevisionRef(resource, envelope.aggregate_revision, envelope.event_id),
                        envelope.payload['geometry'] if envelope.payload['active'] else None)
            settings = self.target if action == 'cutover' else self.source
            state = 'activated' if action == 'cutover' else 'rolled_back'
            activation = {'contract': 'multistore-activation-v1', 'plan_id': str(identifier), 'state': state,
                'configuration_digest': _configuration(settings), 'data_digest': current['data_digest'],
                'final_watermarks': current, 'delivery_watermark': [dict(r) for r in uow.session.execute(text('''
                    SELECT target,status,count(*) AS count FROM store_deliveries GROUP BY target,status ORDER BY target,status''')).mappings()],
                'maintenance': window.evidence, 'approval_id': str(approval_id)}
            revision = uow.session.execute(select(resource_heads.c.revision).where(resource_heads.c.resource_id == identifier)).scalar_one()
            append_revision(uow, actor, identifier, revision, activation,
                'storage.migration.activated' if action == 'cutover' else 'storage.migration.rolled_back')
            uow.session.execute(text('''UPDATE storage_migration_plans SET state=:state,epoch=epoch+1,
                activation=CAST(:value AS jsonb) WHERE id=:id'''), {'state': state, 'value': json.dumps(activation), 'id': identifier})
            window.publish_after_commit(path, {**activation, 'settings': settings.model_dump(mode='json')})
            uow.commit()
        return {'status': 'configuration_prepared', 'plan_id': str(identifier), 'action': action,
            'running_processes_switched': False}

    def cutover(self, identifier, actor, approval_id, window, path):
        return self._activate(identifier, actor, approval_id, window, path, 'cutover')

    def rollback(self, identifier, actor, approval_id, window, path):
        return self._activate(identifier, actor, approval_id, window, path, 'rollback')


def create_engine_for(url):
    from sqlalchemy import create_engine
    return create_engine(url, poolclass=__import__('sqlalchemy').pool.NullPool)


def url_identity(url):
    engine = create_engine_for(url)
    try:
        return identity(engine)
    finally:
        engine.dispose()


def load_activation(path):
    """Load private settings only if the canonical core committed this activation."""
    from lzo.config import Settings
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    settings = Settings.model_validate(value['settings'])
    if value.get('contract') != 'multistore-activation-v1' or _configuration(settings) != value.get('configuration_digest'):
        raise MigrationError('ACTIVATION_CONFIG_MISMATCH')
    engine = create_engine_for(settings.database_url)
    try:
        with engine.connect() as c:
            row = c.execute(text('SELECT state,activation,source_identity FROM storage_migration_plans WHERE id=:id'),
                {'id': UUID(value['plan_id'])}).mappings().one()
            if (row['state'] != value['state'] or row['activation'] != {k: v for k, v in value.items() if k != 'settings'}
                    or connection_identity(c) != row['source_identity']['core']):
                raise MigrationError('ACTIVATION_NOT_CURRENT')
        if settings.storage_mode == 'split':
            verify_blob_namespace(settings, 'migration', UUID(value['plan_id']))
            gis = create_engine_for(settings.gis_database_url)
            try:
                with gis.connect() as c:
                    if (connection_identity(c) != value['maintenance']['gis']['identity']
                            or c.execute(text('SELECT plan_id FROM storage_migration_namespace')).scalar_one() != UUID(value['plan_id'])):
                        raise MigrationError('TARGET_CHANGED')
            finally:
                gis.dispose()
        return settings
    finally:
        engine.dispose()


def runtime_settings():
    import os
    from lzo.config import Settings
    path = os.environ.get('LZO_ACTIVATION_CONFIG')
    if not path:
        return Settings()
    contract = json.loads(Path(path).read_text(encoding='utf-8')).get('contract')
    if contract == 'multistore-restore-v1':
        from lzo.storage.restore import load_restored_settings
        return load_restored_settings(path)
    return load_activation(path)


def verify_backup(path, plan, manifest, *, core):
    """Recheck the core-issued receipt and actual coordinated package material."""
    if path is None:
        raise MigrationError('BACKUP_REQUIRED')
    # A locally written JSON status and file checksums are NOT backup proof.
    # The coordinated producer owns reference-completeness and restore checks.
    try:
        from lzo.storage.backup import verify_migration_backup
    except ImportError:
        raise MigrationError('BACKUP_IMPLEMENTATION_REQUIRED') from None
    from lzo.storage.backup_format import BackupError
    try:
        verify_migration_backup(path, core=core, source_identity=plan['source_identity'],
            data_digest=manifest['data_digest'], data_position=manifest['data_position'])
    except BackupError as error:
        raise MigrationError('BACKUP_NOT_VERIFIED: ' + str(error)) from None
    path = Path(path).resolve()
    value = json.loads(path.read_text(encoding='utf-8'))
    if (value.get('contract') != 'multistore-backup-v1' or value.get('status') != 'complete'
            or value.get('source_identity') != plan['source_identity']
            or value.get('data_digest') != manifest['data_digest']
            or value.get('data_position') != manifest['data_position']
            or not {'core', 'gis', 'timeseries', 'catalog', 'rdf'} <= set(value.get('files', {}))):
        raise MigrationError('BACKUP_NOT_VERIFIED')
    for entry in value['files'].values():
        file = (path.parent / entry['path']).resolve()
        if not file.is_relative_to(path.parent) or not file.is_file() or hashlib.sha256(file.read_bytes()).hexdigest() != entry['sha256']:
            raise MigrationError('BACKUP_DIGEST_MISMATCH')
    return {'manifest_path': str(path), 'manifest_digest': digest_change(value)}
