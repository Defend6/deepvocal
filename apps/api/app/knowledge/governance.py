from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def permitted(row: dict, mode: str) -> bool:
    if row.get("text_sha256") != sha256(row.get("text", "")):
        return False
    if row.get("language") not in {"zh", "en"} or not row.get("url") or not row.get("license_evidence"):
        return False
    if not row.get("license_verified") or row.get("review_status") == "BLOCKED":
        return False
    if mode == "research":
        return row.get("license") in {
            "PROJECT-ORIGINAL-INTERNAL",
            "CC-BY-SA-4.0",
            "CC-BY-4.0",
            "CC0-1.0",
        }
    return (
        mode == "production"
        and row.get("review_status") == "APPROVED"
        and row.get("expert_reviewed") is True
        and row.get("production_enabled") is True
        and row.get("license") in {"CC-BY-4.0", "CC0-1.0"}
    )


def load_bundle(path: str | Path, mode: str) -> dict:
    bundle = json.loads(Path(path).read_text(encoding="utf-8"))
    if bundle.get("mode") != mode:
        raise ValueError("knowledge mode differs from prepared bundle")
    rows = bundle["chunks"]
    if len({row["id"] for row in rows}) != len(rows) or not all(
        permitted(row, mode) for row in rows
    ):
        raise ValueError("knowledge bundle contains unapproved or modified chunks")
    fingerprint = sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False))
    if bundle.get("fingerprint") != fingerprint:
        raise ValueError("knowledge fingerprint mismatch")
    return bundle


def prepare(
    root: Path, destination: Path, mode: str = "research", *, allow_missing: bool = False
) -> dict:
    rows = []
    decisions = []
    original_path = root / "project-original" / "knowledge.json"
    registry_path = root / "source-registry.json"
    missing = not original_path.exists() and not registry_path.exists()
    # A clean code checkout may have no corpus. Partial or malformed imports still fail.
    if missing and allow_missing:
        originals, registry = [], {"sources": []}
    else:
        originals = json.loads(original_path.read_text(encoding="utf-8"))
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    for entry in originals:
        row = {
            **entry,
            "source_id": "project-original",
            "language": "zh",
            "license": "PROJECT-ORIGINAL-INTERNAL",
            "license_verified": True,
            "license_evidence": "项目内原创工程知识草稿；只授权当前项目内部研究，未授予第三方资料许可。",
            "url": f"project://deepvocal/knowledge/{entry['id']}",
            "review_status": "NEEDS_REVIEW",
            "expert_reviewed": False,
            "production_enabled": False,
            "text_sha256": sha256(entry["text"]),
        }
        if permitted(row, mode):
            rows.append(row)
    filtered_path = root / "working" / "zh-filtered-candidates.json"
    if filtered_path.is_file():
        filtered = json.loads(filtered_path.read_text(encoding="utf-8"))
        for row in filtered.get("candidates", []):
            if permitted(row, mode) and row["id"] not in {item["id"] for item in rows}:
                rows.append(row)
    source_by_id = {source["source_id"]: source for source in registry["sources"]}
    documents: dict[str, dict] = {}
    documents_path = root / "normalized" / "documents.jsonl"
    if documents_path.is_file():
        for line in documents_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                document = json.loads(line)
                documents[document["document_id"]] = document
    chunks_path = root / "normalized" / "chunks.jsonl"
    if chunks_path.is_file():
        for line in chunks_path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            chunk = json.loads(line)
            source = source_by_id.get(chunk.get("source_id"))
            document = documents.get(chunk.get("document_id"), {})
            if not source or chunk.get("language") not in {"zh", "en"}:
                continue
            row = {
                "id": chunk["chunk_id"],
                "title": document.get("title", chunk["chunk_id"]),
                "entities": document.get("categories", []),
                "text": chunk["text"],
                "source_id": chunk["source_id"],
                "language": chunk["language"],
                "license": source.get("license", "UNKNOWN"),
                "license_verified": bool(source.get("license_evidence")),
                "license_evidence": source.get("license_evidence", ""),
                "url": source.get("url", ""),
                "source_locator": document.get("source_locator", ""),
                "review_status": source.get("review_status", "NEEDS_REVIEW"),
                "expert_reviewed": False,
                "production_enabled": bool(source.get("production_enabled", False)),
                "text_sha256": sha256(chunk["text"]),
            }
            if permitted(row, mode) and row["id"] not in {item["id"] for item in rows}:
                rows.append(row)
    # Existing collected sources remain separate; their expert/rights status is never fabricated.
    for source in registry["sources"]:
        license_name = source.get("license", "UNKNOWN")
        reason = "NOT_CHINESE_REVIEWED_CORPUS"
        status = "NEEDS_REVIEW"
        if "NC" in license_name or "SA" in license_name:
            status, reason = "BLOCKED", "RESTRICTED_LICENSE_NOT_FOR_PUBLIC_PRODUCTION"
        decisions.append(
            {
                "source_id": source["source_id"],
                "license": license_name,
                "status": status,
                "reason": reason,
                "production_enabled": False,
            }
        )
    wiki_root = root / "zh-corpus-20261103" / "raw" / "zhwiki"
    for metadata in sorted(wiki_root.glob("*/metadata.json")):
        data = json.loads(metadata.read_text(encoding="utf-8"))
        decisions.append(
            {
                "source_id": str(data.get("page_id", metadata.parent.name)),
                "license": "CC-BY-SA-4.0",
                "status": "NEEDS_REVIEW",
                "reason": "ZH_WIKI_RESEARCH_PARTITION_REQUIRES_ATTRIBUTION_AND_CONTENT_REVIEW",
                "production_enabled": False,
            }
        )
    bundle = {
        "version": "zh-project-research-v1",
        "mode": mode,
        "chunks": rows,
        "fingerprint": sha256(json.dumps(rows, sort_keys=True, ensure_ascii=False)),
        "production_enabled": False,
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(bundle, ensure_ascii=False, indent=2), encoding="utf-8")
    audit = {
        "mode": mode,
        "selected_chunks": len(rows),
        "source_decisions": decisions,
        "counts": dict(Counter(item["status"] for item in decisions)),
        "expert_review_completed": False,
        "policy_version": "license-gate-v1",
        "corpus_available": not missing,
    }
    (destination.parent / "license-audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return audit


def terms(text: str) -> set[str]:
    words = set(re.findall(r"[a-zA-Z0-9]{2,}", text.lower()))
    chinese = "".join(re.findall(r"[\u4e00-\u9fff]", text))
    return words | {chinese[i : i + 2] for i in range(len(chinese) - 1)}
