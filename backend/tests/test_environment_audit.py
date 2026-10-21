"""A missing cached probe image must not impersonate a broken Docker daemon."""
import json
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import check_environment  # noqa: E402


def test_ready_daemon_without_cached_pinned_image(tmp_path, monkeypatch):
    (tmp_path / "infra").mkdir()
    image = "postgis/postgis:test@sha256:" + "a" * 64
    (tmp_path / "infra/versions.lock.json").write_text(json.dumps({"compose": {"postgres": image}}))
    monkeypatch.setattr(check_environment, "ROOT", tmp_path)
    calls = []

    def fake_command(args):
        calls.append(args)
        if args[:3] == ["docker", "image", "inspect"]:
            return {"available": False, "exit_code": 1, "output": "No such image"}
        return {"available": True, "exit_code": 0, "output": "ok"}

    monkeypatch.setattr(check_environment, "command", fake_command)
    report = check_environment.inspect()

    assert ["docker", "image", "inspect", image] in calls
    assert report["container_probe"]["reason"] == "pinned_image_absent"
    assert report["compose_acceptance_ready"] is True
    assert not any(args[:2] == ["docker", "run"] for args in calls)


def test_failed_daemon_does_not_probe_container(tmp_path, monkeypatch):
    monkeypatch.setattr(check_environment, "ROOT", tmp_path)
    calls = []

    def fake_command(args):
        calls.append(args)
        if args[:2] == ["docker", "info"]:
            return {"available": False, "exit_code": 1, "output": "daemon unavailable"}
        return {"available": True, "exit_code": 0, "output": "ok"}

    monkeypatch.setattr(check_environment, "command", fake_command)
    report = check_environment.inspect()

    assert report["compose_acceptance_ready"] is False
    assert report["container_probe"]["reason"] == "compose_or_daemon_unavailable"
    assert not any(args[:2] in (["docker", "image"], ["docker", "run"]) for args in calls)
