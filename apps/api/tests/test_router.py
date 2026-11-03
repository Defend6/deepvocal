from app.agents.router import RuleRouter


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
