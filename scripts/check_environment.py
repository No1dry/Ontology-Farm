"""Read-only prerequisite audit; does not install or change host configuration."""
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def command(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, errors="replace", timeout=30,
                                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        return {"available": result.returncode == 0, "exit_code": result.returncode,
                "output": (result.stdout + result.stderr).strip().replace("\x00", "")[:4000]}
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "reason": type(exc).__name__}


def inspect():
    import psutil
    nodes = list((ROOT / ".tools/node").glob("*/node.exe"))
    node = shutil.which("node") or (str(nodes[0]) if nodes else "node")
    git = shutil.which("git") or str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Git/cmd/git.exe")
    report = {
        "os": platform.platform(), "python": platform.python_version(),
        "cpu_physical": psutil.cpu_count(logical=False), "cpu_logical": psutil.cpu_count(),
        "ram_bytes": psutil.virtual_memory().total, "disk_free_bytes": shutil.disk_usage(ROOT).free,
        "git": command([git, "--version"]), "node": command([node, "--version"]),
        "compose": command(["docker", "compose", "version"]),
        "daemon": command(["docker", "info", "--format", "{{.ServerVersion}}"]),
    }
    if report["daemon"]["available"] and report["compose"]["available"]:
        image = json.loads((ROOT / "infra/versions.lock.json").read_text())["compose"]["postgres"]
        cached = command(["docker", "image", "inspect", image])
        if cached["available"]:
            report["container_probe"] = command([
                "docker", "run", "--rm", "--pull=never", "--network", "none",
                "--entrypoint", "/bin/true", image,
            ])
        elif cached.get("exit_code") == 1 and "No such image" in cached.get("output", ""):
            report["container_probe"] = {"available": False, "reason": "pinned_image_absent", "image": image}
        else:
            report["container_probe"] = cached
    else:
        report["container_probe"] = {"available": False, "reason": "compose_or_daemon_unavailable"}
    if os.name == "nt":
        report["wsl"] = command(["wsl", "--list", "--verbose"])
        report["physical_disks"] = command(["powershell.exe", "-NoProfile", "-Command",
            "Get-PhysicalDisk | Select-Object FriendlyName,MediaType,Size | ConvertTo-Json -Compress"])
    else:
        report["physical_disks"] = command(["lsblk", "-d", "-o", "NAME,ROTA,SIZE,MODEL", "--json"])
    report["compose_acceptance_ready"] = (
        report["compose"]["available"] and report["daemon"]["available"]
        and (report["container_probe"]["available"]
             or report["container_probe"].get("reason") == "pinned_image_absent")
    )
    return report


if __name__ == "__main__":
    report = inspect()
    print(json.dumps(report, indent=2, ensure_ascii=True))
    sys.exit(0 if report["compose_acceptance_ready"] else 2)
