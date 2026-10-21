import subprocess
import sys
from pathlib import Path


def test_auth_seed_refuses_non_test_environment_before_any_connection(tmp_path):
    root = Path(__file__).resolve().parents[3]
    env = tmp_path / 'fixture.env'
    env.write_text('APP_ENV=production\n')
    subjects = tmp_path / 'subjects.json'
    subjects.write_text('{"editor-a":"synthetic-subject"}')
    result = subprocess.run([sys.executable, '-B', str(root / 'scripts/seed_workbench.py'),
        '--env', str(env), '--subjects', str(subjects), '--output', str(tmp_path/'manifest.json')],
        capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'APP_ENV=test' in result.stderr
    assert not (tmp_path/'manifest.json').exists()
