"""Local benchmark validation and resumable model-result cache; never caches chat calls."""

import json
import math
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

from app.knowledge.governance import sha256


def validate_frozen_lock(cases_text: str, lock: dict) -> None:
    if lock.get("sha256") != sha256(cases_text):
        raise ValueError("frozen evaluation file changed; create and review a new test version")


@contextmanager
def benchmark_cache_lock(path: Path | None):
    if path is None:
        yield
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    try:
        handle = lock.open("x", encoding="utf-8")
    except FileExistsError as exc:
        raise ValueError("benchmark cache is busy; use another cache or inspect its stale lock") from exc
    try:
        with handle:
            handle.write("Exclusive CLI benchmark cache writer. Remove only after the writer stops.")
        yield
    finally:
        lock.unlink(missing_ok=True)


class BenchmarkCache:
    def __init__(self, path: Path | None, identity: dict):
        self.path = path
        self.namespace = sha256(json.dumps(identity, sort_keys=True))
        self.records = json.loads(path.read_text(encoding="utf-8")) if path and path.exists() else {}
        self.hits = 0
        self.calls = 0

    def save(self):
        if not self.path:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(json.dumps(self.records, ensure_ascii=False), encoding="utf-8")
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    async def call(self, operation: str, payload: list | dict, invoke):
        key = sha256(json.dumps([self.namespace, operation, payload], ensure_ascii=False, sort_keys=True))
        if key in self.records:
            self.hits += 1
            return self.records[key]
        result = await invoke()
        self.calls += 1
        self.records[key] = result
        self.save()
        return result

    async def rerank(self, query: str, texts: list[str], invoke):
        # TEI returns independent sigmoid scores per (query, text), not a listwise softmax.
        keys = [sha256(json.dumps([self.namespace, "rerank-pair", query, text], ensure_ascii=False))
                for text in texts]
        missing = [i for i, key in enumerate(keys) if key not in self.records]
        self.hits += len(texts) - len(missing)
        if missing:
            results = await self.call(
                "rerank-batch", {"query": query, "texts": [texts[i] for i in missing]},
                lambda: invoke([texts[i] for i in missing]),
            )
            if {index for index, _ in results} != set(range(len(missing))):
                raise ValueError("incomplete cached reranking response")
            for index, score in results:
                if not isinstance(score, (int, float)) or not math.isfinite(score):
                    raise ValueError("invalid cached reranking score")
                self.records[keys[missing[index]]] = score
            self.save()
        return sorted(enumerate(self.records[key] for key in keys), key=lambda pair: (-pair[1], pair[0]))


def validate_suite(cases: dict, bundle: dict) -> tuple[list[dict], list[dict]]:
    known = {row["id"]: row for row in bundle["chunks"]}
    seen, queries, groups = set(), set(), {}
    answerable, controls = [], []
    for case in cases.get("cases", []):
        key, query = case.get("id"), case.get("query")
        if not isinstance(key, str) or not key.strip() or key in seen:
            raise ValueError("invalid or duplicate case id")
        if not isinstance(query, str) or not query.strip() or query.casefold().strip() in queries:
            raise ValueError("invalid or duplicate query")
        seen.add(key)
        queries.add(query.casefold().strip())
        labels = case.get("relevant_chunk_ids", [])
        if case.get("answerable", True):
            if not labels or len(set(labels)) != len(labels) or not set(labels) <= known.keys():
                raise ValueError(f"invalid relevance labels for {key}")
            if any(known[cid]["text_sha256"] != value
                   for cid, value in case.get("source_text_sha256", {}).items() if cid in known):
                raise ValueError(f"invalid relevance labels for {key}")
            if set(case.get("source_text_sha256", {})) - known.keys():
                raise ValueError(f"invalid relevance labels for {key}")
            if cases.get("require_complete_provenance"):
                if set(case.get("source_text_sha256", {})) != set(labels):
                    raise ValueError("every relevance label requires a source hash")
                for cid in labels:
                    if not case.get("evidence_spans", {}).get(cid):
                        raise ValueError("every relevance label requires an evidence span")
                    for span in case["evidence_spans"][cid]:
                        start, end = span.get("start"), span.get("end")
                        if (not isinstance(start, int) or not isinstance(end, int)
                                or not 0 <= start < end <= len(known[cid]["text"])
                                or known[cid]["text"][start:end] != span.get("text")):
                            raise ValueError("evaluation evidence span is stale")
            answerable.append(case)
        else:
            if labels or not case.get("expected_behavior"):
                raise ValueError("unanswerable controls must not carry positive relevance labels")
            controls.append(case)
        if cases.get("enforce_document_split") and labels:
            for cid in labels:
                row = known[cid]
                group = row.get("source_locator") or row.get("source_id") or row["id"]
                # Original internal notes are separate authored documents.
                if row.get("source_id") == "project-original":
                    group = f"project-original:{cid}"
                split = case.get("split", "development")
                if group in groups and groups[group] != split:
                    raise ValueError("document leaks across development and frozen test splits")
                groups[group] = split
    if not answerable:
        raise ValueError("benchmark has no answerable cases")
    return answerable, controls


def evidence_summary(rankings: list[list[str]], cases: list[dict], cutoff: int = 5) -> dict:
    precision, complete = [], []
    for ranking, case in zip(rankings, cases, strict=True):
        retrieved = set(ranking[:cutoff])
        labels = set(case["relevant_chunk_ids"])
        precision.append(len(retrieved & labels) / cutoff)
        sufficient = case.get("sufficient_evidence_sets") or [list(labels)]
        if any(not group or not set(group) <= labels for group in sufficient):
            raise ValueError("sufficient evidence sets must be nonempty subsets of labels")
        complete.append(any(set(group) <= retrieved for group in sufficient))
    return {f"precision@{cutoff}": sum(precision) / len(cases),
            f"sufficient_evidence_rate@{cutoff}": sum(complete) / len(cases)}


def latency_summary(values: list[float]) -> dict:
    ordered = sorted(values)
    if not ordered:
        return {"mean": None, "p50": None, "p95": None, "max": None}
    return {"mean": round(sum(values) / len(values), 3),
            "p50": round(ordered[(len(ordered) - 1) // 2], 3),
            "p95": round(ordered[math.ceil(len(ordered) * .95) - 1], 3),
            "max": round(max(ordered), 3)}
