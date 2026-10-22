"""Loopback-only owned Keycloak/API/worker lifecycle, explicit env and tools.

PostgreSQL/S3/Fuseki keep their existing independent ownership and lifecycle.
Keycloak uses an OWNED copy because augmentation/data write into its tree.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time

from multistore_native import owned_state
from multistore_acceptance import load_environment

ROOT = Path(__file__).resolve().parents[1]
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def stop(state, label):
    import psutil
    record = state / (label + '.json')
    if not record.exists():
        return
    item = json.loads(record.read_text())
    try:
        process = psutil.Process(item['pid'])
        if abs(process.create_time()-item['created']) > .01 or Path(process.cwd()).resolve() != Path(item['cwd']):
            raise RuntimeError('Refusing a reused/unowned service process')
        children = process.children(recursive=True)
        for child in reversed(children):
            child.terminate()
        process.terminate()
        _, alive = psutil.wait_procs([process, *children], timeout=15)
        for child in alive:
            child.kill()
    except psutil.NoSuchProcess:
        pass
    record.unlink()


def launch(state, label, args, environment, cwd):
    import psutil
    if (state / (label+'.json')).exists():
        raise RuntimeError('Existing service record: stop explicitly before starting')
    with (state / (label+'.log')).open('ab') as log:
        child = subprocess.Popen(list(map(str, args)), env=environment, cwd=cwd,
            stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=FLAGS)
    (state / (label+'.json')).write_text(json.dumps({'pid': child.pid,
        'created': psutil.Process(child.pid).create_time(), 'cwd': str(Path(cwd).resolve())}))


def require_free_ports(*ports):
    # A ready endpoint from another workspace must never be adopted as ours.
    for port in ports:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))


def wait(url, seconds=180):
    import httpx
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(.5)
    raise RuntimeError('Owned loopback service did not become ready; inspect private log')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['start-idp', 'stop-idp', 'start-app', 'stop-app', 'restart-app', 'restart-fuseki'])
    parser.add_argument('--env', action='append', required=True)
    parser.add_argument('--tool-root', type=Path)
    parser.add_argument('--java-home', type=Path)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--idp-port', type=int, default=28080)
    parser.add_argument('--management-port', type=int, default=29000)
    parser.add_argument('--api-port', type=int, default=28000)
    parser.add_argument('--fuseki-state', type=Path)
    parser.add_argument('--fuseki-port', type=int, default=23030)
    args = parser.parse_args()
    state = owned_state(ROOT, args.state)
    load_environment(args.env)
    if args.action == 'restart-fuseki':
        import psutil
        if not args.fuseki_state or not args.tool_root or not args.java_home:
            parser.error('Fuseki needs explicit owned --fuseki-state, read-only --tool-root and --java-home')
        owned = owned_state(ROOT, args.fuseki_state)
        marker = owned / 'ownership.json'
        record = json.loads(marker.read_text())
        process = psutil.Process(record['pid'])
        if (Path(record['state']).resolve() != owned or Path(process.cwd()).resolve() != owned or
                Path(process.exe()).resolve() != Path(record['executable']).resolve()):
            raise RuntimeError('Refusing unowned Fuseki process')
        java = (args.java_home / 'bin/java.exe').resolve(strict=True)
        home = (args.tool_root / 'fuseki/apache-jena-fuseki-6.2.0').resolve(strict=True)
        process.terminate(); process.wait(timeout=30)
        environment = dict(os.environ, FUSEKI_BASE=str(owned), FUSEKI_HOME=str(home), JAVA_HOME=str(args.java_home))
        with (owned/'server.log').open('ab') as log:
            child = subprocess.Popen([str(java), '-Xmx1g', '-jar', str(home/'fuseki-server.jar'),
                '--config='+str(owned/'config.ttl'), '--localhost', '--port='+str(args.fuseki_port)],
                cwd=owned, env=environment, stdin=subprocess.DEVNULL, stdout=log, stderr=log, creationflags=FLAGS)
        record.update(pid=child.pid, executable=str(java.resolve()))
        marker.write_text(json.dumps(record))
        wait(f'http://127.0.0.1:{args.fuseki_port}/lzo/query?query=ASK%7B%7D')
        print('Owned Fuseki restarted with retained TDB state')
        return
    if args.action == 'stop-idp':
        stop(state, 'keycloak')
        return
    if args.action in ('stop-app', 'restart-app'):
        for label in ('worker', 'api'):
            stop(state, label)
        if args.action == 'stop-app':
            return
    environment = dict(os.environ)
    if args.action == 'start-idp':
        require_free_ports(args.idp_port, args.management_port)
        if not args.tool_root or not args.java_home:
            parser.error('Keycloak needs explicit read-only --tool-root and --java-home')
        owned = state / 'keycloak'
        if not owned.exists():
            shutil.copytree(args.tool_root / 'keycloak/keycloak-26.8.1', owned,
                ignore=shutil.ignore_patterns('data', 'log', 'logs'))
        private = state / 'idp.env'
        if not private.exists():
            origin = f'http://127.0.0.1:{args.idp_port}'
            values = {'LZO_OIDC_ISSUER': origin+'/realms/lzo',
                'LZO_OIDC_DISCOVERY_URL': origin+'/realms/lzo/.well-known/openid-configuration',
                'LZO_OIDC_BACKCHANNEL_BASE': origin+'/realms/lzo',
                'LZO_IDENTITY_ADMIN_URL': origin+'/admin/realms/lzo',
                'LZO_OIDC_CLIENT_SECRET': secrets.token_urlsafe(32),
                'LZO_IDENTITY_ADMIN_CLIENT_SECRET': secrets.token_urlsafe(32),
                'KC_BOOTSTRAP_ADMIN_PASSWORD': secrets.token_urlsafe(32),
                'LZO_SESSION_ENCRYPTION_KEY': __import__('cryptography.fernet', fromlist=['Fernet']).Fernet.generate_key().decode()}
            private.write_text(''.join(f'{key}={value}\n' for key,value in values.items()))
        from dotenv import dotenv_values
        values = dict(dotenv_values(private))
        environment.update(values)
        environment.setdefault('LZO_PUBLIC_ORIGIN', f'http://127.0.0.1:{args.api_port}')
        environment.update(JAVA_HOME=str(args.java_home), KC_BOOTSTRAP_ADMIN_USERNAME='acceptance-admin',
            KC_HTTP_HOST='127.0.0.1', KC_HTTP_PORT=str(args.idp_port),
            KC_HTTP_MANAGEMENT_HOST='127.0.0.1', KC_HTTP_MANAGEMENT_PORT=str(args.management_port))
        realm = json.loads((ROOT / 'infra/keycloak/realm-template.json').read_text())
        realm['sslRequired'] = 'none'  # loopback native synthetic fixture only
        imports = owned / 'data/import'
        imports.mkdir(parents=True, exist_ok=True)
        (imports / 'lzo-realm.json').write_text(json.dumps(realm))
        launch(state, 'keycloak', ['cmd.exe', '/d', '/c', owned/'bin/kc.bat', 'start-dev', '--import-realm'], environment, owned)
        wait(values['LZO_OIDC_DISCOVERY_URL'])
        print('Owned Keycloak ready; private idp.env must be loaded for acceptance')
    else:
        require_free_ports(args.api_port)
        from native_dev import runtime_environment
        environment = runtime_environment(environment)
        environment = {key: value for key,value in environment.items() if not key.endswith('_MIGRATION_DATABASE_URL')
            and key != 'LZO_TIMESERIES_MAINTENANCE_DATABASE_URL'}
        environment.update(LZO_WRITER_OWNERSHIP_DIR=str(state/'writers'), LZO_WRITER_WORKSPACE=str(ROOT))
        environment['PYTHONPATH'] = str(ROOT / 'backend/src')
        launch(state, 'api', [sys.executable, '-m', 'uvicorn', 'lzo.main:create_app', '--factory',
            '--host', '127.0.0.1', '--port', args.api_port, '--no-access-log'], environment, ROOT)
        launch(state, 'worker', [sys.executable, '-m', 'lzo.worker'], environment, ROOT)
        wait(f'http://127.0.0.1:{args.api_port}/health/ready')
        print('Owned API/worker ready on loopback; native supplemental acceptance only')


if __name__ == '__main__':
    main()
