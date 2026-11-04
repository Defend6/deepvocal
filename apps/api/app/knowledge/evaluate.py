"""Reproducible offline smoke evaluation; deliberately never invokes a paid model."""

import argparse
import asyncio
import json
from pathlib import Path

from app.config import get_settings
from app.knowledge.governance import load_bundle, sha256
from app.knowledge.indexes import lexical_search
from app.knowledge.service import KnowledgeService


async def evaluate(settings, cases: dict) -> dict:
    if not cases.get("retrieval") or not cases.get("guardrails"):
        raise ValueError("evaluation requires retrieval and guardrail cases")
    settings = settings.model_copy(update={"model_api_key": ""})
    bundle = load_bundle(settings.knowledge_bundle, settings.knowledge_mode)
    known = {row["id"] for row in bundle["chunks"]}
    rows, guards, ids = [], [], set()
    for case in cases["retrieval"]:
        if not case["relevant"] or not set(case["relevant"]) <= known or case["id"] in ids:
            raise ValueError("duplicate case or unknown relevance labels")
        ids.add(case["id"])
        ranking = lexical_search(bundle, case["question"])
        ranks = [i for i, value in enumerate(ranking, 1) if value in case["relevant"]]
        first = min(ranks) if ranks else None
        rows.append(
            {
                "id": case["id"],
                "ranking": ranking[:5],
                "first_relevant_rank": first,
                "hit_at_1": first == 1,
                "hit_at_5": first is not None and first <= 5,
                "reciprocal_rank": 1 / first if first else 0,
            }
        )
    service = KnowledgeService(settings)
    for case in cases["guardrails"]:
        if case["id"] in ids:
            raise ValueError("duplicate case")
        ids.add(case["id"])
        result = await service.answer(case["question"])
        guards.append(
            {
                "id": case["id"],
                "expected": case["expected"],
                "actual": result.get("reason"),
                "passed": result.get("reason") == case["expected"]
                and result.get("status") != "answered",
            }
        )
    return {
        "version": cases["version"],
        "scope": cases["scope"],
        "mode": "offline_lexical_and_no_key_guardrails",
        "corpus_fingerprint": bundle["fingerprint"],
        "cases_sha256": sha256(json.dumps(cases, ensure_ascii=False, sort_keys=True)),
        "model_calls": 0,
        "expert_benchmark": False,
        "hybrid_retrieval_evaluated": False,
        "metrics": {
            "retrieval_cases": len(rows),
            "hit_at_1": sum(r["hit_at_1"] for r in rows) / len(rows),
            "hit_at_5": sum(r["hit_at_5"] for r in rows) / len(rows),
            "mrr": sum(r["reciprocal_rank"] for r in rows) / len(rows),
            "guardrail_passed": sum(r["passed"] for r in guards),
            "guardrail_cases": len(guards),
        },
        "retrieval": rows,
        "guardrails": guards,
        "limitations": [
            "seed-derived smoke cases; not independent or expert reviewed",
            "no semantic answer entailment, real embeddings, reranker or paid model quality evaluated",
        ],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = asyncio.run(
        evaluate(get_settings(), json.loads(args.cases.read_text(encoding="utf-8")))
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["metrics"], ensure_ascii=False))
    raise SystemExit(
        0
        if report["metrics"]["guardrail_passed"] == report["metrics"]["guardrail_cases"]
        and report["metrics"]["hit_at_5"] >= 0.9
        else 1
    )


if __name__ == "__main__":
    main()
