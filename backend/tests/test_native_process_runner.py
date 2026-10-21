"""The launcher must return when a daemon keeps inherited output handles open."""
import sys
import time
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

from native_dev import run, native_pytest_command, pytest_summary, report_pytest_result, RUNTIME  # noqa: E402


def test_run_does_not_wait_for_spawned_child_output_pipe():
    child = "import time; time.sleep(3)"
    parent = (
        "import subprocess,sys; "
        f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
        "print('launched')"
    )
    started = time.monotonic()
    run([sys.executable, "-c", parent])
    assert time.monotonic() - started < 1.5


def test_native_pytest_command_uses_fresh_worktree_temp():
    command = native_pytest_command("lzo-test-bootstrap")
    index = command.index("--basetemp")
    temporary = Path(command[index + 1])
    assert temporary.parent == RUNTIME
    assert temporary.name.startswith("pytest-lzo-test-bootstrap-")
    assert Path(native_pytest_command("lzo-test-bootstrap")[-1]) != temporary


def test_native_pytest_summary_excludes_captured_secrets():
    output = "synthetic secret in traceback\nFAILED test_x\n2 passed, 1 failed in 0.50s\n"
    assert pytest_summary(output) == "2 passed, 1 failed in 0.50s"


def test_native_pytest_summary_retains_long_duration_suffix():
    output = "synthetic diagnostic\n62 passed in 64.95s (0:01:04)\n"
    assert pytest_summary(output) == "62 passed in 64.95s (0:01:04)"


def test_success_report_keeps_aggregate_without_log(tmp_path):
    path = tmp_path / "pytest.log"
    message = report_pytest_result(0, "test output\n2 passed in 0.50s\n", path, {})
    assert message == "2 passed in 0.50s"
    assert not path.exists()


def test_failure_report_names_test_and_saves_sanitized_context(tmp_path):
    path = tmp_path / "pytest.log"
    password = "SYNTHETIC_PASSWORD_FOR_TEST"
    dsn = f"postgresql+psycopg://user:{password}@host/db"
    output = (f"E   AssertionError: connection {dsn} failed\n"
              f"E   password={password}\n"
              "FAILED tests/test_example.py::test_connection - AssertionError\n"
              "1 failed in 0.50s\n")
    message = report_pytest_result(1, output, path,
                                   {"LZO_APP_PASSWORD": password, "LZO_DATABASE_URL": dsn})
    saved = path.read_text(encoding="utf-8")
    assert "1 failed in 0.50s" in message
    assert "tests/test_example.py::test_connection" in message
    assert "AssertionError" in message
    assert str(path) in message
    assert "AssertionError" in saved
    assert password not in message + saved
    assert dsn not in message + saved
    assert "[redacted]" in saved


def test_failure_without_summary_still_reports_error_and_log(tmp_path):
    path = tmp_path / "pytest.log"
    output = "ImportError: cannot import module\nTOKEN=SYNTHETIC_TOKEN_FOR_TEST\n"
    message = report_pytest_result(4, output, path, {"LZO_OIDC_CLIENT_SECRET": "SYNTHETIC_TOKEN_FOR_TEST"})
    assert "pytest exited 4 without a summary" in message
    assert "ImportError" in message
    assert str(path) in message
    assert "SYNTHETIC_TOKEN_FOR_TEST" not in message + path.read_text(encoding="utf-8")
