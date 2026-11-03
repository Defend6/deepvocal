import pytest
from fastapi.testclient import TestClient

from app.agents.router import RuleRouter
from app.main import app
from app.storage.s3 import S3Storage, S3StorageConfig

client = TestClient(app)


def test_audio_intent_is_routed_to_assessment() -> None:
    decision = RuleRouter().route("请分析这段录音有没有跑调")
    assert decision.agent == "assessment"
    assert "audio.inspect" in decision.tool_allowlist


def test_generation_is_confirmation_gated() -> None:
    decision = RuleRouter().route("帮我生成个性化歌声")
    assert decision.agent == "singing"
    assert decision.requires_confirmation is True


def test_unknown_intent_clarifies() -> None:
    assert RuleRouter().route("你好").agent == "clarify"


def test_router_endpoint_returns_structured_decision() -> None:
    response = client.post("/v1/router/decide", json={"text": "请分析这段录音有没有跑调"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"] == "assessment"
    assert "audio.inspect" in payload["tool_allowlist"]


def test_health_endpoint_reports_mock_capabilities() -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["singing_enabled"] is False


def test_corpus_status_exposes_review_gate() -> None:
    response = client.get("/v1/knowledge/corpus/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["corpus_version"] == "seed-20261103-v1"
    assert payload["document_count"] == 35
    assert payload["chunk_count"] == 166
    assert payload["production_enabled"] is False
    assert "expert review is incomplete" in payload["production_blockers"]
    assert payload["review_status"] == "NEEDS_REVIEW"


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def generate_presigned_url(self, method: str, *, Params: dict, ExpiresIn: int) -> str:
        self.calls.append((method, {"Params": Params, "ExpiresIn": ExpiresIn}))
        return "http://rustfs.local/presigned"


def test_s3_adapter_scopes_upload_to_audio_and_bucket() -> None:
    client = FakeS3Client()
    storage = S3Storage(
        S3StorageConfig("http://rustfs.local", "deepvocal-private", "key", "secret"), client
    )
    url = storage.create_upload_url("users/u1/assets/a1.wav", "audio/wav")
    assert url.endswith("presigned")
    assert client.calls[0][0] == "put_object"
    assert client.calls[0][1]["Params"]["Bucket"] == "deepvocal-private"
    assert client.calls[0][1]["Params"]["ServerSideEncryption"] == "AES256"


def test_s3_adapter_rejects_unsafe_or_non_audio_keys() -> None:
    storage = S3Storage(
        S3StorageConfig("http://rustfs.local", "deepvocal-private", "key", "secret"),
        FakeS3Client(),
    )
    with pytest.raises(ValueError):
        storage.create_upload_url("../escape.wav", "audio/wav")
    with pytest.raises(ValueError):
        storage.create_upload_url("users/u1/assets/a1.txt", "text/plain")
