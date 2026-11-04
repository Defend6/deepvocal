import json

import pytest

from app.knowledge.corpus import CorpusNotFoundError, CorpusStatusService


def test_summary_only_image_does_not_require_raw_corpus(tmp_path):
    (tmp_path / "SUMMARY.json").write_text(
        json.dumps(
            {
                "document_count": 35,
                "chunk_count": 166,
                "entity_count": 18,
                "candidate_relation_count": 9,
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "source-registry.json").write_text('{"sources": []}', encoding="utf-8")
    status = CorpusStatusService(tmp_path).status()
    assert status["document_count"] == 35 and status["chunk_count"] == 166
    assert status["production_enabled"] is False


def test_invalid_metadata_and_absent_fallback_are_controlled(tmp_path):
    summary = tmp_path / "SUMMARY.json"
    (tmp_path / "source-registry.json").write_text('{"sources": []}', encoding="utf-8")
    for content in ("{", "[]", '{"document_count": -1}', "{}"):
        summary.write_text(content, encoding="utf-8")
        with pytest.raises(CorpusNotFoundError):
            CorpusStatusService(tmp_path).status()
