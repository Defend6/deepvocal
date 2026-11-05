"""Provision one disposable PKCE test identity; save credentials only in ignored QA files."""

import json
import secrets
from pathlib import Path

import httpx
from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
config = dotenv_values(root / ".env")
base = "http://127.0.0.1:8180"
with httpx.Client(timeout=20, trust_env=False) as client:
    token = client.post(base + "/realms/master/protocol/openid-connect/token", data={
        "client_id": "admin-cli", "grant_type": "password", "username": config.get("IDENTITY_ADMIN_USER", "admin"),
        "password": config["IDENTITY_ADMIN_PASSWORD"]})
    token.raise_for_status()
    client.headers["Authorization"] = "Bearer " + token.json()["access_token"]
    clients = client.get(base + "/admin/realms/deepvocal/clients", params={"clientId":"deepvocal-web"})
    clients.raise_for_status()
    web_client = clients.json()[0]
    for field, value in (("redirectUris", "http://127.0.0.1:5174/auth/callback"),
                         ("webOrigins", "http://127.0.0.1:5174")):
        if value not in web_client[field]: web_client[field].append(value)
    client.put(base + "/admin/realms/deepvocal/clients/" + web_client["id"], json=web_client).raise_for_status()
    username = "qa-" + secrets.token_hex(6)
    password = secrets.token_urlsafe(32)
    response = client.post(base + "/admin/realms/deepvocal/users", json={"username": username,
        "enabled": True, "emailVerified": True, "firstName": "QA", "lastName": "Local",
        "email": username + "@example.test", "credentials": [{"type": "password", "value": password, "temporary": False}]})
    response.raise_for_status()
    identifier = response.headers["Location"].rsplit("/", 1)[-1]
    (root / "_qa/identity-e2e-private.json").write_text(json.dumps({"username": username, "password": password,
        "id": identifier, "issuer": base + "/realms/deepvocal"}), encoding="utf-8")
print("Disposable identity prepared; private credentials saved locally.")
