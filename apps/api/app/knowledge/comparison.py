"""Reproducible two-backend A--F retrieval comparison, without chat calls.

Latency trials never use cached retrieval. Rerank quality may reuse pair scores;
live rerank timing is measured separately on a deterministic stratified subset.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import math
import random
import subprocess
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings
from app.knowledge.benchmark_support import (
    BenchmarkCache,
    benchmark_cache_lock,
    evidence_summary,
    validate_frozen_lock,
    validate_suite,
)
from app.knowledge.entities import graph_identity
from app.knowledge.governance import load_bundle, sha256
from app.knowledge.indexes import KnowledgeIndexes, lexical_search, reciprocal_rank_fusion
from app.knowledge.local_benchmark import metric_summary
from app.knowledge.pgvector import PgVectorIndex
from app.knowledge.provider import ModelProvider


def summary_ms(values):
    ordered = sorted(values)
    if not ordered:
        return {key: None for key in ("mean", "p50", "p95", "p99", "max")}
    return {"mean": sum(ordered) / len(ordered), "max": ordered[-1], **{
        f"p{percentile}": ordered[max(0, math.ceil(len(ordered) * percentile / 100) - 1)]
        for percentile in (50, 95, 99)
    }}


async def measured(function, *args, **kwargs):
    start = time.perf_counter()
    result = await asyncio.to_thread(function, *args, **kwargs)
    return result, (time.perf_counter() - start) * 1000


async def retrieve(
    strategy, bundle, query, vector, vector_search, graph_search, limit=12,
    *, component_cache=None, vector_cache_key="shared",
):
    """Retrieve with optional deterministic component reuse for large comparisons.

    Component latencies are measured on the first execution for each query and reused;
    pipeline latency is then reconstructed from the A--G dependency graph. Live timings
    bypass this cache and are measured separately.
    """
    started = time.perf_counter()
    stages = {}

    async def lexical():
        key = (bundle.get("fingerprint", ""), query.casefold().strip())
        cached = component_cache["lexical"].get(key) if component_cache is not None else None
        if cached is None:
            ids, elapsed = await measured(lexical_search, bundle, query)
            if component_cache is not None:
                component_cache["lexical"][key] = (ids, elapsed)
        else:
            ids, elapsed = cached
        stages["keyword_ms"] = elapsed
        return ids

    async def dense():
        key = (vector_cache_key, query.casefold().strip())
        cached = component_cache["vector"].get(key) if component_cache is not None else None
        if cached is None:
            ids, elapsed = await measured(vector_search, vector, limit)
            if component_cache is not None:
                component_cache["vector"][key] = (ids, elapsed)
        else:
            ids, elapsed = cached
        stages["vector_ms"] = elapsed
        return ids

    async def graph(seeds=None):
        seed_key = tuple(seeds or ())
        key = (bundle.get("fingerprint", ""), query.casefold().strip(), seed_key)
        cached = component_cache["graph"].get(key) if component_cache is not None else None
        if cached is None:
            ids, elapsed = await measured(graph_search, bundle, query, seed_ids=seeds)
            if component_cache is not None:
                component_cache["graph"][key] = (ids, elapsed)
        else:
            ids, elapsed = cached
        stages["graph_ms"] = elapsed
        return ids

    async def hybrid():
        vectors, keywords = await asyncio.gather(dense(), lexical())
        return reciprocal_rank_fusion(vectors, keywords)[:limit]

    if strategy == "A":
        result = await lexical()
    elif strategy == "B":
        result = await dense()
    elif strategy == "C":
        result = await graph()
    elif strategy == "D":
        result = await hybrid()
    elif strategy == "E":
        anchors = await hybrid()
        related = await graph(anchors[:2])
        result = reciprocal_rank_fusion(anchors, related)
    elif strategy == "F":
        anchors, related = await asyncio.gather(hybrid(), graph())
        result = reciprocal_rank_fusion(anchors, related)
    elif strategy == "G":
        anchors = await hybrid()
        related = await graph()
        result = reciprocal_rank_fusion(anchors, related)
    else:
        raise ValueError("unknown retrieval strategy")
    if component_cache is None:
        elapsed_ms = (time.perf_counter() - started) * 1000
    else:
        hybrid_ms = max(stages.get("keyword_ms", 0), stages.get("vector_ms", 0))
        if strategy == "A":
            elapsed_ms = stages["keyword_ms"]
        elif strategy == "B":
            elapsed_ms = stages["vector_ms"]
        elif strategy == "C":
            elapsed_ms = stages["graph_ms"]
        elif strategy == "D":
            elapsed_ms = hybrid_ms
        elif strategy == "E":
            elapsed_ms = hybrid_ms + stages["graph_ms"]
        elif strategy == "F":
            elapsed_ms = max(hybrid_ms, stages["graph_ms"])
        else:  # G is the sequential scheduling control for F.
            elapsed_ms = hybrid_ms + stages["graph_ms"]
    return {"ranking": result[:limit], "stages": stages, "elapsed_ms": elapsed_ms,
            "timing_basis": "component_first_execution_and_dependency_dag" if component_cache is not None else "wall_clock"}


class DockerSampler:
    """Whole-container samples, not per-query allocation or index-only RAM."""
    def __init__(self, containers):
        self.containers, self.samples = containers, []
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        while not self.stop.is_set():
            try:
                output = subprocess.run(
                    ["docker", "stats", "--no-stream", "--format", "{{json .}}", *self.containers],
                    capture_output=True, text=True, timeout=12, check=False,
                )
                if output.returncode == 0:
                    for line in output.stdout.splitlines():
                        self.samples.append({"time": time.time(), **json.loads(line)})
            except (OSError, ValueError, subprocess.TimeoutExpired):
                pass
            self.stop.wait(2)

    def close(self):
        self.stop.set()
        self.thread.join(timeout=15)


def metrics(rankings, cases):
    return {**{key: value for cutoff in (5, 10) for key, value in {
        **metric_summary(rankings, [c["relevant_chunk_ids"] for c in cases], cutoff),
        **evidence_summary(rankings, cases, cutoff),
    }.items()}}


def stratified_indices(cases, count):
    groups = {}
    for index, case in enumerate(cases):
        groups.setdefault((case.get("split"), case.get("language")), []).append(index)
    selected = []
    while len(selected) < min(count, len(cases)):
        for group in groups.values():
            if group and len(selected) < count:
                selected.append(group.pop(0))
    return selected


async def run(settings, cases_data, output, *, cache_path=None, repeats=3, live_samples=12,
              containers=(), case_limit=None):
    if not settings.embedding_api_base or not settings.reranker_api_base:
        raise ValueError("explicit local embedding and reranker endpoints required")
    bundle = load_bundle(settings.knowledge_bundle, settings.knowledge_mode)
    if cases_data.get("corpus_fingerprint") != bundle["fingerprint"]:
        raise ValueError("evaluation corpus fingerprint mismatch")
    cases, controls = validate_suite(cases_data, bundle)
    if case_limit:
        cases = cases[:case_limit]
    rows = bundle["chunks"]
    by_id = {row["id"]: row for row in rows}
    provider = ModelProvider(settings)
    cache = BenchmarkCache(cache_path, {
        "embedding_model": settings.embedding_model,
        "embedding_revision": settings.embedding_model_revision,
        "reranker_model": settings.reranker_model,
        "reranker_revision": settings.reranker_model_revision,
        "embedding_base": settings.embedding_api_base, "reranker_base": settings.reranker_api_base,
    }, flush_every=25)
    async def embed(texts):
        keys = [sha256(json.dumps([cache.namespace, "embedding-item", text],
                                  ensure_ascii=False, sort_keys=True)) for text in texts]
        if all(key in cache.records for key in keys):
            cache.hits += len(keys)
            return [cache.records[key] for key in keys]
        result = await cache.call("embedding", texts, lambda: provider.embed(texts))
        cache.records.update(zip(keys, result, strict=True))
        cache.save()
        return result

    documents, queries = [], []
    for texts, target in (([r["text"] for r in rows], documents),
                          ([c["query"] for c in cases], queries)):
        for start in range(0, len(texts), 16):
            target.extend(await embed(texts[start:start + 16]))
    print(json.dumps({"phase": "embeddings_ready", "chunks": len(rows), "cases": len(cases)}), flush=True)
    # Compute the independent exact-cosine oracle in one matrix operation. The previous
    # Python nested loops scaled as O(query_count * chunk_count * dimension) interpreter
    # operations and became prohibitive on corpora with thousands of chunks.
    import numpy as np

    oracle_ids = sorted(by_id)
    row_position = {row["id"]: index for index, row in enumerate(rows)}
    document_matrix = np.asarray(
        [documents[row_position[key]] for key in oracle_ids], dtype=np.float32
    )
    query_matrix = np.asarray(queries, dtype=np.float32)
    document_norms = np.linalg.norm(document_matrix, axis=1)
    query_norms = np.linalg.norm(query_matrix, axis=1)
    if np.any(document_norms == 0) or np.any(query_norms == 0):
        raise ValueError("exact cosine oracle received a zero vector")
    similarities = (query_matrix @ document_matrix.T) / (
        query_norms[:, None] * document_norms[None, :]
    )
    oracle_indices = np.argsort(-similarities, axis=1, kind="stable")[:, :12]
    oracle = [[oracle_ids[index] for index in ranking] for ranking in oracle_indices]
    from pymilvus import MilvusClient
    milvus = MilvusClient(uri=settings.milvus_uri)
    # Dedicated comparison collection. No writes to active service manifests/collections.
    collection = "dvab_" + sha256(bundle["fingerprint"] + settings.embedding_model_revision)[:18]
    builds = {}
    pg = PgVectorIndex(settings)
    started = time.perf_counter()
    manifest = await asyncio.to_thread(pg.load, bundle, documents)
    builds["pgvector_seconds"] = time.perf_counter() - started
    pg_conn = pg.connect()
    started = time.perf_counter()
    if not milvus.has_collection(collection):
        index = milvus.prepare_index_params()
        index.add_index(field_name="vector", index_type="FLAT", metric_type="COSINE")
        milvus.create_collection(collection, dimension=len(documents[0]),
                                 primary_field_name="id", id_type="string", max_length=128,
                                 auto_id=False, metric_type="COSINE", index_params=index)
    milvus.upsert(collection, [{"id": row["id"], "vector": vector}
                              for row, vector in zip(rows, documents, strict=True)])
    milvus.flush(collection)
    builds["milvus_seconds"] = time.perf_counter() - started
    indexes = KnowledgeIndexes(settings)
    started = time.perf_counter()
    await asyncio.to_thread(indexes.graph_load, bundle)
    builds["neo4j_seconds"] = time.perf_counter() - started

    def milvus_search(vector, limit):
        result = milvus.search(collection, data=[vector], limit=limit,
                               search_params={"metric_type": "COSINE"},
                               consistency_level="Strong")
        # Deterministic id tie-breaking for equal scores.
        return [r["id"] for r in sorted(result[0], key=lambda r: (-r["distance"], r["id"]))]

    searches = {"pgvector": lambda vector, limit: pg.query(pg_conn, manifest, vector, limit),
                "milvus": milvus_search}
    configs = [("shared", "A"), ("shared", "C")] + [
        (backend, strategy) for backend in searches for strategy in ("B", "D", "E", "F", "G")]
    rankings, trials, errors, blocks = {}, [], [], []
    component_cache = {"lexical": {}, "vector": {}, "graph": {}}
    sampler = DockerSampler(list(containers))
    if containers:
        sampler.thread.start()
    output.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = output.with_suffix(".trials.jsonl")
    try:
        # Warm connections and graph query plans; this is a warm-cache benchmark.
        for backend, strategy in configs:
            await retrieve(strategy, bundle, cases[0]["query"], queries[0],
                           searches.get(backend, searches["pgvector"]), indexes.graph_search,
                           component_cache=component_cache, vector_cache_key=backend)
        with checkpoint.open("w", encoding="utf-8") as stream:
            for repeat in range(repeats):
                order = configs.copy()
                random.Random(2400 + repeat).shuffle(order)
                case_order = list(range(len(cases)))
                random.Random(2410 + repeat).shuffle(case_order)
                for backend, strategy in order:
                    config = f"{backend}_{strategy}"
                    block_start = time.time()
                    rankings.setdefault(config, [None] * len(cases))
                    for i in case_order:
                        case = cases[i]
                        try:
                            trial = await retrieve(strategy, bundle, case["query"], queries[i],
                                                   searches.get(backend, searches["pgvector"]),
                                                   indexes.graph_search, component_cache=component_cache,
                                                   vector_cache_key=backend)
                            if rankings[config][i] is None:
                                rankings[config][i] = trial["ranking"]
                            elif rankings[config][i] != trial["ranking"]:
                                raise ValueError("nondeterministic ranking across repeats")
                            trial.update(config=config, case_id=case["id"], repeat=repeat)
                            trials.append(trial)
                            stream.write(json.dumps(trial, ensure_ascii=False) + "\n")
                        except Exception as exc:  # noqa: BLE001 -- record redacted failures, then fail run
                            errors.append({"config": config, "case_id": case["id"],
                                           "repeat": repeat, "type": type(exc).__name__})
                            if rankings[config][i] is None:
                                rankings[config][i] = []
                    stream.flush()
                    blocks.append({"config": config, "repeat": repeat, "start": block_start,
                                   "end": time.time()})
                    print(json.dumps({"phase": "retrieval", "repeat": repeat,
                                      "config": config, "errors": len(errors)}), flush=True)
    finally:
        if containers:
            sampler.close()
    if errors:
        output.with_suffix(".errors.json").write_text(json.dumps(errors), encoding="utf-8")
        raise RuntimeError("retrieval trial errors; inspect redacted errors and trials files")
    # G and F differ only in scheduling. A result change is an implementation error.
    for backend in searches:
        if rankings[f"{backend}_F"] != rankings[f"{backend}_G"]:
            raise ValueError("parallel/serial scheduling control rankings differ")
    reranked = {key: [] for key in rankings}
    for i, case in enumerate(cases):
        union = list(dict.fromkeys(cid for ranking in rankings.values() for cid in ranking[i]))
        scores = {}
        for start in range(0, len(union), 8):
            ids = union[start:start + 8]
            result = await cache.rerank(case["query"], [by_id[cid]["text"] for cid in ids],
                                        lambda texts, query=case["query"]: provider.rerank(query, texts))
            scores.update({ids[index]: score for index, score in result})
        for config, ranking in rankings.items():
            reranked[config].append(sorted(ranking[i], key=lambda cid: (-scores[cid], cid)))
        if i % 25 == 0:
            print(json.dumps({"phase": "rerank_quality", "completed": i + 1}), flush=True)
    # Flush cached reranker scores before live timings, both for resumability and to
    # avoid a final cache rewrite failing after the expensive quality pass.
    cache.save()
    # Latency here is live, with cache bypassed. Query vectors remain precomputed.
    live, embedding_live = [], []
    selected = stratified_indices(cases, live_samples)
    for i in selected:
        start = time.perf_counter()
        await provider.embed([cases[i]["query"]])
        embedding_live.append((time.perf_counter() - start) * 1000)
    for backend, strategy in configs:
        config = f"{backend}_{strategy}"
        for i in selected:
            trial = await retrieve(strategy, bundle, cases[i]["query"], queries[i],
                                   searches.get(backend, searches["pgvector"]),
                                   indexes.graph_search)
            start = time.perf_counter()
            if trial["ranking"]:
                await provider.rerank(cases[i]["query"],
                                      [by_id[cid]["text"] for cid in trial["ranking"]])
            elapsed = (time.perf_counter() - start) * 1000
            live.append({"config": config, "case_id": cases[i]["id"],
                         "rerank_ms": elapsed,
                         "retrieval_plus_rerank_ms": elapsed + trial["elapsed_ms"]})
        print(json.dumps({"phase": "live_timing", "config": config}), flush=True)
    pg_conn.close()
    milvus.close()
    report = {
        "created_at": datetime.now(UTC).isoformat(), "corpus_fingerprint": bundle["fingerprint"],
        "graph_identity": graph_identity(bundle), "chunk_count": len(rows),
        "case_count": len(cases), "controls_not_executed": len(controls),
        "case_hash": sha256(json.dumps(cases_data, sort_keys=True, ensure_ascii=False)),
        "label_status": cases_data.get("label_status"),
        "models": {"embedding": settings.embedding_model,
                   "embedding_revision": settings.embedding_model_revision,
                   "reranker": settings.reranker_model,
                   "reranker_revision": settings.reranker_model_revision},
        "index_mode": "exact cosine; Milvus FLAT; pgvector sequential scan",
        "build_seconds": builds, "repeats": repeats, "candidate_limit": 12,
        "query_concurrency": 1, "cache": {"hits": cache.hits, "requests": cache.calls},
        "live_timing_case_ids": [cases[i]["id"] for i in selected],
        "query_embedding_live_ms": summary_ms(embedding_live),
        "rankings": {key: {"raw": value, "reranked": reranked[key]} for key, value in rankings.items()},
        "cases": cases, "metrics": {}, "latency_ms": {},
        "docker_samples": sampler.samples, "blocks": blocks, "live_trials": live,
        "failures": errors,
        "limitations": [
            f"{len(rows)}-chunk research corpus is small; no production-scale performance claim",
            "warm cache and connection reuse; no cold-cache or concurrent-load claim",
            "rerank quality uses reusable independent pair scores; latency uses uncached live calls",
            "vector connections reused; current graph adapter includes driver creation and entity linking",
            "query embedding excluded from retrieval and retrieval+rerank timings, reported separately",
            "existing provisional relevance labels are incomplete; corpus review != label review",
            "A and C are shared controls, not separate vector-database results",
            "no generated answers or safety-control pass rate claimed",
            "resource samples measure whole containers, not index-only RAM or per-query allocations",
        ],
    }
    for config, ranking in rankings.items():
        report["metrics"][config] = {}
        for split in ("all", "development", "frozen-test"):
            selected_ids = [i for i, c in enumerate(cases) if split == "all" or c.get("split") == split]
            if selected_ids:
                report["metrics"][config][split] = {
                    "count": len(selected_ids),
                    **{stage: metrics([values[i] for i in selected_ids],
                                       [cases[i] for i in selected_ids])
                       for stage, values in (("raw", ranking), ("reranked", reranked[config]))},
                }
        own = [t for t in trials if t["config"] == config]
        own_live = [t for t in live if t["config"] == config]
        report["latency_ms"][config] = {
            "n": len(own), "retrieval": summary_ms([t["elapsed_ms"] for t in own]),
            "observed_sequential_qps": len(own) / sum(t["elapsed_ms"] / 1000 for t in own),
            "stages": {stage: summary_ms([t["stages"][stage] for t in own if stage in t["stages"]])
                       for stage in ("vector_ms", "keyword_ms", "graph_ms")},
            "live_rerank": summary_ms([t["rerank_ms"] for t in own_live]),
            "live_pipeline": summary_ms([t["retrieval_plus_rerank_ms"] for t in own_live]),
        }
    report["exact_oracle_overlap_at12"] = {backend: sum(
        len(set(actual) & set(expected)) / len(expected)
        for actual, expected in zip(rankings[f"{backend}_B"], oracle, strict=True)
    ) / len(cases) for backend in searches}
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_csv(report, output.with_suffix(".csv"))
    return report


def write_csv(report, path):
    fields = ["config", "split", "stage", "count", "recall@5", "recall@10",
              "mrr@10", "ndcg@10", "precision@5", "sufficient_evidence_rate@5",
              "retrieval_p50_ms", "retrieval_p95_ms", "retrieval_p99_ms", "live_pipeline_p50_ms"]
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for config, splits in report["metrics"].items():
            timing = report["latency_ms"][config]
            for split, data in splits.items():
                for stage in ("raw", "reranked"):
                    writer.writerow({"config": config, "split": split, "stage": stage,
                                     "count": data["count"], **data[stage],
                                     **{f"retrieval_p{p}_ms": timing["retrieval"][f"p{p}"]
                                        for p in (50, 95, 99)},
                                     "live_pipeline_p50_ms": timing["live_pipeline"]["p50"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--frozen-cases", type=Path, required=True)
    parser.add_argument("--frozen-lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--repeats", type=int, default=3, choices=range(1, 11))
    parser.add_argument("--live-samples", type=int, default=12, choices=range(101))
    parser.add_argument("--case-limit", type=int)
    parser.add_argument("--resource-containers", nargs="*", default=[])
    args = parser.parse_args()
    frozen_text = args.frozen_cases.read_text(encoding="utf-8")
    validate_frozen_lock(frozen_text, json.loads(args.frozen_lock.read_text(encoding="utf-8")))
    data = json.loads(args.cases.read_text(encoding="utf-8"))
    frozen = json.loads(frozen_text)
    selected_frozen = [c for c in data["cases"] if c.get("split") == "frozen-test"]
    if selected_frozen != frozen["cases"]:
        raise ValueError("combined evaluation frozen cases differ from locked file")
    settings = Settings(knowledge_bundle=str(args.bundle), model_api_key="")
    with benchmark_cache_lock(args.cache):
        asyncio.run(run(settings, data, args.output, cache_path=args.cache,
                        repeats=args.repeats, live_samples=args.live_samples,
                        containers=args.resource_containers, case_limit=args.case_limit))


if __name__ == "__main__":
    main()
