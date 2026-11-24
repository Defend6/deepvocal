import asyncio
import json
import os
import threading
from pathlib import Path

import pytest
from psycopg import sql
from test_knowledge import prepared

from app.config import Settings
from app.knowledge.comparison import retrieve, stratified_indices, summary_ms
from app.knowledge.comparison_report import exclude_timing_window, memory_mib, paired_recall_delta
from app.knowledge.indexes import KnowledgeIndexes
from app.knowledge.pgvector import PgVectorIndex, vector_literal


def test_parallel_and_serial_controls_share_rankings_but_not_dependencies():
    async def run():
        barrier = threading.Barrier(2, timeout=3)
        calls = []

        def vector(_vector, _limit):
            barrier.wait()
            return ["v", "shared"]

        def graph(_bundle, _query, seed_ids=None):
            calls.append(seed_ids)
            barrier.wait()
            return ["g", "shared"]

        bundle = {"chunks": [{"id": "k", "title": "pitch", "text": "pitch"}]}
        parallel = await retrieve("F", bundle, "pitch", [1, 0], vector, graph)
        assert calls == [None]  # independent graph must not wait for vector seeds
        serial_calls = []

        def serial_graph(_bundle, _query, seed_ids=None):
            serial_calls.append(seed_ids)
            return ["g", "shared"]

        serial = await retrieve("G", bundle, "pitch", [1, 0],
                                lambda *_: ["v", "shared"], serial_graph)
        assert parallel["ranking"] == serial["ranking"]
        await retrieve("E", bundle, "pitch", [1, 0],
                       lambda *_: ["v", "shared"], serial_graph)
        assert serial_calls[0] is None and serial_calls[1] is not None
        assert len(serial_calls[1]) == 2

    asyncio.run(run())


def test_vector_backend_guard_and_finite_vector_validation(tmp_path):
    settings, bundle = prepared(tmp_path)
    settings.knowledge_vector_backend = "pgvector"
    from app.knowledge.indexes import index_identity

    manifest = {"index_identity": index_identity(bundle, settings), "dimension": 2}
    with open(settings.knowledge_index_manifest, "w", encoding="utf-8") as handle:
        json.dump(manifest, handle)
    with pytest.raises(ValueError, match="backend changed"):
        KnowledgeIndexes(settings).vector_search(bundle, [1, 0])
    for vector in ([], [0, 0], [float("nan"), 1], [float("inf"), 1], ["1", 0]):
        with pytest.raises(ValueError):
            vector_literal(vector)
    assert vector_literal([1, -0.5]) == "[1.0,-0.5]"
    with pytest.raises(ValueError, match="knowledge_vector_backend"):
        Settings(knowledge_vector_backend="unknown")


def test_tail_latency_and_stratified_sampling():
    stats = summary_ms(list(range(1, 101)))
    assert stats["p50"] == 50 and stats["p95"] == 95 and stats["p99"] == 99
    cases = [{"split": split, "language": language} for split in ("development", "frozen-test")
             for language in ("zh", "en") for _ in range(3)]
    chosen = stratified_indices(cases, 4)
    assert len({(cases[i]["split"], cases[i]["language"]) for i in chosen}) == 4
    assert memory_mib("1GiB") == 1024
    assert memory_mib("1MB") == 1000000 / 1024 ** 2
    assert memory_mib("bad") is None


def test_paired_delta_groups_correlated_query_variants():
    report = {"cases": [
        {"id": str(i), "split": "frozen-test", "fact_group": group,
         "relevant_chunk_ids": ["correct"]} for i, group in enumerate(["a", "a", "b"])
    ], "rankings": {
        "left": {"reranked": [["correct"], ["correct"], []]},
        "right": {"reranked": [[], [], ["correct"]]},
    }}
    result = paired_recall_delta(report, "left", "right", repeats=100)
    assert result["fact_groups"] == 2 and result["macro_fact_delta"] == 0


def test_contaminated_timing_blocks_are_excluded_equally_without_changing_rankings():
    report = {"rankings": {"a": ["keep"]}, "latency_ms": {"a": {}, "b": {}},
              "docker_samples": [{"time": 1}, {"time": 3}, {"time": 5}],
              "blocks": [
                  {"config": c, "repeat": i, "start": i * 2, "end": i * 2 + 1}
                  for c in ("a", "b") for i in range(3)
              ]}
    trials = [{"config": c, "repeat": i, "elapsed_ms": 10, "stages": {}}
              for c in ("a", "b") for i in range(3)]
    result = exclude_timing_window(report, trials, {"start": 2.5, "end": 3.5})
    assert result["timing_exclusion"]["clean_repeats_per_config"] == 2
    assert result["latency_ms"]["a"]["n"] == result["latency_ms"]["b"]["n"] == 2
    assert result["rankings"] == {"a": ["keep"]}
    assert {s["time"] for s in result["docker_samples"]} == {1, 5}


@pytest.mark.skipif(not os.getenv("TEST_PGVECTOR_URL"), reason="requires isolated pgvector")
def test_pgvector_real_round_trip_and_dimension_isolation(tmp_path):
    settings, bundle = prepared(tmp_path)
    settings.pgvector_url = os.environ["TEST_PGVECTOR_URL"]
    settings.knowledge_vector_backend = "pgvector"
    settings.embedding_model_revision = tmp_path.name
    index = PgVectorIndex(settings)
    manifests = []
    try:
        for vectors in ([[1, 0], [0, 1], [-1, 0]], [[1, 0, 0], [0, 1, 0], [-1, 0, 0]]):
            manifest = index.load(bundle, vectors)
            manifests.append(manifest)
            assert index.ids(manifest) == {row["id"] for row in bundle["chunks"]}
            assert index.search(manifest, vectors[0], 3)[0] == bundle["chunks"][0]["id"]
            from app.knowledge.indexes import index_identity

            Path(settings.knowledge_index_manifest).write_text(
                json.dumps({**manifest, "index_identity": index_identity(bundle, settings)}),
                encoding="utf-8",
            )
            assert KnowledgeIndexes(settings).vector_search(bundle, vectors[0])[0] == bundle["chunks"][0]["id"]
        assert manifests[0]["collection"] != manifests[1]["collection"]
        with pytest.raises(ValueError, match="dimension"):
            index.search(manifests[0], [1, 0, 0])
    finally:
        with index.connect() as conn:
            for manifest in manifests:
                conn.execute(sql.SQL("DROP TABLE {}").format(
                    sql.Identifier("dv_vectors", manifest["collection"])))
