from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from app.knowledge.governance import sha256

TOPIC_TERMS = (
    "声乐",
    "聲樂",
    "歌唱",
    "歌声",
    "人声",
    "音高",
    "音准",
    "音域",
    "声带",
    "聲帶",
    "共振",
    "共振峰",
    "假声",
    "声部",
    "聲部",
    "呼吸",
    "发声",
    "音区",
    "音區",
    "颤音",
    "顫音",
    "混响",
    "混響",
    "采样率",
    "频谱",
    "声学",
    "声音",
    "音乐理论",
)


def _clean_wikitext(value: str) -> str:
    """Convert a downloaded wikitext page into conservative plain text."""
    text = re.sub(r"<!--.*?-->", " ", value, flags=re.DOTALL)
    text = re.sub(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", " ", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\[\[[^]|]+\|([^]]+)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^]]+)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+\s+([^]]+)\]", r"\1", text)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"\{\|.*?\|\}", " ", text, flags=re.DOTALL)
    text = re.sub(r"^\s*[|!*;:].*$", " ", text, flags=re.MULTILINE)
    text = re.sub(r"^=+.*?=+\s*$", "\n", text, flags=re.MULTILINE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"'{2,}", "", text)
    text = re.sub(r"[^\w\u3400-\u9fff，。！？；：、“”‘’（）《》、.?!,:;()\- ]", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def _chunks(text: str, minimum: int = 80, maximum: int = 1200) -> list[str]:
    paragraphs = [re.sub(r"\s+", " ", p).strip() for p in text.splitlines()]
    paragraphs = [p for p in paragraphs if len(p) >= minimum]
    result: list[str] = []
    current = ""
    for paragraph in paragraphs:
        if current and len(current) + len(paragraph) + 1 > maximum:
            result.append(current)
            current = ""
        current = f"{current} {paragraph}".strip()
    if len(current) >= minimum:
        result.append(current)
    return result


def _metadata(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    pages = value.get("query", {}).get("pages", {})
    if pages:
        value = next(iter(pages.values()))
        revisions = value.get("revisions") or []
        if revisions and isinstance(revisions[0], dict):
            value = {**value, "revid": revisions[0].get("revid", "")}
        return value
    return value


def filter_chinese_corpus(root: Path, destination: Path) -> dict[str, Any]:
    wiki_root = root / "zh-corpus-20261103" / "raw" / "zhwiki"
    candidates: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for metadata_path in sorted(wiki_root.glob("*/metadata.json")):
        metadata = _metadata(metadata_path)
        page_id = str(metadata.get("pageid", metadata_path.parent.name))
        title = str(metadata.get("title", "")).strip()
        page = metadata_path.parent / "source.wiki"
        if not title or not page.is_file():
            excluded.append({"page_id": page_id, "reason": "MISSING_PAGE_OR_TITLE"})
            continue
        clean = _clean_wikitext(page.read_text(encoding="utf-8"))
        chinese = len(re.findall(r"[\u3400-\u9fff]", clean))
        if chinese < 80 or chinese / max(1, len(clean)) < 0.12:
            excluded.append({"page_id": page_id, "title": title, "reason": "LOW_CHINESE_CONTENT"})
            continue
        relevance = [term for term in TOPIC_TERMS if term in f"{title} {clean}"]
        if not relevance:
            excluded.append({"page_id": page_id, "title": title, "reason": "LOW_TOPIC_RELEVANCE"})
            continue
        permalink = str(metadata.get("canonicalurl") or metadata.get("fullurl") or "").strip()
        revision_id = str(metadata.get("revid", "")).strip()
        for number, text in enumerate(_chunks(clean), 1):
            digest = sha256(text)
            if digest in seen:
                continue
            seen.add(digest)
            candidates.append(
                {
                    "id": f"zhwiki-{page_id}-{number}",
                    "title": title,
                    "entities": relevance,
                    "text": text,
                    "source_id": f"zhwiki-{page_id}",
                    "language": "zh",
                    "license": "CC-BY-SA-4.0",
                    "license_verified": True,
                    "license_evidence": "中文维基百科页面许可声明；研究分区保留署名与相同方式共享要求。",
                    "url": permalink,
                    "permalink": permalink,
                    "revision_id": revision_id,
                    "review_status": "NEEDS_REVIEW",
                    "expert_reviewed": False,
                    "production_enabled": False,
                    "text_sha256": digest,
                }
            )
    report = {
        "version": "zh-filter-v1",
        "status": "RESEARCH_CANDIDATES_ONLY",
        "selected_chunks": len(candidates),
        "selected_pages": len({row["source_id"] for row in candidates}),
        "excluded_pages": excluded,
        "counts": dict(Counter(row.get("reason", "SELECTED") for row in excluded)),
        "license_gate": "NEEDS_REVIEW_RESEARCH_ONLY",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {"version": "zh-filter-v1", "candidates": candidates}, ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    (destination.parent / "zh-filter-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
