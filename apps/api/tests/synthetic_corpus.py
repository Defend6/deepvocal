"""Tiny invented fixtures for contract tests, unrelated to the private research corpus."""

import json
from pathlib import Path


def create_corpus(root: Path) -> Path:
    original = root / "project-original"
    original.mkdir(parents=True, exist_ok=True)
    entries = [
        {
            "id": "pitch",
            "title": "合成测试：音准",
            "text": "音准测试占位：音高与目标参考音符比较。",
            "entities": ["音准"],
        },
        {
            "id": "breath",
            "title": "合成测试：气息",
            "text": "气息测试占位：这是一条用于检索校验的虚构资料。",
            "entities": ["气息"],
        },
        {
            "id": "rhythm",
            "title": "合成测试：节奏",
            "text": "节奏测试占位：节拍与时间位置用于接口测试。",
            "entities": ["节奏"],
        },
    ]
    (original / "knowledge.json").write_text(
        json.dumps(entries, ensure_ascii=False), encoding="utf-8"
    )
    (root / "source-registry.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "synthetic-restricted-source",
                        "license": "CC-BY-NC-4.0",
                        "production_enabled": False,
                        "review_status": "NEEDS_REVIEW",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (root / "SUMMARY.json").write_text(
        json.dumps(
            {
                "version": "synthetic-test-only",
                "status": "TEST_ONLY",
                "document_count": 3,
                "chunk_count": 3,
                "entity_count": 3,
                "candidate_relation_count": 0,
            }
        ),
        encoding="utf-8",
    )
    return root


def evaluation_cases() -> dict:
    return {
        "version": "synthetic-smoke-v1",
        "scope": "SYNTHETIC_CONTRACT_TEST_ONLY",
        "retrieval": [
            {"id": "r1", "question": "什么是音准", "relevant": ["pitch"]},
            {"id": "r2", "question": "气息", "relevant": ["breath"]},
            {"id": "r3", "question": "节奏与节拍", "relevant": ["rhythm"]},
        ],
        "guardrails": [
            {"id": "g1", "question": "嗓子疼吃什么药", "expected": "HEALTH_BOUNDARY"},
            {"id": "g2", "question": "什么是音准", "expected": "MODEL_API_KEY_REQUIRED"},
            {"id": "g3", "question": "不存在的量子香蕉", "expected": "INSUFFICIENT_EVIDENCE"},
        ],
    }
