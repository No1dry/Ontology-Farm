"""Owned native PostgreSQL/PostGIS cluster, isolated from existing project services.

Tool cache is read-only. Generated passwords and data stay in ignored .runtime.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / ".runtime" / "multistore"
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
DOMAINS = ("core", "gis", "timeseries", "catalog")


def owned_state(root, path):
    """New acceptance processes can only adopt a marked worktree runtime."""
    root, path = Path(root).resolve(), Path(path).resolve()
    if not path.is_relative_to(root / '.runtime') or path == root / '.runtime':
        raise ValueError('Runtime must be a named child of this worktree .runtime')
    marker = path / 'ownership.json'
    if path.exists():
        if not marker.exists() or json.loads(marker.read_text())['worktree'] != str(root):
            raise ValueError('Refusing an unmarked or foreign runtime')
    else:
        path.mkdir(parents=True)
        marker.write_text(json.dumps({'worktree': str(root)}))
    return path

def run(command, **kwargs):
    return subprocess.run([str(x) for x in command], check=True, creationflags=FLAGS, **kwargs)

def owned():
    marker = RUNTIME / "ownership.json"
    if not marker.exists(): raise RuntimeError("No cluster ownership marker")
    values = json.loads(marker.read_text())
    if values["worktree"] != str(ROOT.resolve()) or Path(values["data"]).resolve() != (RUNTIME / "data").resolve():
        raise RuntimeError("Cluster ownership mismatch")
    pid = RUNTIME / "data/postmaster.pid"
    if pid.exists() and Path(pid.read_text().splitlines()[1]).resolve() != (RUNTIME / "data").resolve():
        raise RuntimeError("Running server data path ownership mismatch")
    return values

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("init", "start", "restart", "stop", "status", "provision-telemetry-maintenance"))
    parser.add_argument("--tool-cache", type=Path)
    parser.add_argument("--port", type=int, default=25432)
    args = parser.parse_args()
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if args.action == "init":
        if (RUNTIME / "ownership.json").exists(): raise RuntimeError("Cluster already provisioned; use start")
        if args.tool_cache is None: raise RuntimeError("Explicit read-only --tool-cache required")
        pg = (args.tool_cache / "postgres/pgsql/bin").resolve()
        if not (pg / "initdb.exe").exists(): raise RuntimeError("PostgreSQL native binaries absent")
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", args.port))
        data = RUNTIME / "data"
        password = secrets.token_urlsafe(32)
        pwfile = RUNTIME / "init-password"
        pwfile.write_text(password)
        try:
            run([pg / "initdb.exe", "-D", data, "-U", "postgres", "--pwfile", pwfile, "--auth-host=scram-sha-256", "--auth-local=scram-sha-256", "--encoding=UTF8", "--locale=C"], stdout=subprocess.DEVNULL)
        finally: pwfile.unlink(missing_ok=True)
        with (data / "postgresql.conf").open("a") as output:
            output.write(f"\nlisten_addresses='127.0.0.1'\nport={args.port}\n")
        values = dict(worktree=str(ROOT.resolve()),data=str(data.resolve()),bin=str(pg),port=args.port)
        (RUNTIME / "ownership.json").write_text(json.dumps(values))
        run([pg / "pg_ctl.exe", "-D", data, "-l", RUNTIME / "postgres.log", "-w", "start"], stdout=subprocess.DEVNULL)
        import psycopg
        from psycopg import sql
        env = {"LZO_STORAGE_MODE":"split", "LZO_FUSEKI_URL":"http://127.0.0.1:23030/lzo", "LZO_OIDC_ISSUER":"synthetic-test", "LZO_OIDC_DISCOVERY_URL":"http://127.0.0.1:28080", "LZO_FILE_ROOT":str(RUNTIME / "files")}
        # Administrator secret is runtime-only, never printed.
        (RUNTIME / "admin.json").write_text(json.dumps({"password": password}))
        admin = dict(host="127.0.0.1",port=args.port,user="postgres",password=password)
        with psycopg.connect(**admin,dbname="postgres",autocommit=True) as c:
            for domain in DOMAINS:
                name = "lzo" if domain == "core" else f"lzo_{domain}"
                owner, app = name+"_owner", name+"_app"
                for role, suffix in ((owner,"MIGRATION_DATABASE_URL"),(app,"DATABASE_URL")):
                    secret = secrets.token_urlsafe(32)
                    c.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}").format(sql.Identifier(role),sql.Literal(secret)))
                    key = "LZO_" + ("" if domain == "core" else domain.upper()+"_") + suffix
                    env[key] = f"postgresql+psycopg://{role}:{secret}@127.0.0.1:{args.port}/{name}"
                c.execute(sql.SQL("CREATE DATABASE {} OWNER {}").format(sql.Identifier(name),sql.Identifier(owner)))
                c.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name)))
                c.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {},{}").format(sql.Identifier(name),sql.Identifier(owner),sql.Identifier(app)))
                with psycopg.connect(**admin,dbname=name,autocommit=True) as domain_c:
                    if domain in ("core","gis"): domain_c.execute("CREATE EXTENSION postgis")
                    domain_c.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
                    domain_c.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(app)))
        with psycopg.connect(**admin,dbname='lzo_timeseries',autocommit=True) as maintenance_c:
            maintenance_c.execute('CREATE ROLE lzo_timeseries_maintainer NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE')
            maintenance_c.execute('GRANT CONNECT ON DATABASE lzo_timeseries TO lzo_timeseries_maintainer')
        (RUNTIME / "services.env").write_text("".join(f"{k}={v}\n" for k,v in env.items()))
        print(f"Provisioned four independent stores on loopback port {args.port}")
        return
    values = owned()
    if args.action == 'provision-telemetry-maintenance':
        import psycopg
        from psycopg import sql
        role = 'lzo_timeseries_maintainer'
        target = RUNTIME / 'maintenance.env'
        if target.exists():
            print('Owned telemetry maintenance account already provisioned')
            return
        secret = secrets.token_urlsafe(32)
        admin = json.loads((RUNTIME / 'admin.json').read_text())
        with psycopg.connect(host='127.0.0.1', port=values['port'], dbname='lzo_timeseries',
                user='postgres', password=admin['password'], autocommit=True) as connection:
            if not connection.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (role,)).fetchone():
                connection.execute(sql.SQL('CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE').format(sql.Identifier(role)))
            connection.execute(sql.SQL('ALTER ROLE {} LOGIN PASSWORD {}').format(sql.Identifier(role), sql.Literal(secret)))
            connection.execute(sql.SQL('GRANT CONNECT ON DATABASE lzo_timeseries TO {}').format(sql.Identifier(role)))
        target.write_text(f"LZO_TIMESERIES_MAINTENANCE_DATABASE_URL=postgresql+psycopg://{role}:{secret}@127.0.0.1:{values['port']}/lzo_timeseries\n")
        print('Provisioned restricted owned telemetry maintenance account; credentials saved privately')
        return
    command = [Path(values["bin"]) / "pg_ctl.exe", "-D", values["data"]]
    if args.action in ("start","restart"): command += ["-l",RUNTIME/"postgres.log","-w",args.action]
    elif args.action == "stop": command += ["-m","fast","-w","stop"]
    else: command += ["status"]
    run(command)

if __name__ == "__main__": main()
