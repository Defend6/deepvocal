"""PostgreSQL vector adapter; isolated tables keyed by corpus/model identity."""
from __future__ import annotations

import math

import psycopg
from psycopg import sql


def vector_literal(vector: list[float]) -> str:
    if not vector or len(vector) > 2000 or any(
        not isinstance(v, (float, int)) or not math.isfinite(v) for v in vector
    ) or not any(vector):
        raise ValueError("expected finite nonzero vector with 1..2000 dimensions")
    return "[" + ",".join(str(float(v)) for v in vector) + "]"


class PgVectorIndex:
    def __init__(self, settings):
        self.settings = settings
        self.dsn = (settings.pgvector_url or settings.database_url).replace(
            "postgresql+asyncpg://", "postgresql://", 1
        )

    def connect(self):
        return psycopg.connect(self.dsn, connect_timeout=10, autocommit=True,
                               options="-c statement_timeout=15000")

    def load(self, bundle: dict, vectors: list[list[float]]) -> dict:
        from app.knowledge.indexes import index_identity

        if not vectors or len(vectors) != len(bundle["chunks"]):
            raise ValueError("vector/chunk count mismatch")
        dimension = len(vectors[0])
        values = [vector_literal(v) for v in vectors]
        if any(len(v) != dimension for v in vectors):
            raise ValueError("inconsistent vector dimensions")
        ids = [r["id"] for r in bundle["chunks"]]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate chunk ids")
        name = "dv_" + index_identity(bundle, self.settings) + f"_{dimension}"
        table = sql.Identifier("dv_vectors", name)
        with self.connect() as conn, conn.transaction():
            conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
            conn.execute("CREATE SCHEMA IF NOT EXISTS dv_vectors")
            conn.execute(sql.SQL("CREATE TABLE IF NOT EXISTS {} (id text PRIMARY KEY, embedding vector({}) NOT NULL)").format(table, sql.Literal(dimension)))
            with conn.cursor() as cursor:
                cursor.executemany(sql.SQL("INSERT INTO {} VALUES (%s, %s::vector) ON CONFLICT (id) DO UPDATE SET embedding=EXCLUDED.embedding").format(table), zip(ids, values, strict=True))
            conn.execute(sql.SQL("ANALYZE {}").format(table))
        return {"backend": "pgvector", "collection": name, "dimension": dimension, "count": len(ids)}

    @staticmethod
    def query(conn, manifest: dict, vector: list[float], limit: int = 12) -> list[str]:
        if not 1 <= limit <= 1000 or len(vector) != manifest["dimension"]:
            raise ValueError("invalid limit or query dimension mismatch")
        records = conn.execute(sql.SQL("SELECT id FROM {} ORDER BY embedding <=> %s::vector, id LIMIT %s").format(sql.Identifier("dv_vectors", manifest["collection"])), (vector_literal(vector), limit)).fetchall()
        return [row[0] for row in records]

    def search(self, manifest: dict, vector: list[float], limit: int = 12):
        with self.connect() as conn:
            return self.query(conn, manifest, vector, limit)

    def ids(self, manifest: dict) -> set[str]:
        with self.connect() as conn:
            return {row[0] for row in conn.execute(sql.SQL("SELECT id FROM {}").format(sql.Identifier("dv_vectors", manifest["collection"]))).fetchall()}
