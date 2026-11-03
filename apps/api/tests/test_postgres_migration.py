import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest

DATABASE_URL = os.getenv("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="set TEST_DATABASE_URL for PostgreSQL integration checks")
API_ROOT = Path(__file__).resolve().parents[1]


def test_initial_migration_and_owner_scoped_asset_reference() -> None:
    environment = os.environ.copy()
    environment["DATABASE_URL"] = DATABASE_URL or ""
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        cwd=API_ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    asyncio.run(_check_owner_constraint())


async def _check_owner_constraint() -> None:
    database_url = (DATABASE_URL or "").replace("postgresql+asyncpg://", "postgresql://", 1)
    connection = await asyncpg.connect(database_url)
    try:
        tables = await connection.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        assert {row["tablename"] for row in tables} >= {
            "users",
            "audio_assets",
            "jobs",
            "outbox_events",
            "alembic_version",
        }

        owner_a, owner_b, asset_id = (str(uuid4()) for _ in range(3))
        async with connection.transaction():
            await connection.execute(
                "INSERT INTO users (id, issuer, subject) VALUES ($1, 'test', $2), ($3, 'test', $4)",
                owner_a,
                f"owner-{owner_a}",
                owner_b,
                f"owner-{owner_b}",
            )
            await connection.execute(
                """INSERT INTO audio_assets
                   (id, owner_id, object_key, sha256, content_type, size_bytes, purpose)
                   VALUES ($1, $2, $3, $4, 'audio/wav', 1024, 'assessment')""",
                asset_id,
                owner_a,
                f"test/{asset_id}.wav",
                "a" * 64,
            )
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                async with connection.transaction():
                    await connection.execute(
                        """INSERT INTO jobs (id, owner_id, kind, input_asset_id, request, trace_id)
                           VALUES ($1, $2, 'assessment', $3, '{}'::jsonb, 'test-trace')""",
                        str(uuid4()),
                        owner_b,
                        asset_id,
                    )
    finally:
        await connection.close()
