"""Supplemental Windows real-service runner, never a substitute for Compose acceptance.

All processes are ordinary hidden children, all TCP bindings are loopback, all
state is project-local. Only lzo-test-* projects are accepted by this runner.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile

from dev import certificate

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / ".tools"
RUNTIME = ROOT / ".runtime"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def project_path(project):
    if not project.startswith("lzo-test-") or not all(c.isalnum() or c == "-" for c in project):
        raise ValueError("Native mode requires a dedicated lzo-test-* project")
    result = (RUNTIME / "projects" / project).resolve()
    if not result.is_relative_to((RUNTIME / "projects").resolve()):
        raise ValueError("Project path escapes local runtime")
    result.mkdir(parents=True, exist_ok=True)
    return result


def tool_paths():
    pg = TOOLS / "postgres/pgsql/bin"
    fuseki = TOOLS / "fuseki/apache-jena-fuseki-6.2.0"
    keycloak = TOOLS / "keycloak/keycloak-26.8.1"
    java = os.environ.get("LZO_JAVA_HOME") or os.environ.get("JAVA_HOME")
    if not java:
        candidates = sorted(Path("C:/Program Files/JetBrains").glob("PyCharm*/jbr"))
        if candidates:
            java = str(candidates[-1])
    if not java or not (Path(java) / "bin/java.exe").exists():
        raise RuntimeError("Set LZO_JAVA_HOME to an existing Java 21 installation")
    for path in (pg / "postgres.exe", fuseki / "fuseki-server.jar", keycloak / "bin/kc.bat"):
        if not path.exists():
            raise RuntimeError(f"Missing portable tool: {path.name}; run native_dev.py prepare")
    return pg, fuseki, keycloak, Path(java)


def prepare_tools():
    lock = json.loads((ROOT / "infra/versions.lock.json").read_text())
    TOOLS.mkdir(parents=True, exist_ok=True)
    for name, archive in lock["native"]["archives"].items():
        if name == "node":
            continue
        target = TOOLS / name
        if target.exists():
            continue
        path = TOOLS / archive["url"].rsplit("/", 1)[1]
        if not path.exists():
            print(f"Downloading locked {name} archive", flush=True)
            with urllib.request.urlopen(archive["url"], timeout=60) as response, path.open("wb") as out:
                shutil.copyfileobj(response, out)
        with path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        if digest != archive["sha256"]:
            raise RuntimeError(f"{name} archive checksum mismatch")
        with zipfile.ZipFile(path) as bundle:
            bundle.extractall(target)
    # OSGeo bundle is an overlay containing extension SQL, DLLs, and PROJ data.
    pg = TOOLS / "postgres/pgsql"
    overlay = TOOLS / "postgis"
    candidates = list(overlay.rglob("postgis.control"))
    if not candidates:
        raise RuntimeError("PostGIS extension control file absent from locked bundle")
    overlay_root = candidates[0].parent.parent.parent
    for directory in ("bin", "lib", "share"):
        source = overlay_root / directory
        if source.exists():
            shutil.copytree(source, pg / directory, dirs_exist_ok=True)
    print("Portable service archives verified and prepared.")


def environment(project):
    from dotenv import dotenv_values
    directory = project_path(project)
    path = directory / "services.env"
    if not path.exists():
        values = {name: secrets.token_urlsafe(32) for name in (
            "POSTGRES_PASSWORD", "LZO_OWNER_PASSWORD", "LZO_APP_PASSWORD", "KEYCLOAK_DB_PASSWORD",
            "KC_BOOTSTRAP_ADMIN_PASSWORD", "LZO_OIDC_CLIENT_SECRET")}
        origin = "https://localhost:18443"
        values.update({
            "LZO_DATABASE_URL": f"postgresql+psycopg://lzo_app:{values['LZO_APP_PASSWORD']}@127.0.0.1:15432/lzo",
            "LZO_MIGRATION_DATABASE_URL": f"postgresql+psycopg://lzo_owner:{values['LZO_OWNER_PASSWORD']}@127.0.0.1:15432/lzo",
            "LZO_POSTGRES_ADMIN_URL": f"postgresql://postgres:{values['POSTGRES_PASSWORD']}@127.0.0.1:15432/postgres",
            "LZO_FUSEKI_URL": "http://127.0.0.1:13030/lzo",
            "LZO_PUBLIC_ORIGIN": origin,
            "LZO_OIDC_ISSUER": origin + "/identity/realms/lzo",
            "LZO_OIDC_DISCOVERY_URL": "http://127.0.0.1:18080/identity/realms/lzo/.well-known/openid-configuration",
            "LZO_OIDC_CLIENT_ID": "lzo-api",
            "LZO_FILE_ROOT": (directory / "artifacts").as_posix(),
            "LZO_CA_BUNDLE": (RUNTIME / "certs/localhost.crt").as_posix(),
            "LZO_TEST_PROJECT": project, "LZO_SERVICE_MODE": "native",
        })
        path.write_text("\n".join(f"{k}={v}" for k, v in values.items()) + "\n")
        path.chmod(0o600)
    values = dict(dotenv_values(path))
    from cryptography.fernet import Fernet
    additions = {"LZO_SESSION_ENCRYPTION_KEY": Fernet.generate_key().decode(),
                 "LZO_WORKER_HANDLER_MODULES": "lzo.worker.handlers",
                 "LZO_IDENTITY_ADMIN_CLIENT_SECRET": secrets.token_urlsafe(32),
                 "LZO_IDENTITY_ADMIN_URL": values["LZO_PUBLIC_ORIGIN"] + "/identity/admin/realms/lzo"}
    if any(key not in values for key in additions):
        for key, value in additions.items():
            values.setdefault(key, value)
        path.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    # Stable discovery path for integration tests and subsequent foundation tasks.
    RUNTIME.mkdir(exist_ok=True)
    shutil.copyfile(path, RUNTIME / "native.env")
    return values


def run(args, **kwargs):
    # A daemon spawned by pg_ctl inherits standard handles. A PIPE makes
    # communicate() wait for that daemon instead of returning with pg_ctl.
    with tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace") as output:
        result = subprocess.run([str(x) for x in args], creationflags=FLAGS,
                                stdout=output, stderr=output, text=True, **kwargs)
        if result.returncode:
            output.seek(0)
            # Never echo environment, DSNs, or full command containing credentials.
            raise RuntimeError(f"{Path(args[0]).name} failed (exit {result.returncode}): {output.read()[-1500:]}")
        return result


def launch(name, args, directory, env, cwd=None):
    import psutil
    with (directory / f"{name}.log").open("ab") as log:
        process = subprocess.Popen([str(x) for x in args], env=env, cwd=cwd or ROOT,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
    return {"name": name, "pid": process.pid, "created": psutil.Process(process.pid).create_time()}


def runtime_environment(environment):
    """Keep owner/admin and other service secrets out of API and worker children."""
    privileged = {"LZO_MIGRATION_DATABASE_URL", "LZO_POSTGRES_ADMIN_URL",
                  "LZO_OWNER_PASSWORD", "POSTGRES_PASSWORD", "LZO_APP_PASSWORD",
                  "KEYCLOAK_DB_PASSWORD", "KC_DB_PASSWORD", "KC_BOOTSTRAP_ADMIN_PASSWORD"}
    return {key: value for key, value in environment.items() if key not in privileged}


def native_pytest_command(project):
    """Use a fresh workspace-local temp root on Windows hosts with locked system temp."""
    temporary = RUNTIME / f"pytest-{project}-{secrets.token_hex(8)}"
    return [sys.executable, "-m", "pytest", "tests", "-q", "--basetemp", str(temporary)]


def pytest_summary(output):
    """Expose only aggregate counts, never captured tracebacks or credentials."""
    summaries = re.findall(
        r"(?m)^\d+ (?:passed|failed|errors?)(?:, \d+ (?:passed|failed|errors?|skipped|warnings))* in \d+(?:\.\d+)?s(?: \(\d+:\d{2}:\d{2}\))?$",
        output,
    )
    return summaries[-1] if summaries else None


def sanitize_pytest_output(output, environment):
    """Keep failure evidence while masking credentials available to the child."""
    sensitive_name = re.compile(r"PASSWORD|SECRET|TOKEN|CREDENTIAL|DATABASE_URL|ADMIN_URL|DSN|API_KEY", re.I)
    secrets_to_mask = {value for name, value in environment.items()
                       if sensitive_name.search(name) and isinstance(value, str) and value}
    for secret in sorted(secrets_to_mask, key=len, reverse=True):
        output = output.replace(secret, "[redacted]")
    output = re.sub(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@]+:[^\s/@]+@",
                    lambda match: match.group(1) + "[redacted]@", output)
    output = re.sub(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~-]+", r"\1[redacted]", output)
    output = re.sub(r"(?i)\b(password|secret|token|credential|api[_-]?key)\s*[:=]\s*[^\s,;]+",
                    lambda match: match.group(1) + "=[redacted]", output)
    return output


def report_pytest_result(returncode, output, log_path, environment):
    """Return a concise console report and save sanitized failure diagnostics."""
    summary = pytest_summary(output)
    if returncode == 0:
        return summary or "pytest passed (exit 0; summary unavailable)"
    sanitized = sanitize_pytest_output(output, environment)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(sanitized or "(pytest produced no output)\n", encoding="utf-8")
    named = re.findall(r"(?m)^(?:FAILED|ERROR) [^\r\n]+", sanitized)[:8]
    context = re.findall(r"(?m)^E   [^\r\n]+", sanitized)[-4:]
    if not named and not context:
        context = [line for line in sanitized.splitlines() if line.strip()][-5:]
    excerpt = [line[:400] for line in (*named, *context)]
    return "\n".join((summary or f"pytest exited {returncode} without a summary",
                      f"pytest exit code: {returncode}", *excerpt,
                      f"sanitized pytest log: {log_path}"))


def wait_http(url, timeout=180, verify=True):
    import httpx
    deadline = time.monotonic() + timeout
    if isinstance(verify, str):
        verify = ssl.create_default_context(cafile=verify)
    with httpx.Client(verify=verify, timeout=3) as client:
        while time.monotonic() < deadline:
            try:
                response = client.get(url)
                if response.status_code == 200:
                    return response
            except httpx.HTTPError:
                pass
            time.sleep(1)
    raise RuntimeError(f"Service failed to become ready on loopback URL {url}")


def start(project):
    import psycopg
    from psycopg import sql
    directory = project_path(project)
    if (directory / "processes.json").exists():
        raise RuntimeError("Process state exists; use restart or stop before starting")
    values = environment(project)
    certificate(RUNTIME / "certs")
    pg, fuseki, kc, java = tool_paths()
    env = os.environ | values | {"JAVA_HOME": str(java), "PYTHONPATH": str(ROOT / "backend/src"),
                                "PATH": str(pg) + os.pathsep + os.environ.get("PATH", "")}
    data = directory / "postgres"
    if not (data / "PG_VERSION").exists():
        password_file = directory / "initdb-password"
        password_file.write_text(values["POSTGRES_PASSWORD"])
        try:
            run([pg / "initdb.exe", "-D", data, "--username=postgres", "--pwfile", password_file,
                 "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--encoding=UTF8", "--locale=C"])
        finally:
            password_file.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a") as config:
            config.write("\nlisten_addresses = '127.0.0.1'\nport = 15432\n")
    run([pg / "pg_ctl.exe", "-D", data, "-l", directory / "postgres.log", "-w", "start"], env=env)
    with psycopg.connect(values["LZO_POSTGRES_ADMIN_URL"], autocommit=True) as conn:
        for role, secret in (("lzo_owner", "LZO_OWNER_PASSWORD"), ("lzo_app", "LZO_APP_PASSWORD"), ("keycloak", "KEYCLOAK_DB_PASSWORD")):
            if not conn.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(values[secret])))
        for db, owner in (("lzo", "lzo_owner"), ("keycloak", "keycloak")):
            if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (db,)).fetchone():
                conn.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(db), sql.Identifier(owner)))
    admin_lzo = values["LZO_POSTGRES_ADMIN_URL"].rsplit("/", 1)[0] + "/lzo"
    with psycopg.connect(admin_lzo, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        conn.execute("GRANT USAGE ON SCHEMA public TO lzo_app")
    fuseki_base = directory / "fuseki"
    (fuseki_base / "databases").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "database/fuseki/config.ttl", fuseki_base / "config.ttl")
    env.update({"FUSEKI_BASE": str(fuseki_base), "FUSEKI_HOME": str(fuseki),
        "KC_DB": "postgres", "KC_DB_URL": "jdbc:postgresql://127.0.0.1:15432/keycloak",
        "KC_DB_USERNAME": "keycloak", "KC_DB_PASSWORD": values["KEYCLOAK_DB_PASSWORD"],
        "KC_HTTP_ENABLED": "true", "KC_HTTP_HOST": "127.0.0.1", "KC_HTTP_PORT": "18080",
        "KC_HTTP_RELATIVE_PATH": "/identity", "KC_HOSTNAME": values["LZO_PUBLIC_ORIGIN"] + "/identity",
        "KC_PROXY_HEADERS": "xforwarded", "KC_HEALTH_ENABLED": "true",
        "KC_HTTP_MANAGEMENT_HOST": "127.0.0.1", "KC_HTTP_MANAGEMENT_PORT": "19000",
        "KC_BOOTSTRAP_ADMIN_USERNAME": "bootstrap-admin"})
    import_dir = kc / "data/import"
    import_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "infra/keycloak/realm-template.json", import_dir / "lzo-realm.json")
    state = []
    try:
        state.append(launch("fuseki", [java / "bin/java.exe", "-Xmx1g", "-jar", fuseki / "fuseki-server.jar",
            "--config=" + str(fuseki_base / "config.ttl"), "--localhost", "--port=13030"], directory, env, fuseki_base))
        state.append(launch("keycloak", ["cmd.exe", "/d", "/c", kc / "bin/kc.bat", "start", "--import-realm"], directory, env, kc))
        service_env = runtime_environment(env)
        state.append(launch("api", [sys.executable, "-m", "uvicorn", "lzo.main:create_app", "--factory",
            "--host", "127.0.0.1", "--port", "18000", "--no-access-log"], directory, service_env))
        state.append(launch("worker", [sys.executable, "-m", "lzo.worker"], directory, service_env))
        state.append(launch("proxy", [sys.executable, ROOT / "scripts/native_proxy.py", RUNTIME / "certs"], directory, env))
    finally:
        (directory / "processes.json").write_text(json.dumps(state, indent=2))
    wait_http("http://127.0.0.1:13030/lzo/query?query=ASK%7B%7D")
    wait_http(values["LZO_OIDC_DISCOVERY_URL"])
    wait_http("http://127.0.0.1:18000/health/ready")
    wait_http(values["LZO_PUBLIC_ORIGIN"] + "/health/ready", verify=str(RUNTIME / "certs/localhost.crt"))
    print(f"Native supplemental services ready for {project}; HTTPS loopback :18443 (not Compose acceptance).")


def stop(project):
    import psutil
    directory = project_path(project)
    state = directory / "processes.json"
    if state.exists():
        for item in reversed(json.loads(state.read_text())):
            try:
                parent = psutil.Process(item["pid"])
                if abs(parent.create_time() - item["created"]) > 0.01:
                    raise RuntimeError("Refusing to stop reused process ID")
                children = parent.children(recursive=True)
                for child in reversed(children):
                    child.terminate()
                parent.terminate()
                _, alive = psutil.wait_procs([*children, parent], timeout=10)
                for process in alive:
                    process.kill()
            except psutil.NoSuchProcess:
                pass
        state.unlink()
    pg, _, _, _ = tool_paths()
    data = directory / "postgres"
    if (data / "postmaster.pid").exists():
        run([pg / "pg_ctl.exe", "-D", data, "-w", "-m", "fast", "stop"])
    print(f"Stopped project {project}; all persisted data retained.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "start", "stop", "restart", "test"])
    parser.add_argument("--project", default="lzo-test-bootstrap")
    args = parser.parse_args()
    project_path(args.project)
    if args.action == "prepare":
        prepare_tools()
        return
    if args.action in ("stop", "restart"):
        stop(args.project)
    if args.action in ("start", "restart"):
        start(args.project)
    if args.action == "test":
        values = environment(args.project)
        command = native_pytest_command(args.project)
        child_env = os.environ | values | {"PYTHONPATH": str(ROOT / "backend/src")}
        result = subprocess.run(command, cwd=ROOT / "backend",
            env=child_env,
            creationflags=FLAGS, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        log_path = Path(command[-1]).with_suffix(".log")
        print(report_pytest_result(result.returncode, result.stdout or "", log_path, child_env), flush=True)
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
