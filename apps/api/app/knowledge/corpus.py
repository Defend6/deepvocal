from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class CorpusNotFoundError(RuntimeError):
    """Raised when the configured corpus preparation directory is unavailable."""


class CorpusStatusService:
    """Read corpus manifests without importing or activating knowledge data."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def status(self) -> dict[str, Any]:
        summary = self._read_json("SUMMARY.json")
        sources = self._read_json("source-registry.json").get("sources", [])
        normalized = self.root / "normalized"

        blockers: list[str] = []
        if summary.get("status") != "PRODUCTION_READY":
            blockers.append("corpus status is not PRODUCTION_READY")
        if not summary.get("expert_review_completed", False):
            blockers.append("expert review is incomplete")
        if not summary.get("embeddings_computed", False):
            blockers.append("embeddings have not been computed")
        if not summary.get("neo4j_loaded", False):
            blockers.append("Neo4j graph has not been loaded")
        if not summary.get("milvus_loaded", False):
            blockers.append("Milvus index has not been loaded")
        if any(source.get("production_enabled") is not True for source in sources):
            blockers.append("one or more sources are not production enabled")

        return {
            "corpus_version": summary.get("version", "unknown"),
            "status": summary.get("status", "UNKNOWN"),
            "document_count": summary.get(
                "document_count", self._count_jsonl(normalized / "documents.jsonl")
            ),
            "chunk_count": summary.get(
                "chunk_count", self._count_jsonl(normalized / "chunks.jsonl")
            ),
            "entity_count": summary.get(
                "entity_count", self._count_jsonl(normalized / "entities.jsonl")
            ),
            "relation_candidate_count": summary.get(
                "candidate_relation_count",
                self._count_jsonl(normalized / "relations_candidate.jsonl"),
            ),
            "source_count": summary.get("source_count", len(sources)),
            "review_status": self._review_status(sources),
            "production_enabled": not blockers,
            "production_blockers": blockers,
            "embeddings_computed": bool(summary.get("embeddings_computed", False)),
            "neo4j_loaded": bool(summary.get("neo4j_loaded", False)),
            "milvus_loaded": bool(summary.get("milvus_loaded", False)),
            "expert_review_completed": bool(summary.get("expert_review_completed", False)),
            "user_practice_record_count": int(summary.get("user_practice_record_count", 0)),
        }

    def _read_json(self, name: str) -> dict[str, Any]:
        path = self.root / name
        if not path.is_file():
            raise CorpusNotFoundError(f"corpus metadata file is missing: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _count_jsonl(path: Path) -> int:
        if not path.is_file():
            raise CorpusNotFoundError(f"corpus data file is missing: {path}")
        return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip())

    @staticmethod
    def _review_status(sources: list[dict[str, Any]]) -> str:
        statuses = {source.get("review_status", "UNKNOWN") for source in sources}
        if not statuses:
            return "UNKNOWN"
        if statuses == {"APPROVED"}:
            return "APPROVED"
        if "NEEDS_REVIEW" in statuses:
            return "NEEDS_REVIEW"
        return "MIXED"
