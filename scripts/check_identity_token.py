"""Verify a real issued access token without printing it or personal claims."""
import json
from pathlib import Path
from starlette.requests import Request

from app.auth import authenticate
from app.config import Settings

root = Path(__file__).resolve().parents[1]
tokens = json.loads((root / "_qa/identity-access-private.json").read_text())
settings = Settings(auth_mode="oidc", oidc_issuer="http://127.0.0.1:8180/realms/deepvocal",
    oidc_audience="deepvocal-api", oidc_jwks_url="http://127.0.0.1:8180/realms/deepvocal/protocol/openid-connect/certs")
request = Request({"type":"http", "method":"GET", "path":"/v1/me",
    "headers":[(b"authorization", ("Bearer " + tokens["access_token"]).encode())]})
principal = authenticate(request, settings)
assert principal.subject == tokens["sub"]
print("Real Keycloak access token verified: signature, issuer, audience, expiry and subject.")
