"""Checked loopback Fuseki lifecycle for a marked, worktree-owned TDB namespace."""
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import time

from multistore_native import owned_state


def lifecycle(root, action, state, tool_root, java_home, port, namespace, *, upgrade_stopped_legacy=False):
    import httpx
    import psutil
    root = Path(root).resolve()
    if not 1024 <= port <= 65535 or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,63}', namespace):
        raise ValueError('Explicit unprivileged port and simple namespace required')
    java = (Path(java_home)/'bin/java.exe').resolve(strict=True)
    home = (Path(tool_root)/'fuseki/apache-jena-fuseki-6.2.0').resolve(strict=True)
    jar = (home/'fuseki-server.jar').resolve(strict=True)
    owned = owned_state(root, state)
    marker = owned/'ownership.json'
    record = json.loads(marker.read_text())
    template = (root/'database/fuseki/config.ttl').read_text(encoding='utf-8')
    config = template.replace('fuseki:name "lzo"', f'fuseki:name "{namespace}"').replace(
        'tdb2:location "databases/lzo"', f'tdb2:location "databases/{namespace}"')
    digest = hashlib.sha256(config.encode()).hexdigest()
    target = owned/'config.ttl'
    data = owned/'databases'/namespace
    if not data.resolve().is_relative_to(owned):
        raise RuntimeError('Refusing escaped TDB location')

    def free_port():
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))

    def process():
        if not record.get('pid'):
            return None
        try:
            value = psutil.Process(record['pid'])
        except psutil.NoSuchProcess:
            return None
        # A live legacy/reused PID is ambiguous: never adopt or terminate it.
        if (record.get('created') is None or abs(value.create_time()-record['created']) > .01 or
                Path(value.cwd()).resolve() != owned or Path(value.exe()).resolve() != java):
            raise RuntimeError('Refusing ambiguous Fuseki process incarnation; operator-verified stop required')
        return value

    if not target.exists():
        if action != 'start' or set(record) != {'worktree'} or set(p.name for p in owned.iterdir()) != {'ownership.json'}:
            raise RuntimeError('Fresh Fuseki requires an empty marked runtime')
        free_port()
        target.write_text(config, encoding='utf-8', newline='\n')
        data.mkdir(parents=True)
        record.update(state=str(owned), executable=str(java), port=port, namespace=namespace,
            config_sha256=digest, created=None, pid=None, contract='owned-fuseki-v1')
        marker.write_text(json.dumps(record), encoding='utf-8')
    else:
        if (Path(record.get('state', '')).resolve() != owned or
                Path(record.get('executable', '')).resolve() != java or
                target.read_text(encoding='utf-8') != config or not data.is_dir()):
            raise RuntimeError('Fuseki state/config/data identity differs from explicit inputs')
        digest = hashlib.sha256(target.read_bytes()).hexdigest()
        if record.get('contract') != 'owned-fuseki-v1':
            # Legacy config/TDB is retained verbatim. Upgrade only when the old
            # recorded PID is absent and the caller explicitly selected it.
            if process() is not None:
                raise RuntimeError('Running legacy state requires an operator-verified stop')
            if action != 'start' or not upgrade_stopped_legacy:
                raise RuntimeError('Stopped legacy state requires explicit --upgrade-stopped-legacy')
            free_port()
            record.update(port=port, namespace=namespace, config_sha256=digest,
                contract='owned-fuseki-v1', created=None, pid=None)
            marker.write_text(json.dumps(record), encoding='utf-8')
        elif (record['port'] != port or record['namespace'] != namespace or record['config_sha256'] != digest):
            raise RuntimeError('Fuseki declared port/namespace/config changed')
    current = process()
    if action in ('stop', 'restart') and current is not None:
        current.terminate()
        current.wait(timeout=30)
        current = None
        record['stopped'] = True
        marker.write_text(json.dumps(record), encoding='utf-8')
    if action == 'stop':
        return {'state': 'stopped', 'port': port, 'namespace': namespace}
    if current is not None:
        raise RuntimeError('Owned Fuseki is already running; use explicit restart')
    free_port()
    environment = dict(os.environ, FUSEKI_BASE=str(owned), FUSEKI_HOME=str(home), JAVA_HOME=str(Path(java_home).resolve()))
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (owned/'server.log').open('ab') as log:
        child = subprocess.Popen([str(java), '-Xmx1g', '-jar', str(jar), '--config='+str(target),
            '--localhost', '--port='+str(port)], cwd=owned, env=environment, stdin=subprocess.DEVNULL,
            stdout=log, stderr=log, creationflags=flags)
    record.update(pid=child.pid, created=psutil.Process(child.pid).create_time(), stopped=False)
    marker.write_text(json.dumps(record), encoding='utf-8')
    deadline = time.monotonic()+120
    while time.monotonic() < deadline:
        if child.poll() is not None:
            raise RuntimeError('Owned Fuseki exited; inspect private server.log')
        try:
            server = psutil.Process(child.pid)
            owns_port = any(c.status == psutil.CONN_LISTEN and c.laddr.port == port
                for c in server.net_connections(kind='tcp'))
            if not owns_port:
                time.sleep(.2)
                continue
            response = httpx.get(f'http://127.0.0.1:{port}/{namespace}/query', params={'query': 'ASK {}'}, timeout=2, trust_env=False)
            if response.status_code == 200:
                return {'state': 'ready', 'port': port, 'namespace': namespace,
                    'pid': record['pid'], 'created': record['created'], 'config_sha256': digest}
        except httpx.HTTPError:
            pass
        time.sleep(.2)
    raise RuntimeError('Owned Fuseki readiness timed out; inspect private log')
