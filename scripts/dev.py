"""Compose lifecycle and local secret/certificate preparation; never removes volumes."""
import argparse
from datetime import datetime, timedelta, timezone
import ipaddress
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def certificate(directory, *, source=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    directory.mkdir(parents=True, exist_ok=True)
    cert, key = directory / "localhost.crt", directory / "localhost.key"
    if cert.exists() and key.exists():
        return
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LZO local development")])
    now = datetime.now(timezone.utc)
    public = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(private.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=30))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                   x509.DNSName("source-postgres"),x509.DNSName("source-mysql")] if source else
                   [x509.DNSName("localhost"),x509.DNSName("proxy"),x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
              .sign(private, hashes.SHA256()))
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    key.chmod(0o600)
    cert.write_bytes(public.public_bytes(serialization.Encoding.PEM))


def prepare_compose():
    directory = ROOT / "infra/compose"
    certificate(directory / "certs")
    certificate(directory / "source-certs",source=True)
    certificate(directory / "wrong-source-ca",source=True)
    env = directory / ".env"
    if not env.exists():
        source = (directory / ".env.example").read_text()
        lines = [line.split("=", 1)[0] + "=" + secrets.token_urlsafe(32)
                 if "=REPLACE_WITH_GENERATED_SECRET" in line else line for line in source.splitlines()]
        from cryptography.fernet import Fernet
        lines = [line.replace("REPLACE_WITH_FERNET_KEY", Fernet.generate_key().decode()) for line in lines]
        env.write_text("\n".join(lines) + "\n")
        env.chmod(0o600)
    secret_dir = directory / 'secrets'
    secret_dir.mkdir(exist_ok=True)
    secret_dir.chmod(0o700)
    for name in ('s3_access_key', 's3_secret_key', 's3_admin_access_key', 's3_admin_secret_key'):
        path = secret_dir / name
        if not path.exists():
            path.write_text(secrets.token_urlsafe(32))
        # Local Compose secrets are bind-mounted files. Keep host traversal
        # private while allowing the unprivileged container UID to read its mount.
        path.chmod(0o444)
    print("Prepared local HTTPS certificate and isolated Compose secrets (values hidden).")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "check", "up", "test", "e2e", "down", "status"])
    args = parser.parse_args()
    if args.action == "prepare":
        prepare_compose()
        return
    if args.action == "check":
        subprocess.run([sys.executable, str(ROOT / "scripts/check_environment.py")], check=True)
        return
    env = os.environ.copy()
    if args.action == "test":
        project = env.get("LZO_TEST_PROJECT", "lzo-test-bootstrap")
        if not project.startswith("lzo-test-"):
            raise SystemExit("Tests require a dedicated lzo-test-* project")
        env["COMPOSE_PROJECT_NAME"] = project
    base = ["docker", "compose", "--env-file", str(ROOT / "infra/compose/.env"), "-f", str(ROOT / "infra/compose/compose.yaml")]
    if args.action == "test":
        negative_sources=["source-postgres-no-tls", "source-mysql-no-tls"]
        try:
            subprocess.run(base + ["--profile", "source-postgres", "--profile", "source-mysql", "--profile", "source-no-tls-test",
                                  "up", "-d", "--wait", "postgres", "fuseki", "keycloak", "api",
                                  "source-postgres", "source-mysql", *negative_sources], env=env, check=True)
            subprocess.run(base + ["--profile", "test", "--profile", "source-no-tls-test", "run", "--rm",
                                  "api-test", "pytest", "tests", "-q"], env=env, check=True)
        finally:
            # Only the explicitly named negative fixtures in this isolated test
            # project are stopped/removed; they use disposable tmpfs data.
            subprocess.run(base + ["--profile", "source-no-tls-test", "stop", *negative_sources], env=env, check=True)
            subprocess.run(base + ["--profile", "source-no-tls-test", "rm", "-f", *negative_sources], env=env, check=True)
    elif args.action == "e2e":
        subprocess.run(base + ["--profile", "e2e-test", "run", "--rm", "e2e-test"], env=env, check=True)
    else:
        commands = {"up": ["up", "-d", "--build", "--wait"], "down": ["down"], "status": ["ps"]}
        subprocess.run(base + commands[args.action], env=env, check=True)


if __name__ == "__main__":
    main()
