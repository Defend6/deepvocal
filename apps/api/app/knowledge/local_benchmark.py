"""Local-only retrieval benchmark for embedding and reranker comparisons."""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import time
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.knowledge.governance import load_bundle, sha256
from app.knowledge.indexes import KnowledgeIndexes, lexical_search, reciprocal_rank_fusion
from app.knowledge.provider import ModelProvider


def cosine(left: list[float], right: list[float]) -> float:
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm or len(left) != len(right):
        raise ValueError("invalid embedding vector")
    return sum(a * b for a, b in zip(left, right, strict=True)) / (left_norm * right_norm)


def metric_summary(rankings: list[list[str]], relevant: list[list[str]], cutoff: int = 5) -> dict:
    if not rankings or len(rankings) != len(relevant) or cutoff < 1:
        raise ValueError("invalid benchmark rankings")
    reciprocal_ranks, recalls, ndcgs, hits = [], [], [], []
    for ranking, expected in zip(rankings, relevant, strict=True):
        expected_set = set(expected)
        if not expected_set or len(expected_set) != len(expected):
            raise ValueError("relevance labels must be nonempty and unique")
        if len(set(ranking)) != len(ranking):
            raise ValueError("ranking must not contain duplicate chunks")
        positions = [rank for rank, item in enumerate(ranking[:cutoff], 1) if item in expected_set]
        reciprocal_ranks.append(1 / positions[0] if positions else 0.0)
        hits.append(bool(positions and positions[0] <= cutoff))
        recalls.append(
            len({item for item in ranking[:cutoff] if item in expected_set}) / len(expected_set)
        )
        dcg = sum(
            1 / math.log2(rank + 1)
            for rank, item in enumerate(ranking[:cutoff], 1)
            if item in expected_set
        )
        ideal = sum(
            1 / math.log2(rank + 1) for rank in range(1, min(len(expected_set), cutoff) + 1)
        )
        ndcgs.append(dcg / ideal if ideal else 0.0)
    return {
        f"mrr@{cutoff}": sum(reciprocal_ranks) / len(reciprocal_ranks),
        f"recall@{cutoff}": sum(recalls) / len(recalls),
        f"ndcg@{cutoff}": sum(ndcgs) / len(ndcgs),
        f"hit_rate@{cutoff}": sum(hits) / len(hits),
    }


