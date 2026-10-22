"""Native acceptance never adopts a borrowed or escaped runtime."""
import importlib.util
import json
from pathlib import Path

import pytest


def module():
    script = Path(__file__).resolve().parents[3] / 'scripts/multistore_native.py'
    spec = importlib.util.spec_from_file_location('multistore_native', script)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


def test_owned_state_refuses_workspace_escape_and_foreign_marker(tmp_path):
    native = module()
    root = tmp_path / 'worktree'
    (root / '.runtime').mkdir(parents=True)
    with pytest.raises(ValueError):
        native.owned_state(root, tmp_path / 'foreign')
    state = root / '.runtime' / 'owned'
    state.mkdir()
    (state / 'ownership.json').write_text(json.dumps({'worktree': str(tmp_path / 'foreign')}))
    with pytest.raises(ValueError):
        native.owned_state(root, state)


def test_owned_state_requires_marker_on_existing_directory(tmp_path):
    native = module()
    (tmp_path / '.runtime').mkdir()
    state = tmp_path / '.runtime' / 'new'
    assert native.owned_state(tmp_path, state) == state.resolve()
    other = tmp_path / '.runtime' / 'unmarked'
    other.mkdir()
    with pytest.raises(ValueError):
        native.owned_state(tmp_path, other)
