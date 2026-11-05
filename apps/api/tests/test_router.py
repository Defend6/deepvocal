import pytest
from fastapi.testclient import TestClient

from app.agents.router import RuleRouter
from app.config import Settings
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("如何制定练习计划", "planning"),
        ("帮我制定音准练习计划，每天10分钟，共3天", "planning"),
        ("如何分析这段录音", "assessment"),
        ("音准", "knowledge"),
        ("录音中音高偏差的原理是什么", "knowledge"),
        ("什么是歌声生成", "knowledge"),
        ("请分析我的录音，并制定练习计划", "assessment"),
        ("生成歌声然后制定计划", "clarify"),
        ("请给我生成一段歌声", "singing"),
        ("帮我安排一个音准练习", "planning"),
        ("帮我看看有没有跑调", "assessment"),
    ],
)
def test_action_intents_do_not_confuse_theory_or_multiple_requests(text, expected):
    assert RuleRouter().route(text).agent == expected


def test_plan_constraints_are_extracted_or_clarified_instead_of_silently_ignored():
    from app.agents.intent_input import planning_input

    values, question = planning_input("帮我制定音准练习计划，每天十分钟，共三天")
    assert question is None
    assert (values["goal"], values["minutes"], values["days"]) == ("pitch", 10, 3)
    for request in (
        "每天60分钟的计划",
        "每天10分钟或20分钟的计划",
        "两周的计划",
        "每天10.5分钟的计划",
        "音准和节奏练习计划",
        "根据上次报告制定计划",
        "高音练习计划",
        "每天五到十分钟的计划",
        "每天-10分钟的计划",
        "每天十十分钟的计划",
        "每天15分的计划",
        "每天15min的计划",
    ):
        assert planning_input(request)[1], request
    assert planning_input("嗓子疼，帮我制定计划")[0]["discomfort"] is True
    assert planning_input("没有疼痛，帮我制定计划")[0]["discomfort"] is False


def test_router_endpoint_returns_structured_decision() -> None:
    response = client.post("/v1/router/decide", json={"text": "请分析这段录音有没有跑调"})
    assert response.status_code == 200
    payload = response.json()
    assert payload["agent"] == "assessment"
    assert "audio.inspect" in payload["tool_allowlist"]


def test_health_endpoint_reports_mock_capabilities(monkeypatch) -> None:
    from app import main

    monkeypatch.setattr(main, "settings", Settings(singing_enabled=False))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["singing_enabled"] is False


def test_corpus_status_exposes_review_gate(tmp_path, monkeypatch) -> None:
    from synthetic_corpus import create_corpus

    from app import main

    monkeypatch.setattr(
        main,
        "settings",
        main.settings.model_copy(
            update={
                "corpus_root": str(create_corpus(tmp_path / "corpus")),
            }
        ),
    )
    response = client.get("/v1/knowledge/corpus/status")
    assert response.status_code == 200
    payload = response.json()
    assert payload["corpus_version"] == "synthetic-test-only"
    assert payload["document_count"] == 3
    assert payload["chunk_count"] == 3
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
