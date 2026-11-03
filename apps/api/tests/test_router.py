from app.agents.router import RuleRouter
from fastapi.testclient import TestClient
from app.main import app


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
