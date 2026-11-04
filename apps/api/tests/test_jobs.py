import asyncio
import os
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from fastapi import HTTPException
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.jobs import JobCreate, cancel_job, enqueue, read_job
from app.worker import (
    DEAD,
    STREAM,
    RetryableJobError,
    claim,
    finish,
    process_one,
    publish_outbox,
    recover_exhausted,
)

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")


@asynccontextmanager
async def database_case():
    engine = create_async_engine(os.environ["TEST_DATABASE_URL"])
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    owner = str(uuid4())
    async with sessions() as session:
        await session.execute(
            text("INSERT INTO users(id,issuer,subject) VALUES (:id,'test-jobs',:sub)"),
            {"id": owner, "sub": owner},
        )
        await session.commit()
    try:
        yield sessions, owner
    finally:
        async with sessions() as session:
            for table in (
                "practice_records",
                "practice_plans",
                "assessment_reports",
                "outbox_events",
                "jobs",
                "audio_assets",
            ):
                await session.execute(
                    text(f"DELETE FROM {table} WHERE owner_id=:owner"), {"owner": owner}
                )
            await session.execute(text("DELETE FROM users WHERE id=:owner"), {"owner": owner})
            await session.commit()
        await engine.dispose()


async def submit(sessions, owner, key=None):
    async with sessions() as session:
        return await enqueue(
            session,
            owner,
            JobCreate(
                kind="evaluation",
                input={"check": "infrastructure"},
                idempotency_key=key or str(uuid4()),
            ),
        )


def test_concurrent_idempotency_and_outbox_atomicity():
    async def run():
        async with database_case() as (sessions, owner):
            key = str(uuid4())
            left, right = await asyncio.gather(
                submit(sessions, owner, key), submit(sessions, owner, key)
            )
            assert left["id"] == right["id"]
            async with sessions() as session:
                count = await session.scalar(
                    text("SELECT count(*) FROM outbox_events WHERE owner_id=:owner"),
                    {"owner": owner},
                )
                assert count == 1
                with pytest.raises(HTTPException) as exc:
                    await enqueue(
                        session,
                        owner,
                        JobCreate(
                            kind="evaluation", input={"different": True}, idempotency_key=key
                        ),
                    )
                assert exc.value.status_code == 409

    asyncio.run(run())


def test_duplicate_delivery_cancellation_and_fencing():
    async def run():
        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            job = await claim(sessions, created["id"])
            assert job and await claim(sessions, created["id"]) is None
            async with sessions() as session:
                cancelled = await cancel_job(job["id"], {"id": owner}, session)
                assert cancelled["status"] == "CANCELLED"
            assert await finish(sessions, job, {"stale": True}, None) is False
            async with sessions() as session:
                with pytest.raises(HTTPException) as exc:
                    await read_job(job["id"], {"id": str(uuid4())}, session)
                assert exc.value.status_code == 404

    asyncio.run(run())


def test_expired_lease_reclaimed_and_old_worker_cannot_finish():
    async def run():
        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            old = await claim(sessions, created["id"])
            async with sessions() as session:
                await session.execute(
                    text("UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id"),
                    {"id": created["id"]},
                )
                await session.commit()
            new = await claim(sessions, created["id"])
            assert new["attempt"] == 2
            assert not await finish(sessions, old, {"stale": True}, None)
            assert await finish(sessions, new, {"fresh": True}, None)
            assert not await process_one(sessions, job_id=created["id"])

    asyncio.run(run())


def test_retry_backoff_exhaustion_and_dead_event():
    async def fail(_job):
        raise RetryableJobError("injected outage")

    async def run():
        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            assert await process_one(sessions, fail, created["id"])
            assert await claim(sessions, created["id"]) is None
            for _ in range(2):
                async with sessions() as session:
                    await session.execute(
                        text("UPDATE jobs SET available_at=now() WHERE id=:id"),
                        {"id": created["id"]},
                    )
                    await session.commit()
                await process_one(sessions, fail, created["id"])
            async with sessions() as session:
                job = await read_job(created["id"], {"id": owner}, session)
                assert job["status"] == "FAILED" and job["attempt"] == 3
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM outbox_events WHERE aggregate_id=:id AND event_type='job.dead'"
                        ),
                        {"id": created["id"]},
                    )
                    == 1
                )

    asyncio.run(run())


def test_final_attempt_crash_recovery_and_pending_asset_rejection():
    async def run():
        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            async with sessions() as session:
                await session.execute(
                    text(
                        "UPDATE jobs SET status='RUNNING',attempt=3,lease_until=now()-interval '1 second' WHERE id=:id"
                    ),
                    {"id": created["id"]},
                )
                asset = str(uuid4())
                await session.execute(
                    text(
                        "INSERT INTO audio_assets(id,owner_id,object_key,sha256,content_type,size_bytes,purpose) VALUES (:id,:owner,:key,:sha,'audio/wav',10,'assessment')"
                    ),
                    {"id": asset, "owner": owner, "key": f"test/{asset}", "sha": "a" * 64},
                )
                await session.commit()
                with pytest.raises(HTTPException) as exc:
                    await enqueue(
                        session,
                        owner,
                        JobCreate(
                            kind="assessment", input_asset_id=asset, idempotency_key=str(uuid4())
                        ),
                    )
                assert exc.value.status_code == 409
            await recover_exhausted(sessions)
            async with sessions() as session:
                assert (await read_job(created["id"], {"id": owner}, session))[
                    "error_code"
                ] == "LEASE_EXHAUSTED"

    asyncio.run(run())


def test_redis_outbox_notification_and_job_execution():
    if not os.getenv("TEST_REDIS_URL"):
        pytest.skip("requires isolated Redis test database")

    async def run():
        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        try:
            async with database_case() as (sessions, owner):
                created = await submit(sessions, owner)
                await publish_outbox(sessions, redis)
                messages = await redis.xrange(STREAM)
                assert any(data["job_id"] == created["id"] for _, data in messages)
                assert await process_one(sessions, job_id=created["id"])
                async with sessions() as session:
                    assert (await read_job(created["id"], {"id": owner}, session))[
                        "status"
                    ] == "SUCCEEDED"
        finally:
            await redis.delete(STREAM, DEAD)
            await redis.aclose()

    asyncio.run(run())
