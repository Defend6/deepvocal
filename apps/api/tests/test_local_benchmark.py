import asyncio

import pytest
from test_knowledge import prepared

from app.knowledge.local_benchmark import metric_summary, run_benchmark


def test_metric_summary_uses_binary_relevance_and_cutoff():
    report = metric_summary(
        [["b", "a", "c"], ["x", "b"]],
        [["a", "b"], ["b"]],
        cutoff=2,
    )
    assert report["mrr@2"] == 0.75
    assert report["recall@2"] == 1.0
    assert report["hit_rate@2"] == 1.0
    assert 0.81 < report["ndcg@2"] < 0.82


def test_mrr_ignores_hits_beyond_cutoff_and_handles_no_hits():
    report = metric_summary([["x", "y", "a"], []], [["a"], ["b"]], cutoff=2)
    assert report == {"mrr@2": 0.0, "recall@2": 0.0, "ndcg@2": 0.0, "hit_rate@2": 0.0}


@pytest.mark.parametrize(
    ("rankings", "relevant"),
    [([["a", "a"]], [["a"]]), ([["a"]], [[]]), ([["a"]], [["a", "a"]])],
)
def test_invalid_labels_and_duplicate_rankings_cannot_inflate_metrics(rankings, relevant):
    with pytest.raises(ValueError):
        metric_summary(rankings, relevant)


def test_stale_corpus_or_source_labels_fail_before_model_requests(tmp_path, monkeypatch):
    settings, bundle = prepared(tmp_path)
    settings = settings.model_copy(
        update={
            "embedding_api_base": "http://ollama/v1",
            "reranker_api_base": "http://tei",
        }
    )

    async def forbidden(*args, **kwargs):
        raise AssertionError("stale relevance labels triggered a model request")

    monkeypatch.setattr("app.knowledge.provider.ModelProvider.post", forbidden)
    case = {"id": "stale", "query": "pitch reference", "relevant_chunk_ids": ["pitch"]}
    with pytest.raises(ValueError, match="fingerprint"):
        asyncio.run(run_benchmark(settings, {"corpus_fingerprint": "changed", "cases": [case]}))
    case["source_text_sha256"] = {"pitch": "changed-text"}
    with pytest.raises(ValueError, match="relevance labels"):
        asyncio.run(
            run_benchmark(settings, {"corpus_fingerprint": bundle["fingerprint"], "cases": [case]})
        )
