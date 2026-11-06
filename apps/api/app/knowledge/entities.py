"""Source-grounded, versioned entity linking. Vocabulary is supplied by a local corpus."""

from __future__ import annotations

import json
import math
import re
import unicodedata
from collections import Counter, defaultdict
from functools import lru_cache

from app.knowledge.governance import sha256, terms

GRAPH_SCHEMA = "grounded-entities-v2-isolated"


def normalize(value: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value).casefold()).strip()


def alias_spans(text: str, alias: str) -> list[tuple[int, int]]:
    """Keep offsets in the original text, including case/width variants."""
    normalized, offsets = [], []
    for index, char in enumerate(text):
        for expanded in unicodedata.normalize("NFKC", char).casefold():
            normalized.append(expanded)
            offsets.append(index)
    needle = normalize(alias)
    if not needle:
        return []
    pattern = re.escape(needle).replace(r"\ ", r"\s+")
    if needle[0].isascii() and needle[0].isalnum():
        pattern = r"(?<![a-z0-9])" + pattern
    if needle[-1].isascii() and needle[-1].isalnum():
        pattern += r"(?![a-z0-9])"
    haystack = "".join(normalized)
    return [(offsets[m.start()], offsets[m.end() - 1] + 1) for m in re.finditer(pattern, haystack)]


def catalog_for(bundle: dict) -> dict:
    catalog = bundle.get("graph_catalog")
    if catalog is not None:
        return catalog
    # Backwards compatible metadata mode for clean checkouts and existing bundles.
    names = sorted({name for row in bundle["chunks"] for name in row.get("entities", [])})
    return {
        "version": "legacy-metadata",
        "entities": [{"id": name, "names": [name], "aliases": []} for name in names],
        "relations": [],
    }


def graph_identity(bundle: dict) -> str:
    return sha256(
        json.dumps(
            [GRAPH_SCHEMA, bundle["fingerprint"], bundle["mode"], catalog_for(bundle)],
            sort_keys=True, ensure_ascii=False,
        )
    )


class EntityLinker:
    def __init__(self, catalog: dict):
        self.catalog = catalog
        self.entities = {}
        self.aliases: dict[str, set[str]] = defaultdict(set)
        for entity in catalog.get("entities", []):
            key = entity.get("id")
            aliases = entity.get("names", []) + entity.get("aliases", [])
            if not isinstance(key, str) or not key or key in self.entities or not aliases:
                raise ValueError("invalid or duplicate canonical entity")
            self.entities[key] = entity
            for alias in aliases:
                if not isinstance(alias, str) or not normalize(alias):
                    raise ValueError("invalid entity alias")
                self.aliases[normalize(alias)].add(key)
        self.ambiguous_aliases = {key for key, values in self.aliases.items() if len(values) > 1}

    def link(self, text: str, *, query: bool = False) -> list[dict]:
        candidates = []
        for alias, keys in self.aliases.items():
            # Ambiguous aliases must be resolved by explicit longer wording, never guessed.
            if len(keys) != 1:
                continue
            key = next(iter(keys))
            for start, end in alias_spans(text, alias):
                candidates.append({"id": key, "start": start, "end": end,
                                   "surface": text[start:end], "alias": alias})
        candidates.sort(key=lambda row: (-(row["end"] - row["start"]), row["start"], row["id"]))
        selected = []
        for row in candidates:
            # A longer phrase wins over a contained phrase (pitch class versus pitch).
            if any(row["start"] >= old["start"] and row["end"] <= old["end"] for old in selected):
                continue
            selected.append(row)
        selected.sort(key=lambda row: (row["start"], row["id"]))
        if query:
            seen = set()
            selected = [row for row in selected if not (row["id"] in seen or seen.add(row["id"]))]
            return selected[:8]
        return selected


def compile_graph(bundle: dict) -> dict:
    """Compile only observed mentions and explicitly supplied, evidence-checked relations."""
    return _compile_cached(json.dumps(bundle, sort_keys=True, ensure_ascii=False))


