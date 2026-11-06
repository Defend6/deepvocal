from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from app.knowledge.entities import (
    EntityLinker,
    catalog_for,
    compile_graph,
    graph_identity,
    rank_graph_candidates,
)
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
        if manifest.get("graph_identity") != graph_identity(bundle):
            raise ValueError("graph schema or catalog changed; rebuild graph index")
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
                "MATCH (c:DVKnowledgeChunk {fingerprint:$fp,mode:$mode,graph_identity:$gid}) "
                "RETURN collect(c.chunk_id) AS ids",
                fp=bundle["fingerprint"],
                mode=bundle["mode"],
                gid=graph_identity(bundle),
            )
            stats, _, _ = driver.execute_query(
                "MATCH (c:DVKnowledgeChunk {fingerprint:$fp,mode:$mode,graph_identity:$gid})"
                "-[r:MENTIONS]->(e:DVEntity) "
                "RETURN count(r) AS mentions,count(DISTINCT e) AS entities,"
                "collect([c.chunk_id,e.name]) AS pairs",
                fp=bundle["fingerprint"],
                mode=bundle["mode"],
                gid=graph_identity(bundle),
            )
            relations, _, _ = driver.execute_query(
                "MATCH (r:DVRelation {graph_identity:$gid}) RETURN collect(r.key) AS keys",
                gid=graph_identity(bundle),
            )
        if set(graph[0]["ids"]) != expected:
            raise ValueError("Neo4j ids differ from the knowledge bundle")
        compiled = compile_graph(bundle)
        if {tuple(pair) for pair in stats[0]["pairs"]} != {
            (row["chunk_id"], row["entity_id"]) for row in compiled["mentions"]
        }:
            raise ValueError("Neo4j entity mentions differ from the source-grounded graph")
        if set(relations[0]["keys"]) != {
            f"{graph_identity(bundle)}:{row['key']}" for row in compiled["relations"]
        }:
            raise ValueError("Neo4j relation evidence differs from the graph catalog")
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
            "graph_identity": graph_identity(bundle),
            "graph_relations": len(relations[0]["keys"]),
        }

    def graph_load(self, bundle: dict) -> None:
        from neo4j import GraphDatabase

        graph = compile_graph(bundle)
        gid = graph["identity"]
        with GraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        ) as driver:
            driver.execute_query(
                "CREATE CONSTRAINT dv_knowledge_chunk_key IF NOT EXISTS FOR (c:DVKnowledgeChunk) REQUIRE c.key IS UNIQUE"
            )
            driver.execute_query(
                "CREATE CONSTRAINT dv_entity_name IF NOT EXISTS FOR (e:DVEntity) REQUIRE e.name IS UNIQUE"
            )
            driver.execute_query(
                "CREATE CONSTRAINT dv_relation_key IF NOT EXISTS FOR (r:DVRelation) REQUIRE r.key IS UNIQUE"
            )
            rows = [
                {
                    "key": f"{gid}:{row['id']}",
                    "id": row["id"],
                    "fingerprint": bundle["fingerprint"],
                    "mode": bundle["mode"],
                }
                for row in bundle["chunks"]
            ]
            def write_snapshot(tx):
                tx.run(
                    """
                    UNWIND $rows AS row MERGE (c:DVKnowledgeChunk {key: row.key})
                    SET c.chunk_id=row.id,c.fingerprint=row.fingerprint,c.mode=row.mode,c.graph_identity=$gid
                    """, rows=rows, gid=gid,
                ).consume()
                tx.run(
                    """
                    UNWIND $entities AS row MERGE (e:DVEntity {name:row.id})
                    SET e.names=row.names,e.aliases=row.aliases
                    """,
                    entities=[{"id": key, "names": entity.get("names", []),
                               "aliases": entity.get("aliases", [])}
                              for key, entity in graph["entities"].items()
                              if key in graph["degrees"]],
                ).consume()
                tx.run(
                    """
                    UNWIND $mentions AS row MATCH (c:DVKnowledgeChunk {key:$gid+':'+row.chunk_id})
                    MERGE (e:DVEntity {name:row.entity_id}) MERGE (c)-[m:MENTIONS]->(e)
                    SET m.weight=row.weight,m.evidence=row.evidence,m.grounded=row.grounded
                    """, mentions=graph["mentions"], gid=gid,
                ).consume()
                tx.run(
                    """
                    UNWIND $relations AS row
                    MATCH (c:DVKnowledgeChunk {key:$gid+':'+row.chunk_id})
                    MATCH (s:DVEntity {name:row.source}), (t:DVEntity {name:row.target})
                    MERGE (r:DVRelation {key:$gid+':'+row.key})
                    SET r.graph_identity=$gid,r.kind=row.type,r.evidence=row.evidence,
                        r.start=row.start,r.end=row.end,r.confidence=row.confidence,
                        r.text_sha256=row.text_sha256
                    MERGE (r)-[:FROM]->(s) MERGE (r)-[:TO]->(t) MERGE (r)-[:SOURCE]->(c)
                    """, relations=graph["relations"], gid=gid,
                ).consume()

            with driver.session() as session:
                session.execute_write(write_snapshot)

    def graph_search(
        self, bundle: dict, query: str, seed_ids: list[str] | None = None, *, expansion: bool = True
    ) -> list[str]:
        from neo4j import GraphDatabase

        graph = compile_graph(bundle)
        entities = [row["id"] for row in EntityLinker(catalog_for(bundle)).link(query, query=True)]
        if not entities and not seed_ids:
            return []
        with GraphDatabase.driver(
            self.settings.neo4j_uri, auth=(self.settings.neo4j_user, self.settings.neo4j_password)
        ) as driver:
            records, _, _ = driver.execute_query(
                """
                CALL () {
                    MATCH (c:DVKnowledgeChunk {graph_identity:$gid})-[m:MENTIONS]->(e:DVEntity)
                    WHERE e.name IN $entities
                    RETURN c.chunk_id AS chunk_id,e.name AS entity_id,3.0*m.weight AS weight
                    UNION ALL
                    MATCH (seed:DVEntity)<-[:MENTIONS]-(bridge:DVKnowledgeChunk)-[:MENTIONS]->(related:DVEntity)<-[:MENTIONS]-(c:DVKnowledgeChunk)
                    WHERE $expand AND seed.name IN $entities AND seed<>related
                        AND bridge.graph_identity=$gid AND c.graph_identity=$gid
                        AND related.name IN $specific
                    RETURN DISTINCT c.chunk_id AS chunk_id,related.name AS entity_id,0.15 AS weight
                    UNION ALL
                    MATCH (s:DVEntity)<-[:FROM|TO]-(r:DVRelation)-[:FROM|TO]->(t:DVEntity)
                    MATCH (c:DVKnowledgeChunk {graph_identity:$gid})-[m:MENTIONS]->(t)
                    WHERE $expand AND s.name IN $entities AND s<>t AND r.graph_identity=$gid
                    RETURN DISTINCT c.chunk_id AS chunk_id,t.name AS entity_id,0.8*m.weight AS weight
                    UNION ALL
                    MATCH (anchor:DVKnowledgeChunk {graph_identity:$gid})-[:MENTIONS]->(e:DVEntity)
                    MATCH (c:DVKnowledgeChunk {graph_identity:$gid})-[m:MENTIONS]->(e)
                    WHERE $expand AND anchor.chunk_id IN $seed_ids AND e.name IN $specific
                    RETURN DISTINCT c.chunk_id AS chunk_id,e.name AS entity_id,0.2*m.weight AS weight
                }
                RETURN chunk_id,entity_id,sum(weight) AS weight
            """,
                gid=graph["identity"], entities=entities, seed_ids=(seed_ids or [])[:2],
                expand=expansion,
                specific=[key for key, degree in graph["degrees"].items() if degree <= 12],
            )
            return rank_graph_candidates(bundle, query, [dict(row) for row in records])

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
