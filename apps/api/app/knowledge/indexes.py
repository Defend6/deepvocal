from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.knowledge.governance import sha256, terms


def index_identity(bundle: dict, settings) -> str:
    revision = f"|{settings.embedding_model_revision}" if settings.embedding_model_revision else ""
    return sha256(
        f"{bundle['fingerprint']}|{bundle['mode']}|"
        f"{settings.embedding_api_base or settings.model_api_base}|{settings.embedding_model}{revision}"
    )[:24]


class KnowledgeIndexes:
    def __init__(self, settings):
        self.settings = settings

    def verify(self, bundle: dict) -> dict:
        """Check stored ids against the governed bundle, without altering either database."""
        from neo4j import GraphDatabase
        from pymilvus import MilvusClient

        manifest = json.loads(
            Path(self.settings.knowledge_index_manifest).read_text(encoding="utf-8")
        )
        if manifest.get("index_identity") != index_identity(bundle, self.settings):
            raise ValueError("embedding model or corpus changed; rebuild indexes")
        expected = {row["id"] for row in bundle["chunks"]}
        client = MilvusClient(uri=self.settings.milvus_uri)
        try:
            records = client.query(
                collection_name=manifest["collection"],
                filter="",
                output_fields=["id"],
                limit=len(expected) + 1,
                consistency_level="Strong",
            )
        finally:
            client.close()
        if {row["id"] for row in records} != expected:
            raise ValueError("Milvus ids differ from the knowledge bundle")
        with GraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        ) as driver:
            graph, _, _ = driver.execute_query(
                "MATCH (c:DVChunk {fingerprint:$fp,mode:$mode}) RETURN collect(c.chunk_id) AS ids",
                fp=bundle["fingerprint"],
                mode=bundle["mode"],
            )
            stats, _, _ = driver.execute_query(
                "MATCH (c:DVChunk {fingerprint:$fp,mode:$mode})-[r:MENTIONS]->(e:DVEntity) "
                "RETURN count(r) AS mentions,count(DISTINCT e) AS entities",
                fp=bundle["fingerprint"],
                mode=bundle["mode"],
            )
        if set(graph[0]["ids"]) != expected:
            raise ValueError("Neo4j ids differ from the knowledge bundle")
        return {
            "status": "verified",
            "mode": bundle["mode"],
            "fingerprint": bundle["fingerprint"],
            "collection": manifest["collection"],
            "dimension": manifest["dimension"],
            "milvus_chunks": len(records),
            "neo4j_chunks": len(graph[0]["ids"]),
            "graph_entities": stats[0]["entities"],
            "graph_mentions": stats[0]["mentions"],
        }

    def graph_load(self, bundle: dict) -> None:
        from neo4j import GraphDatabase

        with GraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        ) as driver:
            driver.execute_query(
                "CREATE CONSTRAINT dv_chunk_key IF NOT EXISTS FOR (c:DVChunk) REQUIRE c.key IS UNIQUE"
            )
            driver.execute_query(
                "CREATE CONSTRAINT dv_entity_name IF NOT EXISTS FOR (e:DVEntity) REQUIRE e.name IS UNIQUE"
            )
            rows = [
                {
                    "key": f"{bundle['fingerprint']}:{row['id']}",
                    "id": row["id"],
                    "entities": row.get("entities", []),
                    "fingerprint": bundle["fingerprint"],
                    "mode": bundle["mode"],
                }
                for row in bundle["chunks"]
            ]
            driver.execute_query(
                """
                UNWIND $rows AS row MERGE (c:DVChunk {key: row.key})
                SET c.chunk_id=row.id,c.fingerprint=row.fingerprint,c.mode=row.mode
                WITH c,row UNWIND row.entities AS name
                MERGE (e:DVEntity {name:name}) MERGE (c)-[:MENTIONS]->(e)
            """,
                rows=rows,
            )

    def graph_search(self, bundle: dict, query: str) -> list[str]:
        from neo4j import GraphDatabase

        with GraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        ) as driver:
            entities = list(
                {
                    entity
                    for row in bundle["chunks"]
                    for entity in row.get("entities", [])
                    if entity in query
                }
            )
            if not entities:
                return []
            records, _, _ = driver.execute_query(
                """
                CALL () {
                    MATCH (c:DVChunk)-[:MENTIONS]->(e:DVEntity)
                    WHERE c.fingerprint=$fingerprint AND c.mode=$mode AND e.name IN $entities
                    RETURN c.chunk_id AS id,3.0 AS score
                    UNION ALL
                    MATCH (seed:DVEntity)<-[:MENTIONS]-(bridge:DVChunk)-[:MENTIONS]->(related:DVEntity)<-[:MENTIONS]-(c:DVChunk)
                    WHERE seed.name IN $entities AND seed<>related
                        AND bridge.fingerprint=$fingerprint AND bridge.mode=$mode
                        AND c.fingerprint=$fingerprint AND c.mode=$mode
                    RETURN c.chunk_id AS id,1.0 AS score
                }
                RETURN id,sum(score) AS score ORDER BY score DESC,id LIMIT 12
            """,
                fingerprint=bundle["fingerprint"],
                mode=bundle["mode"],
                entities=entities,
            )
            return [row["id"] for row in records]

    def vector_load(self, bundle: dict, vectors: list[list[float]]) -> dict:
        from pymilvus import MilvusClient

        name = "dv_" + index_identity(bundle, self.settings)
        client = MilvusClient(uri=self.settings.milvus_uri)
        try:
            if not client.has_collection(name):
                client.create_collection(
                    name,
                    dimension=len(vectors[0]),
                    primary_field_name="id",
                    id_type="string",
                    max_length=128,
                    auto_id=False,
                    metric_type="COSINE",
                )
            client.upsert(
                name,
                [
                    {"id": row["id"], "vector": vector}
                    for row, vector in zip(bundle["chunks"], vectors, strict=True)
                ],
            )
            client.flush(name)
            return {"collection": name, "dimension": len(vectors[0]), "count": len(vectors)}
        finally:
            client.close()

    def vector_search(self, bundle: dict, vector: list[float]) -> list[str]:
        from pymilvus import MilvusClient

        manifest = json.loads(
            Path(self.settings.knowledge_index_manifest).read_text(encoding="utf-8")
        )
        if manifest.get("index_identity") != index_identity(bundle, self.settings):
            raise ValueError("embedding model or corpus changed; rebuild indexes")
        if len(vector) != manifest["dimension"]:
            raise ValueError("query vector dimension mismatch")
        client = MilvusClient(uri=self.settings.milvus_uri)
        try:
            results = client.search(
                manifest["collection"],
                data=[vector],
                limit=12,
                search_params={"metric_type": "COSINE"},
            )
            return [row["id"] for row in results[0]]
        finally:
            client.close()


def lexical_search(bundle: dict, query: str) -> list[str]:
    query_terms = terms(query)
    scored = []
    for row in bundle["chunks"]:
        row_terms = terms(row["title"] + row["text"])
        overlap = len(query_terms & row_terms)
        if overlap:
            scored.append((row["id"], overlap / max(1, len(query_terms))))
    return [key for key, _ in sorted(scored, key=lambda pair: (-pair[1], pair[0]))[:12]]


def reciprocal_rank_fusion(*rankings: list[str]) -> list[str]:
    scores = defaultdict(float)
    for ranking in rankings:
        for rank, key in enumerate(dict.fromkeys(ranking), 1):
            scores[key] += 1 / (60 + rank)
    return sorted(scores, key=lambda key: (-scores[key], key))
