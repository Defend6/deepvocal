import asyncio
from uuid import UUID

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.auth import authenticate
from app.config import Settings
from app.main import app
from app.schemas import UploadCreateRequest
from app.storage.s3 import S3Storage, S3StorageConfig
from app.uploads import UploadService


def make_request(headers: dict[str, str]) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(key.lower().encode(), value.encode()) for key, value in headers.items()],
        }
    )


def test_local_identity_requires_explicit_header() -> None:
    principal = authenticate(make_request({"X-Debug-Identity": "issuer-a|subject-a"}), Settings())
    assert (principal.issuer, principal.subject) == ("issuer-a", "subject-a")
    for headers in (
        {},
        {"Authorization": "Bearer fake"},
        {"X-Debug-Identity": "missing-separator"},
        {"X-Debug-Identity": "issuer|   "},
    ):
        with pytest.raises(HTTPException) as error:
            authenticate(make_request(headers), Settings())
        assert error.value.status_code == 401


def test_only_local_test_configuration_is_supported() -> None:
    with pytest.raises(ValueError, match="only local test identity"):
        Settings(auth_mode="oidc")
    with pytest.raises(ValueError, match="only allowed"):
        Settings(deepvocal_mode="production")
    settings = Settings(oidc_issuer="https://reserved.example.test")
    assert settings.oidc_issuer == "https://reserved.example.test"


def test_presign_api_requires_identity_and_has_no_completion_endpoint() -> None:
    client = TestClient(app)
    assert client.get("/v1/me").status_code == 401
    assert client.get("/v1/operations/diagnostics").status_code == 401
    assert client.post("/v1/uploads", json={}).status_code == 401
    assert client.post("/v1/uploads/asset/complete").status_code == 404
    assert "/v1/uploads/{asset_id}/complete" not in app.openapi()["paths"]


def test_upload_request_rejects_client_supplied_owner() -> None:
    with pytest.raises(ValueError):
        UploadCreateRequest(
            filename="voice.wav",
            content_type="audio/wav",
            size_bytes=10,
            sha256="a" * 64,
            purpose="assessment",
            owner_id="untrusted-owner",
        )


class FakeSession:
    def __init__(self) -> None:
        self.committed = False

    async def execute(self, statement, parameters) -> None:
        self.statement = str(statement)
        self.parameters = parameters

    async def commit(self) -> None:
        self.committed = True


class FakeS3Client:
    def generate_presigned_url(self, method: str, *, Params: dict, ExpiresIn: int) -> str:
        self.params = Params
        return "https://storage.test/upload"


def test_presign_records_owner_scoped_pending_asset_without_object_inspection() -> None:
    session = FakeSession()
    s3 = FakeS3Client()
    storage = S3Storage(S3StorageConfig("https://storage.test", "audio", "key", "secret"), s3)
    service = UploadService(session, storage, Settings())
    request = UploadCreateRequest(
        filename="../ignored-name.wav",
        content_type="audio/wav",
        size_bytes=10,
        sha256="a" * 64,
        purpose="assessment",
    )
    result = asyncio.run(service.create("trusted-owner", request))
    UUID(result["asset_id"])
    assert result["object_key"] == f"users/trusted-owner/assets/{result['asset_id']}"
    assert session.parameters["owner_id"] == "trusted-owner"
    assert "'pending'" in session.statement
    assert "ready" not in session.statement
    assert session.committed
    assert "ChecksumSHA256" not in s3.params
    assert result["required_headers"] == {
        "Content-Type": "audio/wav",
        "x-amz-server-side-encryption": "AES256",
    }
