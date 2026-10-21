"""Synthetic Keycloak users for browser tests; passwords never traverse the API."""
import os
from pathlib import Path
import ssl
import sys

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "backend/src"))
from bootstrap_authorization import bootstrap
from lzo.config import Settings

values = dict(dotenv_values(ROOT / ".runtime/native.env"))
os.environ.update(values)
base = values["LZO_PUBLIC_ORIGIN"] + "/identity"
with httpx.Client(verify=ssl.create_default_context(cafile=values["LZO_CA_BUNDLE"]), timeout=20) as client:
    response = client.post(base + "/realms/master/protocol/openid-connect/token", data={
        "grant_type": "password", "client_id": "admin-cli", "username": "bootstrap-admin",
        "password": values["KC_BOOTSTRAP_ADMIN_PASSWORD"]})
    response.raise_for_status()
    client.headers["Authorization"] = "Bearer " + response.json()["access_token"]
    admin = base + "/admin/realms/lzo"
    for username in ("f03-synthetic", "f03-admin"):
        response = client.get(admin + "/users", params={"username": username, "exact": "true"})
        response.raise_for_status()
        matches = response.json()
        if matches:
            subject = matches[0]["id"]
        else:
            response = client.post(admin + "/users", json={
                "username": username, "email": username + "@example.invalid", "emailVerified": True,
                "firstName": "Synthetic", "lastName": "F03", "enabled": True})
            response.raise_for_status()
            subject = response.headers["Location"].rsplit("/", 1)[-1]
        response = client.put(admin + "/users/" + subject + "/reset-password",
            json={"type": "password", "temporary": False, "value": "Synthetic-F03-only!"})
        response.raise_for_status()
        if username == "f03-admin":
            bootstrap(Settings(), values["LZO_OIDC_ISSUER"], subject)
print("Synthetic browser accounts provisioned; platform never received their passwords.")
