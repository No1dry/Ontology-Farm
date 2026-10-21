"""Operator-only Keycloak setup through supported admin APIs; never run in the API."""
import argparse
import json
from pathlib import Path
import secrets
import ssl

import httpx
from dotenv import dotenv_values
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parents[1]


def configure(values, smtp=None):
    origin = values["LZO_PUBLIC_ORIGIN"]
    base = origin + "/identity"
    verify = ssl.create_default_context(cafile=values["LZO_CA_BUNDLE"]) if values.get("LZO_CA_BUNDLE") else True
    with httpx.Client(verify=verify, timeout=20) as client:
        response = client.post(base + "/realms/master/protocol/openid-connect/token", data={
            "grant_type": "password", "client_id": "admin-cli", "username": "bootstrap-admin",
            "password": values["KC_BOOTSTRAP_ADMIN_PASSWORD"]})
        response.raise_for_status()
        client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
        admin = base + "/admin/realms/lzo"

        def request(method, path, **kwargs):
            result = client.request(method, admin + path, **kwargs)
            result.raise_for_status()
            return result

        template = json.loads((ROOT / "infra/keycloak/realm-template.json").read_text())
        serialized = json.dumps(template)
        for key, value in values.items():
            if value is not None:
                serialized = serialized.replace("${" + key + "}", value)
        template = json.loads(serialized)
        for representation in template["clients"]:
            found = request("GET", "/clients", params={"clientId": representation["clientId"]}).json()
            if found:
                request("PUT", "/clients/" + found[0]["id"], json=representation)
            else:
                request("POST", "/clients", json=representation)
        service = request("GET", "/clients", params={"clientId": "lzo-identity-admin"}).json()[0]
        account = request("GET", "/clients/" + service["id"] + "/service-account-user").json()
        management = request("GET", "/clients", params={"clientId": "realm-management"}).json()[0]
        permission_roles = [request("GET", f"/clients/{management['id']}/roles/{name}").json()
                            for name in ("manage-users", "query-users")]
        request("POST", f"/users/{account['id']}/role-mappings/clients/{management['id']}", json=permission_roles)
        if smtp is not None:
            request("PUT", "", json={"smtpServer": smtp})
        values["LZO_IDENTITY_ADMIN_URL"] = admin
    return values


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env", type=Path, required=True)
    parser.add_argument("--smtp-json", type=Path)
    args = parser.parse_args()
    values = dict(dotenv_values(args.env))
    values.setdefault("LZO_SESSION_ENCRYPTION_KEY", Fernet.generate_key().decode())
    values.setdefault("LZO_IDENTITY_ADMIN_CLIENT_SECRET", secrets.token_urlsafe(32))
    smtp = json.loads(args.smtp_json.read_text(encoding="utf-8-sig")) if args.smtp_json else None
    values = configure(values, smtp)
    if smtp:
        values["LZO_INVITATION_SMTP_CONFIGURED"] = "true"
    args.env.write_text("\n".join(f"{key}={value}" for key, value in values.items()) + "\n")
    args.env.chmod(0o600)
    if values.get("LZO_SERVICE_MODE") == "native":
        project = values["LZO_TEST_PROJECT"]
        from native_dev import project_path
        (project_path(project) / "services.env").write_text(args.env.read_text())
    print("Configured OIDC client, restricted identity service account and session settings (values hidden).")


if __name__ == "__main__":
    main()