@lru_cache(maxsize=4)
def _compile_cached(serialized: str) -> dict:
    bundle = json.loads(serialized)
    catalog = catalog_for(bundle)
    if bundle["mode"] == "production" and "graph_catalog" in bundle and not (
        catalog.get("expert_reviewed") is True and catalog.get("production_enabled") is True
    ):
        raise ValueError("production graph catalog requires actual expert approval")
    linker = EntityLinker(catalog)
    legacy = catalog.get("version") == "legacy-metadata"
    mentions, by_id = [], {row["id"]: row for row in bundle["chunks"]}
    for row in bundle["chunks"]:
        if row["id"] in catalog.get("excluded_chunk_ids", []):
            continue
        found = linker.link(row["text"])
        title_ids = {item["id"] for item in linker.link(row["title"])}
        grouped = defaultdict(list)
        for match in found:
            grouped[match["id"]].append(match)
        if legacy:
            for name in row.get("entities", []):
                grouped.setdefault(name, [])
        for entity, matches in grouped.items():
            mentions.append({
                "chunk_id": row["id"], "entity_id": entity,
                "weight": 1.4 if entity in title_ids else 1.0,
                "evidence": json.dumps(matches[:8], ensure_ascii=False),
                "grounded": bool(matches),
            })
    observed = {(m["chunk_id"], m["entity_id"]) for m in mentions}
    relations = []
    for relation in catalog.get("relations", []):
        row = by_id.get(relation.get("chunk_id"))
        start, end = relation.get("start"), relation.get("end")
        if (
            row is None or relation.get("source") not in linker.entities
            or relation.get("target") not in linker.entities
            or relation["source"] == relation["target"]
            or not isinstance(start, int) or not isinstance(end, int)
            or not 0 <= start < end <= len(row["text"])
            or row["text"][start:end] != relation.get("evidence")
            or relation.get("text_sha256") != row["text_sha256"]
            or not re.fullmatch(r"[A-Z][A-Z_]{1,39}", relation.get("type", ""))
            or not 0 < relation.get("confidence", 0) <= 1
            or (row["id"], relation["source"]) not in observed
            or (row["id"], relation["target"]) not in observed
        ):
            raise ValueError("relation has invalid source evidence")
        evidence_ids = {match["id"] for match in linker.link(relation["evidence"])}
        if not {relation["source"], relation["target"]} <= evidence_ids:
            raise ValueError("relation endpoints are not grounded in the evidence span")
        relations.append({**relation, "key": sha256(json.dumps(relation, sort_keys=True))})
    degrees = Counter(m["entity_id"] for m in mentions)
    return {"identity": graph_identity(bundle), "mentions": mentions, "relations": relations,
            "entities": linker.entities, "degrees": dict(degrees), "legacy": legacy}


def rank_graph_candidates(bundle: dict, query: str, candidates: list[dict], *, limit: int = 12) -> list[str]:
    """Normalize hub weights; use source overlap only as a bounded relevance tie breaker."""
    graph = compile_graph(bundle)
    query_terms = terms(query)
    by_id = {row["id"]: row for row in bundle["chunks"]}
    scores = defaultdict(float)
    for match in candidates:
        key = match["chunk_id"]
        if key not in by_id:
            continue
        degree = max(1, graph["degrees"].get(match["entity_id"], 1))
        idf = math.log1p(len(by_id) / degree)
        scores[key] += float(match["weight"]) * idf
    for key in scores:
        overlap = len(query_terms & terms(by_id[key]["title"] + " " + by_id[key]["text"]))
        scores[key] *= 1 + 0.45 * overlap / max(1, len(query_terms))
    return sorted(scores, key=lambda key: (-scores[key], key))[:limit]


def protected_candidates(vector: list[str], lexical: list[str], graph: list[str], limit: int = 12) -> list[str]:
    """Reserve vector slots; allow graph to add candidates without displacing all anchors."""
    from app.knowledge.indexes import reciprocal_rank_fusion

    anchors = list(dict.fromkeys(vector))[: min(8, limit)]
    supplemental = reciprocal_rank_fusion(graph[:6], lexical[:4], vector)
    return (anchors + [key for key in supplemental if key not in anchors])[:limit]
