import asyncio
import json

import pytest

from app.config import Settings
from app.knowledge.governance import load_bundle, prepare
from app.knowledge.service import KnowledgeService


def test_clean_checkout_can_prepare_empty_bundle_without_paid_calls(tmp_path, monkeypatch):
    destination = tmp_path / "working" / "knowledge.json"
    root = tmp_path / "no-corpus"
    with pytest.raises(FileNotFoundError):
        prepare(root, destination)
    audit = prepare(root, destination, allow_missing=True)
    assert audit["corpus_available"] is False and audit["selected_chunks"] == 0
    assert load_bundle(destination, "research")["chunks"] == []

    async def forbidden(*args, **kwargs):
        raise AssertionError("empty corpus must not call a paid model")

    monkeypatch.setattr("app.knowledge.provider.ModelProvider.post", forbidden)
    settings = Settings(knowledge_bundle=str(destination), model_api_key="test-never-used")
    result = asyncio.run(KnowledgeService(settings).answer("什么是音准"))
    assert result["reason"] == "PREPARE_REVIEWED_CORPUS_FIRST"
    assert result["answer"] is None
    assert asyncio.run(KnowledgeService(settings).ingest())["reason"] == "NO_APPROVED_CHUNKS"


def test_allow_missing_does_not_hide_partial_or_corrupt_data(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    registry = root / "source-registry.json"
    registry.write_text('{"sources": []}', encoding="utf-8")
    destination = tmp_path / "working" / "knowledge.json"
    with pytest.raises(FileNotFoundError):
        prepare(root, destination, allow_missing=True)
    original = root / "project-original"
    original.mkdir()
    (original / "knowledge.json").write_text("broken-json", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        prepare(root, destination, allow_missing=True)
    assert not destination.exists()
