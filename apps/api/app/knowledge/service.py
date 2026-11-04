from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from uuid import uuid4

from app.knowledge.governance import load_bundle
from app.knowledge.indexes import (
    KnowledgeIndexes,
    index_identity,
    lexical_search,
    reciprocal_rank_fusion,
)
from app.knowledge.provider import ModelProvider, ProviderDisabled, ProviderError

logger = logging.getLogger(__name__)


class KnowledgeService:
    def __init__(self, settings, provider=None, indexes=None):
        self.settings = settings
        self.provider = provider or ModelProvider(settings)
        self.indexes = indexes or KnowledgeIndexes(settings)

    async def ingest(self) -> dict:
        bundle = load_bundle(self.settings.knowledge_bundle, self.settings.knowledge_mode)
        if not bundle["chunks"]:
            return {"status": "blocked", "reason": "NO_APPROVED_CHUNKS"}
        if not self.settings.model_api_key:
            return {
                "status": "disabled",
                "reason": "MODEL_API_KEY_REQUIRED",
                "chunks": len(bundle["chunks"]),
            }
        vectors = []
        for start in range(0, len(bundle["chunks"]), 16):
            vectors.extend(
                await self.provider.embed(
                    [row["text"] for row in bundle["chunks"][start : start + 16]]
                )
            )
        vector_manifest = await asyncio.to_thread(self.indexes.vector_load, bundle, vectors)
        await asyncio.to_thread(self.indexes.graph_load, bundle)
        manifest = {
            **vector_manifest,
            "index_identity": index_identity(bundle, self.settings),
            "fingerprint": bundle["fingerprint"],
            "mode": bundle["mode"],
            "embedding_model": self.settings.embedding_model,
            "status": "ready",
        }
        path = Path(self.settings.knowledge_index_manifest)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f".{uuid4().hex}.tmp")
        try:
            temporary.write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
        return manifest

    async def answer(self, question: str, skill: dict | None = None) -> dict:
        if any(
            word in question
            for word in ("用药", "吃什么药", "诊断疾病", "声带结节", "嗓子疼", "喉咙痛")
        ):
            return {
                "status": "refused",
                "reason": "HEALTH_BOUNDARY",
                "answer": "无法用知识问答或录音诊断疾病。出现不适请停止发声训练，寻求专业帮助。",
                "citations": [],
            }
        try:
            bundle = load_bundle(self.settings.knowledge_bundle, self.settings.knowledge_mode)
        except (OSError, ValueError, KeyError):
            return {
                "status": "disabled",
                "reason": "PREPARE_REVIEWED_CORPUS_FIRST",
                "citations": [],
            }
        by_id = {row["id"]: row for row in bundle["chunks"]}
        lexical = lexical_search(bundle, question)
        if not lexical and not self.settings.model_api_key:
            return {
                "status": "refused",
                "reason": "INSUFFICIENT_EVIDENCE",
                "answer": "当前语料没有足够证据回答这个问题。",
                "citations": [],
            }
        # No fake embeddings or mock generative answers when the key is absent.
        if not self.settings.model_api_key:
            return {
                "status": "disabled",
                "reason": "MODEL_API_KEY_REQUIRED",
                "mode": bundle["mode"],
                "answer": None,
                "evidence_preview": [
                    self.citation(by_id[key], i) for i, key in enumerate(lexical[:3], 1)
                ],
                "citations": [],
            }
        try:
            if not Path(self.settings.knowledge_index_manifest).exists():
                await self.ingest()
            vector = (await self.provider.embed([question]))[0]
            vector_ids = await asyncio.to_thread(self.indexes.vector_search, bundle, vector)
            graph_ids = await asyncio.to_thread(self.indexes.graph_search, bundle, question)
            candidates = [
                by_id[key]
                for key in reciprocal_rank_fusion(lexical, vector_ids, graph_ids)
                if key in by_id
            ][:12]
            if not candidates:
                return {"status": "refused", "reason": "INSUFFICIENT_EVIDENCE", "citations": []}
            ranking = await self.provider.rerank(question, [row["text"] for row in candidates])
            if ranking[0][1] < 0.35:
                return {"status": "refused", "reason": "LOW_RELEVANCE", "citations": []}
            evidence = [candidates[index] for index, score in ranking if score >= 0.35][:5]
            answer = (
                await self.provider.answer(question, evidence, skill)
                if skill
                else await self.provider.answer(question, evidence)
            )
            references = [int(value) for value in re.findall(r"\[(\d+)\]", answer)]
            if not references or any(value < 1 or value > len(evidence) for value in references):
                return {
                    "status": "refused",
                    "reason": "INVALID_GENERATED_CITATIONS",
                    "citations": [],
                }
            return {
                "status": "answered",
                "answer": answer,
                "mode": bundle["mode"],
                "review_notice": "项目研究资料尚未经声乐专家审核；不能视为专业教学或医疗意见。"
                if bundle["mode"] == "research"
                else None,
                "citations": [self.citation(row, i) for i, row in enumerate(evidence, 1)],
                "retrieval": {
                    "mode": "lexical+milvus+neo4j+reranker",
                    "version": bundle["version"],
                },
            }
        except ProviderDisabled:
            return {"status": "disabled", "reason": "MODEL_API_KEY_REQUIRED", "citations": []}
        except ProviderError:
            return {"status": "unavailable", "reason": "MODEL_PROVIDER_FAILED", "citations": []}
        except Exception:  # noqa: BLE001 -- adapter boundary returns a redacted public error
            logger.warning("Knowledge index request failed")
            return {
                "status": "unavailable",
                "reason": "KNOWLEDGE_INDEX_UNAVAILABLE",
                "citations": [],
            }

    @staticmethod
    def citation(row: dict, number: int) -> dict:
        return {
            "number": number,
            "chunk_id": row["id"],
            "title": row["title"],
            "url": row["url"],
            "text": row["text"],
            "text_sha256": row["text_sha256"],
            "license": row["license"],
            "review_status": row["review_status"],
        }
