import json

from app.knowledge.filter import filter_chinese_corpus
from app.knowledge.governance import load_bundle, prepare


def test_filter_wiki_pages_is_repeatable_and_keeps_research_license_metadata(tmp_path):
    raw = tmp_path / "zh-corpus-20261103" / "raw" / "zhwiki" / "1"
    raw.mkdir(parents=True)
    (raw / "metadata.json").write_text(
        json.dumps(
            {
                "pageid": 1,
                "title": "音准",
                "canonicalurl": "https://zh.wikipedia.org/wiki/%E9%9F%B3%E5%87%86",
                "revid": 123,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    (raw / "source.wiki").write_text(
        "== 音准 ==\n" + "音准是歌唱中音高与目标音符的关系。" * 30,
        encoding="utf-8",
    )
    destination = tmp_path / "working" / "zh-filtered-candidates.json"
    first = filter_chinese_corpus(tmp_path, destination)
    second = filter_chinese_corpus(tmp_path, destination)
    assert first["selected_chunks"] == second["selected_chunks"] == 1
    row = json.loads(destination.read_text(encoding="utf-8"))["candidates"][0]
    assert row["license"] == "CC-BY-SA-4.0"
    assert row["review_status"] == "NEEDS_REVIEW"


def test_prepare_imports_filtered_research_candidates_but_not_production(tmp_path):
    root = tmp_path / "corpus"
    (root / "project-original").mkdir(parents=True)
    (root / "working").mkdir()
    (root / "project-original" / "knowledge.json").write_text("[]", encoding="utf-8")
    (root / "source-registry.json").write_text('{"sources": []}', encoding="utf-8")
    from app.knowledge.filter import sha256

    text = "音准是歌唱中音高与目标音符的关系。" * 10
    row = {
        "id": "zhwiki-1-1",
        "title": "音准",
        "entities": ["音准"],
        "text": text,
        "source_id": "zhwiki-1",
        "language": "zh",
        "license": "CC-BY-SA-4.0",
        "license_verified": True,
        "license_evidence": "snapshot",
        "url": "https://example.test/1",
        "review_status": "NEEDS_REVIEW",
        "expert_reviewed": False,
        "production_enabled": False,
        "text_sha256": sha256(text),
    }
    (root / "working" / "zh-filtered-candidates.json").write_text(
        json.dumps({"candidates": [row]}, ensure_ascii=False), encoding="utf-8"
    )
    destination = tmp_path / "bundle.json"
    audit = prepare(root, destination)
    assert audit["selected_chunks"] == 1
    assert load_bundle(destination, "research")["chunks"][0]["id"] == "zhwiki-1-1"


def test_prepare_imports_permitted_english_normalized_chunks(tmp_path):
    root = tmp_path / "corpus"
    (root / "project-original").mkdir(parents=True)
    (root / "normalized").mkdir()
    (root / "project-original" / "knowledge.json").write_text("[]", encoding="utf-8")
    (root / "source-registry.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "public-en",
                        "license": "CC0-1.0",
                        "license_evidence": "LICENSE.txt",
                        "url": "https://example.test/public-en",
                        "review_status": "NEEDS_REVIEW",
                        "production_enabled": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "normalized" / "documents.jsonl").write_text(
        json.dumps(
            {
                "document_id": "doc-en",
                "source_id": "public-en",
                "source_locator": "voice.md",
                "title": "Vocal resonance",
                "language": "en",
                "categories": ["VocalConcept"],
            }
        ),
        encoding="utf-8",
    )
    text = "Vocal resonance describes how the vocal tract shapes the sound." * 4
    (root / "normalized" / "chunks.jsonl").write_text(
        json.dumps(
            {
                "chunk_id": "chunk-en",
                "document_id": "doc-en",
                "source_id": "public-en",
                "language": "en",
                "text": text,
            }
        ),
        encoding="utf-8",
    )
    destination = tmp_path / "bundle.json"
    audit = prepare(root, destination)
    assert audit["selected_chunks"] == 1
    bundle = load_bundle(destination, "research")
    assert bundle["chunks"][0]["language"] == "en"
