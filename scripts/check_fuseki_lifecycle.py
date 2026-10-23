"""Actual public CLI/HTTP lifecycle acceptance on a NEW isolated owned namespace."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

import httpx
import psutil

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, required=True)
    parser.add_argument('--tool-root', type=Path, required=True)
    parser.add_argument('--java-home', type=Path, required=True)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--untouched-state', type=Path)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    state = args.state.resolve()
    if state.exists() or not state.is_relative_to(ROOT/'.runtime'):
        parser.error('Use a NEW namespace under this worktree .runtime')
    command = [sys.executable, str(ROOT/'scripts/multistore_prerequisites.py')]
    flags = ['--fuseki-state', str(state), '--tool-root', str(args.tool_root.resolve()),
        '--java-home', str(args.java_home.resolve()), '--fuseki-port', str(args.port), '--fuseki-namespace', args.namespace]
    marker = state/'ownership.json'
    observed = []
    def run(action, *extra, refused=False):
        value = subprocess.run(command+[action+'-fuseki', *flags, *extra], capture_output=True, text=True)
        if (value.returncode != 0) != refused:
            raise RuntimeError('Unexpected public CLI result; action='+action+' returncode='+str(value.returncode))
        return value
    def record():
        value = json.loads(marker.read_text())
        observed.append({'pid': value['pid'], 'created': value['created']})
        return value
    def write(value):
        marker.write_text(json.dumps(value), encoding='utf-8')
    def legacy(value):
        return {k: value[k] for k in ('worktree', 'state', 'executable', 'pid')}
    def untouched():
        if not args.untouched_state:
            return None
        path = args.untouched_state/'ownership.json'
        value = json.loads(path.read_text())
        return {'marker_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'pid': value['pid'], 'created': psutil.Process(value['pid']).create_time()}
    retained = untouched()
    url = f'http://127.0.0.1:{args.port}/{args.namespace}'
    try:
        run('start')
        first = record()
        config_hash = hashlib.sha256((state/'config.ttl').read_bytes()).hexdigest()
        assert config_hash == first['config_sha256']
        with httpx.Client(trust_env=False, timeout=10) as client:
            update = 'INSERT DATA { GRAPH <urn:acceptance:fuseki> { <urn:synthetic:subject> <urn:synthetic:retained> "yes" } }'
            assert client.post(url+'/update', content=update, headers={'Content-Type': 'application/sparql-update'}).status_code in (200,204)
            def check():
                value = client.get(url+'/query', params={'query': 'ASK { GRAPH <urn:acceptance:fuseki> { <urn:synthetic:subject> <urn:synthetic:retained> "yes" } }'})
                assert value.status_code == 200 and value.json()['boolean'] is True
            check()
            try:
                write({**first, 'created': first['created']-10})
                assert 'incarnation' in run('stop', refused=True).stderr
                assert psutil.Process(first['pid']).create_time() == first['created']
                write(legacy(first))
                assert 'operator-verified stop' in run('restart', refused=True).stderr
                write({**legacy(first), 'created': first['created']})
                assert 'operator-verified stop' in run('start', '--upgrade-stopped-legacy', refused=True).stderr
                check()
            finally:
                write(first)
            run('stop')
            run('start'); second = record(); check()
            assert second['created'] != first['created']
            run('stop')
            write(legacy(second))
            assert 'upgrade-stopped-legacy' in run('start', refused=True).stderr
            run('start', '--upgrade-stopped-legacy'); upgraded = record(); check()
            # Deliberate crash of only this exact newly-owned incarnation.
            current = psutil.Process(upgraded['pid'])
            assert current.create_time() == upgraded['created'] and Path(current.cwd()).resolve() == state
            current.kill(); current.wait(timeout=15)
            run('start'); recovered = record(); check()
            assert recovered['created'] != upgraded['created']
        assert hashlib.sha256((state/'config.ttl').read_bytes()).hexdigest() == config_hash
        run('stop')
        assert retained == untouched()
        result = {'state': 'passed', 'port': args.port, 'namespace': args.namespace,
            'runtime': str(state.relative_to(ROOT)), 'config_sha256': config_hash, 'observed_incarnations': observed,
            'checks': ['fresh public startup', 'same-state stopped startup preserves synthetic RDF',
                'reused-incarnation refusal', 'running legacy refusal without stopping process',
                'explicit stopped-legacy upgrade preserves config/TDB', 'crashed-owned restart preserves RDF'],
            'isolated_runtime_stopped': True, 'untouched_retained_runtime': retained,
            'scope': 'synthetic RDF on explicit loopback namespace; no retained23030 mutation'}
        args.output.write_text(json.dumps(result, indent=2)+'\n', encoding='utf-8')
        print('Owned isolated Fuseki lifecycle verified; retained runtime unchanged')
    finally:
        if marker.exists():
            value = json.loads(marker.read_text())
            if value.get('contract') == 'owned-fuseki-v1':
                run('stop')


if __name__ == '__main__':
    main()
