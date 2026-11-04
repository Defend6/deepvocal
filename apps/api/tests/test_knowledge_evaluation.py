import asyncio

import pytest
from synthetic_corpus import evaluation_cases
from test_knowledge import prepared

from app.knowledge.evaluate import evaluate


def test_offline_smoke_baseline_is_traceable_and_never_calls_a_model(tmp_path, monkeypatch):
    settings, _ = prepared(tmp_path)
    cases = evaluation_cases()

    async def forbidden(*args, **kwargs):
        raise AssertionError("offline evaluation called a model")

    monkeypatch.setattr("app.knowledge.provider.ModelProvider.post", forbidden)
    report = asyncio.run(
        evaluate(settings.model_copy(update={"model_api_key": "must-not-be-used"}), cases)
    )
    assert report["model_calls"] == 0 and report["expert_benchmark"] is False
    assert len(report["corpus_fingerprint"]) == 64 and len(report["cases_sha256"]) == 64
    assert report["metrics"]["guardrail_passed"] == 3
    assert report["metrics"]["hit_at_5"] >= 0.9
    cases["retrieval"][0]["relevant"] = ["unknown-chunk"]
    with pytest.raises(ValueError):
        asyncio.run(evaluate(settings, cases))
