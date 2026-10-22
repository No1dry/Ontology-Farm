"""Explicit secret-safe environment loader for native/Compose acceptance.

Example: python scripts/multistore_acceptance.py --env .runtime/multistore/services.env
  --env .runtime/multistore/maintenance.env -- pytest backend/tests/integration/test_split_gis.py
  -q --basetemp .runtime/acceptance-UNIQUE
Never prints environment values or rewrites any service configuration.
"""
import argparse
import os
import json
from pathlib import Path
import runpy
import sys

ROOT = Path(__file__).resolve().parents[1]


def load_environment(paths):
    from dotenv import load_dotenv
    for filename in paths:
        path = Path(filename).resolve(strict=True)
        load_dotenv(path, override=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env', action='append', required=True, help='Private dotenv file; later files override earlier ones')
    parser.add_argument('--tool-root', type=Path, help='Read-only native tool cache for source fixtures')
    parser.add_argument('--matrix', type=Path, help='Checked-in JSON array of selected pytest node IDs')
    parser.add_argument('--offline', action='store_true', help='Deny non-loopback egress in this Python process and children')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    load_environment(args.env)
    if args.offline:
        from offline_guard import install
        install()
        os.environ['LZO_ACCEPTANCE_OFFLINE'] = '1'
        os.environ['PYTHONPATH'] = os.pathsep.join((str(ROOT/'scripts/offline_python'), str(ROOT/'scripts'),
            os.environ.get('PYTHONPATH', '')))
    if args.tool_root:
        os.environ['LZO_TEST_TOOL_ROOT'] = str(args.tool_root.resolve(strict=True))
    command = args.command
    if command[:1] == ['--']:
        command = command[1:]
    if not command:
        parser.error('Supply -- pytest ... or -- path/to/script.py ...')
    if command[0] == 'pytest':
        if args.matrix:
            command[1:1] = json.loads(args.matrix.read_text(encoding='utf-8'))
        base = next((command[i+1] for i, value in enumerate(command[:-1]) if value == '--basetemp'), None)
        if base is None or not Path(base).resolve().is_relative_to(ROOT / '.runtime') or Path(base).exists():
            parser.error('pytest requires a NEW --basetemp beneath this worktree .runtime')
        import pytest
        return pytest.main(command[1:])
    sys.path.insert(0, str(ROOT / 'scripts'))
    sys.path.insert(0, str(ROOT / 'backend/src'))
    sys.argv = command
    runpy.run_path(command[0], run_name='__main__')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
