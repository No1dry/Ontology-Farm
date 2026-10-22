"""Verified stopped writers and bounded database locks for offline activation.

This module never kills a process, changes a role or controls a borrowed service.
Private Fuseki/S3 listeners must remain isolated from direct external writers.
"""
import json
import os
from pathlib import Path
import sys

import psutil
from sqlalchemy import text

from lzo.storage.migration import MigrationError, connection_identity


def record_writer(service, *, directory=None, workspace=None):
    directory = directory or os.environ.get('LZO_WRITER_OWNERSHIP_DIR')
    if directory is None:
        return
    if service not in ('api', 'worker'):
        raise MigrationError('UNKNOWN_WRITER')
    workspace = Path(workspace or os.environ['LZO_WRITER_WORKSPACE']).resolve()
    directory = Path(directory).resolve()
    if not directory.is_relative_to(workspace):
        raise MigrationError('WRITER_OWNERSHIP_PATH')
    directory.mkdir(parents=True, exist_ok=True)
    process = psutil.Process()
    value = {'service': service, 'pid': process.pid, 'created': process.create_time(),
        'executable': process.exe(), 'workspace': str(workspace)}
    temporary = directory / (service + '.tmp')
    temporary.write_text(json.dumps(value), encoding='utf-8')
    temporary.replace(directory / (service + '.json'))


class MaintenanceWindow:
    """Hold locks from final check through approval/audit/config preparation.

    API/worker processes include ingest/upload/sync/RDF publishers. Their startup
    records must identify stopped incarnations; every app-role DB connection is
    refused, even idle pooled connections. Locks then prevent a racing writer
    from modifying any current core/GIS table during the critical phase.
    """
    def __init__(self, core, gis, markers, workspace, *, additional=None):
        if set(additional or {}) - {'timeseries', 'catalog'}:
            raise MigrationError('UNKNOWN_MAINTENANCE_STORE')
        self.engines = {'core': core, 'gis': gis, **(additional or {})}
        self.markers, self.workspace = Path(markers).resolve(), Path(workspace).resolve()
        self.connections, self.transactions, self.publications = {}, {}, []
        self.active = False
        self.evidence = {}

    def _writers_stopped(self):
        if not self.markers.is_relative_to(self.workspace):
            raise MigrationError('WRITER_OWNERSHIP_PATH')
        evidence = []
        for service in ('api', 'worker'):
            path = self.markers / (service + '.json')
            if not path.is_file():
                raise MigrationError('WRITER_OWNERSHIP_REQUIRED')
            value = json.loads(path.read_text(encoding='utf-8'))
            if (value.get('service') != service or Path(value.get('workspace', '')).resolve() != self.workspace
                    or type(value.get('pid')) is not int or type(value.get('created')) not in (int, float)
                    or not Path(value.get('executable', '')).is_file()):
                raise MigrationError('WRITER_OWNERSHIP_MISMATCH')
            try:
                process = psutil.Process(value['pid'])
                if process.create_time() == value['created']:
                    raise MigrationError('WRITER_RUNNING')
            except psutil.NoSuchProcess:
                pass
            evidence.append({'service': service, 'pid': value['pid'], 'created': value['created'], 'stopped': True})
        return evidence

    def _no_connections(self, c, domain):
        role = 'lzo_app' if domain == 'core' else 'lzo_' + domain + '_app'
        if c.execute(text('''SELECT count(*) FROM pg_stat_activity
                WHERE datname=current_database() AND usename=:role AND pid<>pg_backend_pid()'''), {'role': role}).scalar_one():
            raise MigrationError('WRITER_CONNECTIONS')

    def __enter__(self):
        try:
            processes = self._writers_stopped()
            for domain, engine in self.engines.items():
                c = engine.connect()
                self.connections[domain] = c
                self.transactions[domain] = c.begin()
                c.execute(text("SET LOCAL lock_timeout='2s'"))
                c.execute(text("SET LOCAL statement_timeout='30s'"))
                self._no_connections(c, domain)
                tables = c.execute(text('''SELECT tablename FROM pg_tables WHERE schemaname=current_schema()
                    ORDER BY tablename''')).scalars().all()
                # Identifiers come from PostgreSQL, quote rather than interpolate SQL.
                quote = c.dialect.identifier_preparer.quote
                if tables:
                    c.exec_driver_sql('LOCK TABLE ' + ','.join(quote(t) for t in tables) + ' IN SHARE ROW EXCLUSIVE MODE NOWAIT')
                self.evidence[domain] = {'identity': connection_identity(c), 'locked_tables': len(tables)}
            self.evidence['processes'] = processes
            self.active = True
            return self
        except Exception:
            self.__exit__(*sys.exc_info())
            raise

    @property
    def core(self):
        if not self.active:
            raise MigrationError('MAINTENANCE_WINDOW_REQUIRED')
        return self.connections['core']

    def verify(self):
        if not self.active:
            raise MigrationError('MAINTENANCE_WINDOW_REQUIRED')
        self._writers_stopped()
        for domain, c in self.connections.items():
            self._no_connections(c, domain)

    def publish_after_commit(self, path, payload):
        path = Path(path).resolve()
        if path.exists():
            raise MigrationError('ACTIVATION_PATH_EXISTS')
        self.publications.append((path, payload))

    def __exit__(self, kind, value, traceback):
        try:
            if kind is None:
                self.verify()
                # GIS is read-locked here; only core approval/audit/legacy index
                # changes are committed. No distributed commit claim is made.
                for domain in reversed(tuple(self.transactions)):
                    self.transactions[domain].commit()
                for path, payload in self.publications:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open('x', encoding='utf-8') as output:
                        json.dump(payload, output, ensure_ascii=False, indent=2)
            else:
                for transaction in self.transactions.values():
                    if transaction.is_active:
                        transaction.rollback()
        finally:
            self.active = False
            for c in self.connections.values():
                c.close()
