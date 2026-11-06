import asyncio
import copy

import pytest

from app.knowledge.benchmark_support import (
    BenchmarkCache,
    benchmark_cache_lock,
    evidence_summary,
    validate_frozen_lock,
    validate_suite,
)
from app.knowledge.governance import sha256


def suite_and_bundle():
    text = "An invented support statement for test purposes."
    row = {"id": "one", "text": text, "text_sha256": sha256(text),
           "source_locator": "synthetic/document.md"}
    case = {"id": "first", "query": "first question", "relevant_chunk_ids": ["one"],
            "source_text_sha256": {"one": sha256(text)}, "split": "development",
            "evidence_spans": {"one": [{"start": 0, "end": len(text), "text": text}]}}
    return ({"cases": [case], "require_complete_provenance": True,
             "enforce_document_split": True}, {"chunks": [row]})


def test_grouped_document_split_hashes_and_evidence_fail_closed():
    suite, bundle = suite_and_bundle()
    assert len(validate_suite(suite, bundle)[0]) == 1
    copy_case = copy.deepcopy(suite["cases"][0])
    copy_case.update(id="second", query="different wording", split="frozen-test")
    suite["cases"].append(copy_case)
    with pytest.raises(ValueError, match="leaks"):
        validate_suite(suite, bundle)
    suite["cases"].pop()
    suite["cases"][0]["evidence_spans"]["one"][0]["text"] = "modified source"
    with pytest.raises(ValueError, match="span"):
        validate_suite(suite, bundle)


def test_controls_are_not_silently_counted_as_passed_retrieval_cases():
    suite, bundle = suite_and_bundle()
    suite["cases"].append({"id": "negative", "query": "unanswerable question",
                           "answerable": False, "relevant_chunk_ids": [],
                           "expected_behavior": "ask for evidence"})
    supported, controls = validate_suite(suite, bundle)
    assert len(supported) == len(controls) == 1
    suite["cases"][-1]["relevant_chunk_ids"] = ["one"]
    with pytest.raises(ValueError, match="positive relevance"):
        validate_suite(suite, bundle)


def test_partial_evidence_hit_is_distinct_from_complete_sufficient_evidence():
    cases = [{"relevant_chunk_ids": ["one", "two"], "sufficient_evidence_sets": [["one", "two"]]}]
    report = evidence_summary([["one", "noise"]], cases)
    assert report["precision@5"] == .2
    assert report["sufficient_evidence_rate@5"] == 0
    cases[0]["sufficient_evidence_sets"] = [["one"], ["two"]]
    assert evidence_summary([["two"]], cases)["sufficient_evidence_rate@5"] == 1


def test_rerank_pair_cache_reuses_overlap_and_invalidates_model_revision(tmp_path):
    calls = []

    async def invoke(texts):
        calls.append(texts)
        return [(i, len(text) / 10) for i, text in enumerate(texts)]

    path = tmp_path / "private-cache.json"
    cache = BenchmarkCache(path, {"model": "synthetic", "revision": "first"})
    assert asyncio.run(cache.rerank("query", ["a", "bbb"], invoke)) == [(1, .3), (0, .1)]
    cache = BenchmarkCache(path, {"model": "synthetic", "revision": "first"})
    assert asyncio.run(cache.rerank("query", ["bbb", "cc"], invoke)) == [(0, .3), (1, .2)]
    assert calls == [["a", "bbb"], ["cc"]]
    changed = BenchmarkCache(path, {"model": "synthetic", "revision": "different"})
    asyncio.run(changed.rerank("query", ["a"], invoke))
    assert calls[-1] == ["a"]


def test_duplicate_queries_cannot_pad_case_count():
    suite, bundle = suite_and_bundle()
    duplicate = copy.deepcopy(suite["cases"][0])
    duplicate["id"] = "new-id-same-query"
    suite["cases"].append(duplicate)
    with pytest.raises(ValueError, match="duplicate query"):
        validate_suite(suite, bundle)


def test_frozen_test_lock_detects_changes_including_label_edits():
    text = '{"cases": []}'
    validate_frozen_lock(text, {"sha256": sha256(text)})
    with pytest.raises(ValueError, match="frozen"):
        validate_frozen_lock(text + " ", {"sha256": sha256(text)})


def test_parallel_cache_writers_fail_and_normal_errors_release_the_lock(tmp_path):
    path = tmp_path / "cache.json"
    with pytest.raises(RuntimeError, match="synthetic"), benchmark_cache_lock(path):
        with pytest.raises(ValueError, match="cache is busy"), benchmark_cache_lock(path):
            raise AssertionError("concurrent cache writer entered")
        raise RuntimeError("synthetic evaluation failure")
    with benchmark_cache_lock(path):
        assert path.with_suffix(".json.lock").is_file()
    assert not path.with_suffix(".json.lock").exists()
