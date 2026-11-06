import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from synthetic_corpus import create_corpus

from app.config import Settings
from app.knowledge.entities import graph_identity
from app.knowledge.governance import load_bundle, permitted, prepare, sha256
from app.knowledge.indexes import KnowledgeIndexes, index_identity, lexical_search
from app.knowledge.provider import ModelProvider, ProviderDisabled, ProviderError
from app.knowledge.service import KnowledgeService


def prepared(tmp_path):
    path = tmp_path / "knowledge.json"
    root = create_corpus(tmp_path / "synthetic-corpus")
    audit = prepare(root, path)
    assert audit["selected_chunks"] == 3
    assert audit["counts"]["BLOCKED"] == 1
    settings = Settings(
        knowledge_bundle=str(path), knowledge_index_manifest=str(tmp_path / "active.json")
    )
    return settings, load_bundle(path, "research")


def test_license_language_hash_and_production_gates(tmp_path):
    settings, bundle = prepared(tmp_path)
    row = bundle["chunks"][0]
    assert permitted(row, "research")
    assert not permitted(row, "production")
    assert permitted({**row, "language": "en"}, "research")
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


def test_prepare_preserves_local_catalog_and_rejects_unversioned_alias_edits(tmp_path):
    settings, _bundle = prepared(tmp_path)
    root = tmp_path / "synthetic-corpus"
    (root / "working").mkdir()
    catalog = {"version": "synthetic", "entities": [
        {"id": "intonation", "names": ["音准", "intonation"], "aliases": []}
    ], "relations": []}
    (root / "working" / "entity-catalog.json").write_text(json.dumps(catalog), encoding="utf-8")
    prepare(root, Path(settings.knowledge_bundle))
    bundle = load_bundle(settings.knowledge_bundle, "research")
    assert bundle["graph_catalog"] == catalog
    bundle["graph_catalog"]["entities"][0]["aliases"].append("unreviewed")
    Path(settings.knowledge_bundle).write_text(json.dumps(bundle), encoding="utf-8")
    with pytest.raises(ValueError, match="catalog fingerprint"):
        load_bundle(settings.knowledge_bundle, "research")


def test_graph_rebuild_does_not_publish_manifest_when_database_verification_fails(tmp_path, monkeypatch):
    from app.knowledge import cli

    settings, _bundle = prepared(tmp_path)
    path = Path(settings.knowledge_index_manifest)
    original = '{"status": "previously-verified"}'
    path.write_text(original, encoding="utf-8")

    class FailingIndexes:
        def __init__(self, _settings):
            pass

        def graph_load(self, _bundle):
            pass

        def verify(self, _bundle):
            raise ValueError("synthetic database verification failure")

    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "KnowledgeIndexes", FailingIndexes)
    monkeypatch.setattr("sys.argv", ["knowledge", "rebuild-graph"])
    with pytest.raises(ValueError, match="verification failure"):
        cli.main()
    assert path.read_text(encoding="utf-8") == original
    assert not list(tmp_path.glob("*.graph.tmp"))


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


def test_local_embedding_and_reranking_bypass_remote_budget_and_translate_contracts():
    requests = []

    class ForbiddenBudget:
        async def reserve(self, *_args, **_kwargs):
            raise AssertionError("local model requests must not reserve remote spend")

    def respond(request):
        requests.append(request)
        assert "Authorization" not in request.headers
        body = json.loads(request.content)
        if request.url.path == "/v1/embeddings":
            assert body["model"] == "bge-m3"
            return httpx.Response(200, json={"data": [{"index": 0, "embedding": [0.1, 0.2, 0.3]}]})
        assert request.url.path == "/rerank"
        assert body == {"query": "breath support", "texts": ["wrong", "right"], "raw_scores": False}
        return httpx.Response(200, json=[{"index": 1, "score": 0.91}, {"index": 0, "score": 0.08}])

    settings = Settings(
        embedding_model="bge-m3",
        embedding_api_base="http://ollama/v1",
        reranker_api_base="http://tei",
    )
    provider = ModelProvider(settings, httpx.MockTransport(respond), budget=ForbiddenBudget())
    assert asyncio.run(provider.embed(["唱歌时怎样安排呼吸练习"])) == [[0.1, 0.2, 0.3]]
    assert asyncio.run(provider.rerank("breath support", ["wrong", "right"])) == [
        (1, 0.91),
        (0, 0.08),
    ]
    assert [request.url.path for request in requests] == ["/v1/embeddings", "/rerank"]


def test_full_retrieval_citation_validation_with_test_provider(tmp_path):
    settings, _bundle = prepared(tmp_path)
    settings = settings.model_copy(update={"model_api_key": "test"})
    Path(settings.knowledge_index_manifest).write_text("{}")

    class FakeIndexes:
        def vector_search(self, _bundle, _vector):
            return ["pitch"]

        def graph_search(self, _bundle, _query, seed_ids=None):
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
            json.dumps({**manifest, "index_identity": index_identity(bundle, settings),
                        "graph_identity": graph_identity(bundle)})
        )
        assert indexes.vector_search(bundle, [1.0, 0.0, 0.0, 0.0])[0] == "pitch"
        verification = indexes.verify(bundle)
        assert verification["milvus_chunks"] == verification["neo4j_chunks"] == 2
        assert verification["graph_mentions"] == 3
        settings.embedding_model_revision = "different-weights"
        with pytest.raises(ValueError, match="rebuild"):
            indexes.vector_search(bundle, [1.0, 0.0, 0.0, 0.0])
        settings.embedding_model_revision = ""
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
                "MATCH (c:DVKnowledgeChunk {fingerprint:$fp}) DETACH DELETE c", fp=bundle["fingerprint"]
            )
        if manifest:
            client = MilvusClient(uri=settings.milvus_uri)
            client.drop_collection(manifest["collection"])
            client.close()
