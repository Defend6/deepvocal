import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.config import Settings
from app.knowledge.governance import load_bundle, permitted, prepare, sha256
from app.knowledge.indexes import KnowledgeIndexes, index_identity, lexical_search
from app.knowledge.provider import ModelProvider, ProviderDisabled, ProviderError
from app.knowledge.service import KnowledgeService

ROOT = Path(__file__).resolve().parents[3] / "datasets"


def prepared(tmp_path):
    path = tmp_path / "knowledge.json"
    audit = prepare(ROOT, path)
    assert audit["selected_chunks"] == 12
    settings = Settings(
        knowledge_bundle=str(path), knowledge_index_manifest=str(tmp_path / "active.json")
    )
    return settings, load_bundle(path, "research")


def test_license_language_hash_and_production_gates(tmp_path):
    settings, bundle = prepared(tmp_path)
    row = bundle["chunks"][0]
    assert permitted(row, "research")
    assert not permitted(row, "production")
    assert not permitted({**row, "language": "en"}, "research")
    assert not permitted({**row, "license": "CC-BY-NC-4.0"}, "research")
    assert not permitted({**row, "text": "tampered"}, "research")
    bundle["chunks"][0]["text"] = "modified without review"
    Path(settings.knowledge_bundle).write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError):
        load_bundle(settings.knowledge_bundle, "research")


def test_key_missing_has_evidence_preview_not_fake_answer(tmp_path):
    settings, bundle = prepared(tmp_path)
    assert "pitch" in lexical_search(bundle, "什么是音准")
    result = asyncio.run(KnowledgeService(settings).answer("什么是音准"))
    assert result["status"] == "disabled" and result["reason"] == "MODEL_API_KEY_REQUIRED"
    assert result["answer"] is None and result["citations"] == []
    assert result["evidence_preview"]
    refused = asyncio.run(KnowledgeService(settings).answer("嗓子疼应该吃什么药"))
    assert refused["status"] == "refused"


def test_provider_http_contract_secret_redaction_and_vector_validation():
    requests = []

    class TestBudget:
        async def reserve(self, endpoint, body):
            return "isolated-http-contract-test"

        async def dispatch(self, call_id):
            pass

        async def settle(self, call_id, payload):
            pass

    budget = TestBudget()

    def respond(request):
        requests.append(request)
        assert request.headers["Authorization"] == "Bearer test-secret"
        body = json.loads(request.content)
        assert body["model"] == "BAAI/bge-m3"
        return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2]}]})

    settings = Settings(model_api_key="test-secret")
    assert asyncio.run(
        ModelProvider(settings, httpx.MockTransport(respond), budget=budget).embed(["test"])
    ) == [[0.1, 0.2]]
    assert len(requests) == 1
    with pytest.raises(ProviderDisabled):
        asyncio.run(ModelProvider(Settings()).embed(["test"]))
    bad = ModelProvider(
        settings,
        httpx.MockTransport(lambda _: httpx.Response(200, json={"data": []})),
        budget=budget,
    )
    with pytest.raises(ProviderError):
        asyncio.run(bad.embed(["test"]))
    fail = ModelProvider(
        settings,
        httpx.MockTransport(lambda _: httpx.Response(401, text="secret-upstream-body")),
        budget=budget,
    )
    with pytest.raises(ProviderError) as exc:
        asyncio.run(fail.embed(["test"]))
    assert "test-secret" not in str(exc.value) and "upstream" not in str(exc.value)


def test_full_retrieval_citation_validation_with_test_provider(tmp_path):
    settings, _bundle = prepared(tmp_path)
    settings = settings.model_copy(update={"model_api_key": "test"})
    Path(settings.knowledge_index_manifest).write_text("{}")

    class FakeIndexes:
        def vector_search(self, _bundle, _vector):
            return ["pitch"]

        def graph_search(self, _bundle, _query):
            return ["pitch"]

    class FakeProvider:
        response = "音准需要参考目标音符。[1]"

        async def embed(self, texts):
            return [[0.1, 0.2] for _ in texts]

        async def rerank(self, query, documents):
            return [(0, 0.9)]

        async def answer(self, question, evidence):
            return self.response

    provider = FakeProvider()
    service = KnowledgeService(settings, provider, FakeIndexes())
    result = asyncio.run(service.answer("什么是音准"))
    assert result["status"] == "answered" and result["citations"][0]["chunk_id"] == "pitch"
    provider.response = "错误引用[999]"
    assert asyncio.run(service.answer("什么是音准"))["reason"] == "INVALID_GENERATED_CITATIONS"


def test_real_neo4j_milvus_index_round_trip_with_synthetic_test_vectors(tmp_path):
    if os.getenv("TEST_KNOWLEDGE_INDEXES") != "1":
        pytest.skip("requires Neo4j and Milvus")
    settings, bundle = prepared(tmp_path)
    bundle["fingerprint"] = sha256(str(uuid4()))
    bundle["chunks"] = bundle["chunks"][:2]
    bundle["chunks"][0]["entities"] = ["音准", "test-shared-entity"]
    bundle["chunks"][1]["entities"] = ["test-shared-entity"]
    indexes = KnowledgeIndexes(settings)
    manifest = None
    try:
        indexes.graph_load(bundle)
        indexes.graph_load(bundle)
        assert "pitch" in indexes.graph_search(bundle, "音准")
        assert bundle["chunks"][1]["id"] in indexes.graph_search(bundle, "音准")
        manifest = indexes.vector_load(bundle, [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0]])
        Path(settings.knowledge_index_manifest).write_text(
            json.dumps({**manifest, "index_identity": index_identity(bundle, settings)})
        )
        assert indexes.vector_search(bundle, [1.0, 0.0, 0.0, 0.0])[0] == "pitch"
        settings.embedding_model = "different-model"
        with pytest.raises(ValueError, match="rebuild"):
            indexes.vector_search(bundle, [1.0, 0.0, 0.0, 0.0])
    finally:
        from neo4j import GraphDatabase
        from pymilvus import MilvusClient

        with GraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_user, settings.neo4j_password)
        ) as driver:
            driver.execute_query(
                "MATCH (c:DVChunk {fingerprint:$fp}) DETACH DELETE c", fp=bundle["fingerprint"]
            )
        if manifest:
            client = MilvusClient(uri=settings.milvus_uri)
            client.drop_collection(manifest["collection"])
            client.close()
