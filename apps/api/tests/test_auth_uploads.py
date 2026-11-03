import asyncio
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from starlette.requests import Request

from app import auth
from app.auth import Principal, authenticate
from app.config import Settings
from app.schemas import UploadCreateRequest
from app.storage.s3 import S3Storage, S3StorageConfig
from app.uploads import UploadError, UploadService


def make_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
            "query_string": b"",
            "server": ("test", 80),
            "client": ("test", 1234),
            "scheme": "http",
        }
    )


def test_local_auth_requires_explicit_debug_identity() -> None:
    request = make_request({"X-Debug-Identity": "issuer-a|subject-a"})
    principal = asyncio.run(authenticate(request, Settings(auth_mode="local")))
    assert principal == Principal("issuer-a", "subject-a", {"iss": "issuer-a", "sub": "subject-a"})

    with pytest.raises(HTTPException) as error:
        asyncio.run(authenticate(make_request({}), Settings(auth_mode="local")))
    assert error.value.status_code == 401


def test_production_configuration_rejects_local_auth() -> None:
    with pytest.raises(ValueError, match="production requires AUTH_MODE=oidc"):
        Settings(deepvocal_mode="production", auth_mode="local")


def test_oidc_validates_signature_issuer_audience_and_expiry(monkeypatch) -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    public_jwk["kid"] = "test-key"

    async def fake_jwks(_settings: Settings) -> dict:
        return {"keys": [public_jwk]}

    monkeypatch.setattr(auth, "_jwks", fake_jwks)
    settings = Settings(
        auth_mode="oidc",
        oidc_issuer="https://identity.example.test/",
        oidc_audience="deepvocal-api",
        oidc_jwks_url="https://identity.example.test/jwks",
    )
    now = datetime.now(UTC)

    def token(issuer: str, audience: str, expires: datetime) -> str:
        return jwt.encode(
            {
                "iss": issuer,
                "sub": "user-123",
                "aud": audience,
                "iat": now,
                "exp": expires,
            },
            private_key,
            algorithm="RS256",
            headers={"kid": "test-key"},
        )

    valid = token(settings.oidc_issuer, settings.oidc_audience, now + timedelta(minutes=5))
    principal = asyncio.run(
        authenticate(make_request({"Authorization": f"Bearer {valid}"}), settings)
    )
    assert principal.issuer == settings.oidc_issuer
    assert principal.subject == "user-123"

    for invalid in (
        token("https://wrong.example.test/", settings.oidc_audience, now + timedelta(minutes=5)),
        token(settings.oidc_issuer, "wrong-audience", now + timedelta(minutes=5)),
        token(settings.oidc_issuer, settings.oidc_audience, now - timedelta(minutes=5)),
    ):
        with pytest.raises(HTTPException) as error:
            asyncio.run(
                authenticate(make_request({"Authorization": f"Bearer {invalid}"}), settings)
            )
        assert error.value.status_code == 401


class FakeS3Client:
    def generate_presigned_url(self, method: str, *, Params: dict, ExpiresIn: int) -> str:
        self.params = Params
        return "https://storage.test/upload"


def test_presigned_upload_binds_sha256_checksum() -> None:
    client = FakeS3Client()
    storage = S3Storage(S3StorageConfig("https://storage.test", "audio", "key", "secret"), client)
    storage.create_upload_url("users/u1/assets/a1", "audio/wav", "a" * 64)
    assert client.params["ChecksumSHA256"] == storage.expected_checksum_b64("a" * 64)
    assert client.params["Metadata"]["sha256"] == "a" * 64


def test_upload_request_rejects_client_supplied_owner() -> None:
    with pytest.raises(ValueError):
        UploadCreateRequest(
            filename="voice.wav",
            content_type="audio/wav",
            size_bytes=10,
            sha256="a" * 64,
            purpose="assessment",
            owner_id="attacker-selected-owner",
        )


def test_upload_completion_rejects_mismatched_object_metadata() -> None:
    storage = S3Storage(
        S3StorageConfig("https://storage.test", "audio", "key", "secret"), FakeS3Client()
    )
    service = UploadService(None, storage, Settings())  # type: ignore[arg-type]
    asset = {"sha256": "a" * 64, "content_type": "audio/wav", "size_bytes": 128}
    valid_head = {
        "ContentLength": 128,
        "ContentType": "audio/wav",
        "ChecksumSHA256": storage.expected_checksum_b64("a" * 64),
        "Metadata": {"sha256": "a" * 64},
    }
    service._validate_head(asset, valid_head)

    for field, value in (
        ("ContentLength", 127),
        ("ContentType", "audio/mpeg"),
        ("ChecksumSHA256", "bad"),
    ):
        with pytest.raises(UploadError, match="does not match"):
            service._validate_head(asset, {**valid_head, field: value})