async def run_benchmark(
    settings,
    cases: dict,
    *,
    candidate_limit: int = 12,
    use_indexes: bool = False,
    concurrency: int = 1,
) -> dict:
    if not settings.embedding_api_base or not settings.reranker_api_base:
        raise ValueError("configure both local embedding and reranker API bases")
    bundle = load_bundle(settings.knowledge_bundle, settings.knowledge_mode)
    if cases.get("corpus_fingerprint", bundle["fingerprint"]) != bundle["fingerprint"]:
        raise ValueError("evaluation corpus fingerprint changed; review labels first")
    rows = bundle["chunks"]
    if not rows or not 1 <= candidate_limit <= 32 or not 1 <= concurrency <= 2:
        raise ValueError("invalid corpus, candidate limit or concurrency")
    row_ids = [row["id"] for row in rows]
    by_id = dict(zip(row_ids, rows, strict=True))
    test_cases = cases.get("cases", [])
    if not test_cases:
        raise ValueError("benchmark has no cases")
    case_ids, relevant = [], []
    for case in test_cases:
        labels = case.get("relevant_chunk_ids", [])
        if (
            not labels
            or len(set(labels)) != len(labels)
            or not set(labels) <= set(by_id)
            or not isinstance(case.get("query"), str)
            or not case["query"].strip()
            or case.get("id") in case_ids
            or not isinstance(case.get("id"), str)
            or not case["id"].strip()
            or any(
                key not in by_id or by_id[key]["text_sha256"] != digest
                for key, digest in case.get("source_text_sha256", {}).items()
            )
        ):
            raise ValueError(f"invalid relevance labels for {case.get('id')}")
        case_ids.append(case["id"])
        relevant.append(labels)

    provider = ModelProvider(settings)
    started = time.perf_counter()
    document_vectors = []
    for start in range(0, len(rows), 16):
        document_vectors.extend(
            await provider.embed([row["text"] for row in rows[start : start + 16]])
        )
    document_embedding_seconds = time.perf_counter() - started
    started = time.perf_counter()
    query_vectors = []
    for start in range(0, len(test_cases), 16):
        query_vectors.extend(
            await provider.embed([case["query"] for case in test_cases[start : start + 16]])
        )
    query_embedding_seconds = time.perf_counter() - started

    vector_rankings = [
        [
            row_ids[index]
            for index in sorted(
                range(len(rows)),
                key=lambda candidate: cosine(query_vector, document_vectors[candidate]),
                reverse=True,
            )
        ]
        for query_vector in query_vectors
    ]
    lexical_rankings = [lexical_search(bundle, case["query"]) for case in test_cases]

    index_rankings, graph_rankings, hybrid_rankings = [], [], []
    index_seconds = []
    if use_indexes:
        indexes = KnowledgeIndexes(settings)
        for case, query_vector, lexical in zip(
            test_cases, query_vectors, lexical_rankings, strict=True
        ):
            started_at = time.perf_counter()
            vector_ids = await asyncio.to_thread(indexes.vector_search, bundle, query_vector)
            graph_ids = await asyncio.to_thread(indexes.graph_search, bundle, case["query"])
            index_seconds.append(time.perf_counter() - started_at)
            index_rankings.append(vector_ids)
            graph_rankings.append(graph_ids)
            hybrid_rankings.append(reciprocal_rank_fusion(lexical, vector_ids, graph_ids))
    rerank_seconds = []
    semaphore = asyncio.Semaphore(concurrency)

    async def rerank_one(case, ranking):
        candidates = ranking[:candidate_limit]
        async with semaphore:
            started_at = time.perf_counter()
            result = await provider.rerank(
                case["query"], [by_id[key]["text"] for key in candidates]
            )
            elapsed = time.perf_counter() - started_at
        reranked = [candidates[index] for index, _score in result]
        reranked.extend(key for key in candidates if key not in set(reranked))
        return reranked + ranking[candidate_limit:], elapsed

    rerank_results = await asyncio.gather(
        *(
            rerank_one(case, ranking)
            for case, ranking in zip(test_cases, vector_rankings, strict=True)
        )
    )
    reranked_rankings = [ranking for ranking, _elapsed in rerank_results]
    rerank_seconds = [elapsed for _ranking, elapsed in rerank_results]

    ranking_groups = {
        "lexical": lexical_rankings,
        "embedding": vector_rankings,
        "embedding_plus_reranker": reranked_rankings,
    }
    if use_indexes:
        hybrid_results = await asyncio.gather(
            *(
                rerank_one(case, ranking)
                for case, ranking in zip(test_cases, hybrid_rankings, strict=True)
            )
        )
        ranking_groups.update(
            {
                "milvus": index_rankings,
                "neo4j_entity_graph": graph_rankings,
                "hybrid_rrf": hybrid_rankings,
                "hybrid_plus_reranker": [ranking for ranking, _elapsed in hybrid_results],
            }
        )

    grouped_metrics = {}
    for language in sorted({case.get("language") or "unspecified" for case in test_cases}):
        selected = [
            i
            for i, case in enumerate(test_cases)
            if (case.get("language") or "unspecified") == language
        ]
        grouped_metrics[language] = {
            "case_count": len(selected),
            **{
                name: metric_summary(
                    [rankings[i] for i in selected], [relevant[i] for i in selected]
                )
                for name, rankings in ranking_groups.items()
            },
        }
    ordered_latency = sorted(rerank_seconds)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "version": cases.get("version", "unspecified"),
        "scope": cases.get("scope", "local research evaluation"),
        "label_status": cases.get("label_status", "provisional"),
        "corpus_fingerprint": bundle["fingerprint"],
        "cases_sha256": sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True)),
        "embedding_model": settings.embedding_model,
        "embedding_model_revision": settings.embedding_model_revision,
        "reranker_model": settings.reranker_model,
        "reranker_model_revision": settings.reranker_model_revision,
        "candidate_limit": candidate_limit,
        "concurrency": concurrency,
        "use_indexes": use_indexes,
        "case_count": len(test_cases),
        "chunk_count": len(rows),
        "embedding_dimension": len(document_vectors[0]),
        "metrics": {
            name: metric_summary(rankings, relevant) for name, rankings in ranking_groups.items()
        },
        "metrics_by_language": grouped_metrics,
        "latency_seconds": {
            "document_embedding_total": round(document_embedding_seconds, 3),
            "query_embedding_total": round(query_embedding_seconds, 3),
            "reranker_mean_per_query": round(sum(rerank_seconds) / len(rerank_seconds), 3),
            "reranker_max_per_query": round(max(rerank_seconds), 3),
            "reranker_p50_per_query": round(ordered_latency[(len(ordered_latency) - 1) // 2], 3),
            "reranker_p95_per_query": round(
                ordered_latency[math.ceil(len(ordered_latency) * 0.95) - 1], 3
            ),
            "database_query_mean": round(sum(index_seconds) / len(index_seconds), 3)
            if index_seconds
            else None,
            "hybrid_reranker_mean_per_query": round(
                sum(elapsed for _, elapsed in hybrid_results) / len(hybrid_results), 3
            )
            if use_indexes
            else None,
        },
        "results": [
            {
                "id": case_id,
                "query": case["query"],
                "language": case.get("language"),
                "relevant_chunk_ids": labels,
                "lexical_top5": lexical[:5],
                "embedding_top5": vector[:5],
                "reranked_top5": reranked[:5],
                "rankings_top5": {
                    name: rankings[index][:5] for name, rankings in ranking_groups.items()
                },
            }
            for index, (case_id, case, labels, lexical, vector, reranked) in enumerate(
                zip(
                    case_ids,
                    test_cases,
                    relevant,
                    lexical_rankings,
                    vector_rankings,
                    reranked_rankings,
                    strict=True,
                )
            )
        ],
        "limitations": [
            "exploratory benchmark with provisional labels; not expert reviewed or a blinded test",
            "retrieval quality only; no generated-answer or expert teaching-quality evaluation",
            "all source chunks remain in research mode and are not production-approved",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--use-indexes",
        action="store_true",
        help="also verify real Milvus/Neo4j and hybrid retrieval",
    )
    parser.add_argument("--concurrency", type=int, default=1, choices=[1, 2])
    args = parser.parse_args()
    settings = get_settings().model_copy(update={"knowledge_bundle": str(args.bundle)})
    report = asyncio.run(
        run_benchmark(
            settings,
            json.loads(args.cases.read_text(encoding="utf-8")),
            use_indexes=args.use_indexes,
            concurrency=args.concurrency,
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {"metrics": report["metrics"], "latency_seconds": report["latency_seconds"]},
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
