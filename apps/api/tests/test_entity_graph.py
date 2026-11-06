import copy

import pytest

from app.knowledge.entities import (
    EntityLinker,
    alias_spans,
    compile_graph,
    graph_identity,
    protected_candidates,
    rank_graph_candidates,
)
from app.knowledge.governance import sha256


def example_bundle():
    text = "A fictional pitch class has a pitch. 音准与音高是不同的测试实体。"
    return {
        "fingerprint": "synthetic", "mode": "research",
        "chunks": [{"id": "test", "title": "Test pitch class", "text": text,
                    "text_sha256": sha256(text)}],
        "graph_catalog": {
            "version": "synthetic-only", "entities": [
                {"id": "pitch", "names": ["音高", "pitch"], "aliases": ["pitches"]},
                {"id": "intonation", "names": ["音准", "intonation"], "aliases": ["音準"]},
                {"id": "pitch_class", "names": ["音级", "pitch class"], "aliases": []},
            ], "relations": [],
        },
    }


def test_bilingual_case_width_boundaries_and_longest_phrase():
    linker = EntityLinker(example_bundle()["graph_catalog"])
    matches = linker.link("ＰＩＴＣＨ CLASS 音準 intonation PITCHER pitches", query=True)
    assert [m["id"] for m in matches] == ["pitch_class", "intonation", "pitch"]
    assert matches[0]["surface"] == "ＰＩＴＣＨ CLASS"
    assert alias_spans("pitcher", "pitch") == []
    assert alias_spans("pitch\n  class", "pitch class") == [(0, 13)]


def test_ambiguous_alias_requires_explicit_unambiguous_wording():
    catalog = example_bundle()["graph_catalog"]
    catalog["entities"][0]["aliases"].append("shared")
    catalog["entities"][1]["aliases"].append("shared")
    linker = EntityLinker(catalog)
    assert linker.link("shared") == []
    assert linker.link("音高 shared", query=True)[0]["id"] == "pitch"


def test_all_mentions_have_verifiable_source_offsets_and_no_title_only_entities():
    bundle = example_bundle()
    bundle["chunks"][0]["title"] += " imaginary"
    bundle["graph_catalog"]["entities"].append({"id": "imaginary", "names": ["imaginary"]})
    graph = compile_graph(bundle)
    assert "imaginary" not in graph["degrees"]
    assert all(row["grounded"] for row in graph["mentions"])
    import json

    text = bundle["chunks"][0]["text"]
    for mention in graph["mentions"]:
        for span in json.loads(mention["evidence"]):
            assert text[span["start"]:span["end"]] == span["surface"]


def test_unverified_relation_evidence_and_catalog_changes_cannot_silently_reuse_graph():
    bundle = example_bundle()
    original = graph_identity(bundle)
    relation = {"source": "intonation", "target": "pitch", "chunk_id": "test",
                "type": "DISTINCT_FROM", "start": 43, "end": 44,
                "evidence": "fabricated", "text_sha256": bundle["chunks"][0]["text_sha256"],
                "confidence": 0.9}
    bundle["graph_catalog"]["relations"].append(relation)
    assert graph_identity(bundle) != original
    with pytest.raises(ValueError, match="source evidence"):
        compile_graph(bundle)
    text = bundle["chunks"][0]["text"]
    start = text.index("音准")
    relation.update(start=start, end=len(text), evidence=text[start:])
    assert len(compile_graph(bundle)["relations"]) == 1
    changed = copy.deepcopy(bundle)
    changed["mode"] = "production"
    assert graph_identity(changed) != graph_identity(bundle)
    with pytest.raises(ValueError, match="actual expert approval"):
        compile_graph(changed)


def test_hubs_are_penalized_and_vector_anchors_survive_noisy_graph():
    bundle = example_bundle()
    bundle["chunks"].extend(
        {"id": f"hub-{i}", "title": "pitch", "text": "pitch", "text_sha256": sha256("pitch")}
        for i in range(15)
    )
    ranked = rank_graph_candidates(bundle, "intonation", [
        {"chunk_id": "hub-0", "entity_id": "pitch", "weight": 3},
        {"chunk_id": "test", "entity_id": "intonation", "weight": 3},
    ])
    assert ranked[0] == "test"
    vector = [f"v{i}" for i in range(12)]
    candidates = protected_candidates(vector, ["noise"], ["noise", "relation"])
    assert candidates[:8] == vector[:8]
    assert len(candidates) == len(set(candidates)) == 12
    assert "relation" in candidates


def test_excluded_source_is_not_misrepresented_as_vocal_entity_evidence():
    bundle = example_bundle()
    bundle["graph_catalog"]["excluded_chunk_ids"] = ["test"]
    assert compile_graph(bundle)["mentions"] == []
