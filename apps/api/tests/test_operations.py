import asyncio
import json
import os
from uuid import uuid4

import httpx
import pytest
from redis.asyncio import Redis
from redis.exceptions import ConnectionError as QueueConnectionError
from sqlalchemy import text
from test_jobs import database_case, submit

from app.database import get_session
from app.identity import current_user
from app.main import app
from app.operations.cli import operational_status
from app.operations.diagnostics import own_diagnostics
from app.operations.heartbeat import worker_presence
from app.operations.maintenance import maintain_stream, recover_notifications, valid_cursor
from app.worker import process_one, tick

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")


def test_heartbeat_pulses_during_handler_and_stops_cleanly():
    async def run():
        async with database_case() as (sessions, owner):
            consumer = "test-" + str(uuid4())
            created = await submit(sessions, owner)
            gate, started = asyncio.Event(), asyncio.Event()

            async def long_handler(_job):
                started.set()
                await gate.wait()
                return {"status": "ok"}

            try:
                async with worker_presence(sessions, consumer, interval=0.02) as state:
                    task = asyncio.create_task(
                        process_one(sessions, long_handler, created["id"], state)
                    )
                    await started.wait()
                    for _ in range(100):
                        async with sessions() as session:
                            row = (
                                (
                                    await session.execute(
                                        text(
                                            "SELECT * FROM worker_heartbeats WHERE consumer=:consumer"
                                        ),
                                        {"consumer": consumer},
                                    )
                                )
                                .mappings()
                                .one()
                            )
                        if row["phase"] == "busy" and row["seen_at"] > row["started_at"]:
                            break
                        await asyncio.sleep(0.02)
                    assert row["phase"] == "busy" and str(row["active_job_id"]) == created["id"]
                    assert row["seen_at"] > row["started_at"]
                    gate.set()
                    assert await task
                async with sessions() as session:
                    assert (
                        await session.scalar(
                            text("SELECT phase FROM worker_heartbeats WHERE consumer=:consumer"),
                            {"consumer": consumer},
                        )
                        == "stopped"
                    )
            finally:
                gate.set()
                async with sessions() as session:
                    await session.execute(
                        text("DELETE FROM worker_heartbeats WHERE consumer=:c"), {"c": consumer}
                    )
                    await session.commit()

    asyncio.run(run())


def test_reclaim_cursor_advances_instead_of_restarting_scan():
    from app.operations.heartbeat import WorkerState

    class ScanQueue:
        def __init__(self):
            self.starts = []

        async def xgroup_create(self, *args, **kwargs):
            pass

        async def xautoclaim(self, *args, **kwargs):
            self.starts.append(args[4])
            return ["400-0" if len(self.starts) == 1 else "0-0", [], []]

        async def xreadgroup(self, *args, **kwargs):
            return []

    async def run():
        async with database_case() as (sessions, _):
            queue, state = ScanQueue(), WorkerState()
            await tick(sessions, queue, "scan-test", state)
            await tick(sessions, queue, "scan-test", state)
            assert queue.starts == ["0-0", "400-0"]
            assert state.reclaim_cursor == "0-0" and state.tick_generation == 2

    asyncio.run(run())


def test_diagnostics_api_only_exposes_current_owner_and_safe_metadata():
    async def run():
        async with database_case() as (sessions, owner), database_case() as (_, other):
            first = await submit(sessions, owner)
            hidden = await submit(sessions, other)
            async with sessions() as session:
                await session.execute(
                    text("""UPDATE jobs SET status='FAILED',error_code='TIMEOUT',
                    updated_at=now() WHERE id=:id"""),
                    {"id": first["id"]},
                )
                await session.commit()

            async def identity():
                return {"id": owner}

            async def session_dependency():
                async with sessions() as session:
                    yield session

            app.dependency_overrides[current_user] = identity
            app.dependency_overrides[get_session] = session_dependency
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    response = await client.get(
                        "/v1/operations/diagnostics", params={"owner_id": other}
                    )
                assert response.status_code == 200
                result = response.json()
                assert result["counts"]["FAILED"] == 1 and result["counts"]["QUEUED"] == 0
                assert "RECENT_JOB_FAILURES" in result["alerts"]
                assert result["findings"][0]["id"] == first["id"]
                serialized = json.dumps(result)
                assert hidden["id"] not in serialized
                assert all(
                    key not in serialized
                    for key in ("worker_token", "request_hash", "idempotency_key", "issuer")
                )
                async with sessions() as session:
                    second = await own_diagnostics(session, other)
                assert second["counts"]["QUEUED"] == 1 and second["counts"]["FAILED"] == 0
            finally:
                app.dependency_overrides.clear()

    asyncio.run(run())


class BrokenQueue:
    async def xadd(self, *args, **kwargs):
        raise QueueConnectionError("injected outage: secret-url-must-not-be-returned")

    async def exists(self, *args):
        raise QueueConnectionError("injected outage: secret-url-must-not-be-returned")


def test_queue_outage_keeps_database_recovery_and_redacted_diagnostics():
    async def run():
        from app.operations.heartbeat import WorkerState

        async with database_case() as (sessions, owner):
            created = await submit(sessions, owner)
            state = WorkerState()
            await tick(sessions, BrokenQueue(), "test-outage", state)
            assert state.phase == "degraded" and state.last_error_code == "QUEUE_UNAVAILABLE"
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT status FROM jobs WHERE id=:id"), {"id": created["id"]}
                    )
                    == "SUCCEEDED"
                )
            status = await operational_status(sessions, BrokenQueue())
            assert "QUEUE_UNAVAILABLE" in status["signals"]
            assert "secret-url" not in json.dumps(status)

    asyncio.run(run())


def test_recovery_preview_and_apply_do_not_reset_or_execute_jobs():
    async def run():
        async with database_case() as (sessions, owner):
            due, exhausted, cancelled = [await submit(sessions, owner) for _ in range(3)]
            async with sessions() as session:
                await session.execute(
                    text("UPDATE outbox_events SET published_at=now() WHERE owner_id=:owner"),
                    {"owner": owner},
                )
                await session.execute(
                    text("""UPDATE jobs SET status='RUNNING',attempt=3,
                    lease_until=now()-interval '1 second' WHERE id=:id"""),
                    {"id": exhausted["id"]},
                )
                await session.execute(
                    text("UPDATE jobs SET status='CANCELLED',cancel_requested=true WHERE id=:id"),
                    {"id": cancelled["id"]},
                )
                await session.commit()
            preview = await recover_notifications(sessions)
            assert preview["mode"] == "preview" and preview["notifications_created"] == 0
            applied = await recover_notifications(sessions, apply=True)
            assert applied["exhausted_failed"] == 1 and applied["notifications_created"] == 1
            again = await recover_notifications(sessions, apply=True)
            assert again["exhausted_failed"] == 0 and again["notifications_created"] == 0
            async with sessions() as session:
                rows = (
                    (
                        await session.execute(
                            text("SELECT id,status,attempt FROM jobs WHERE owner_id=:owner"),
                            {"owner": owner},
                        )
                    )
                    .mappings()
                    .all()
                )
            jobs = {str(row["id"]): row for row in rows}
            assert jobs[due["id"]]["status"] == "QUEUED" and jobs[due["id"]]["attempt"] == 0
            assert (
                jobs[exhausted["id"]]["status"] == "FAILED"
                and jobs[exhausted["id"]]["attempt"] == 3
            )
            assert jobs[cancelled["id"]]["status"] == "CANCELLED"

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="requires isolated Redis")
def test_maintenance_protects_pending_all_groups_unread_and_active_jobs(monkeypatch):
    async def run():
        key = "maintenance-test-" + str(uuid4())
        monkeypatch.setattr("app.operations.maintenance.STREAM", key)
        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        try:
            async with database_case() as (sessions, owner):
                jobs = [await submit(sessions, owner) for _ in range(4)]
                async with sessions() as session:
                    await session.execute(
                        text("""UPDATE jobs SET status='SUCCEEDED',
                        updated_at=now()-interval '60 days' WHERE owner_id=:owner AND id<>:active"""),
                        {"owner": owner, "active": jobs[3]["id"]},
                    )
                    await session.execute(
                        text(
                            "UPDATE outbox_events SET published_at=now()-interval '60 days' WHERE owner_id=:owner"
                        ),
                        {"owner": owner},
                    )
                    rows = (
                        await session.execute(
                            text("SELECT id,aggregate_id FROM outbox_events WHERE owner_id=:owner"),
                            {"owner": owner},
                        )
                    ).all()
                    await session.commit()
                events = {str(row.aggregate_id): str(row.id) for row in rows}
                for number, job in enumerate(jobs, 1):
                    await redis.xadd(
                        key, {"job_id": job["id"], "event_id": events[job["id"]]}, id=f"{number}-0"
                    )
                for group in ("first", "second"):
                    await redis.xgroup_create(key, group, "0")
                await redis.xreadgroup("first", "c1", {key: ">"}, count=2)
                await redis.xreadgroup("second", "c2", {key: ">"}, count=1)
                await redis.xack(key, "first", "1-0")
                await redis.xack(key, "second", "1-0")
                preview = await maintain_stream(sessions, redis, "jobs", 7)
                assert preview["eligible_after_group_checks"] == 1 and await redis.xlen(key) == 4
                applied = await maintain_stream(sessions, redis, "jobs", 7, apply=True)
                assert applied["deleted"] == 1
                assert (await redis.xpending_range(key, "first", "-", "+", 10))[0][
                    "message_id"
                ] == "2-0"
                await redis.xreadgroup("second", "c2", {key: ">"}, count=1)
                await redis.xack(key, "second", "2-0")
                assert (await maintain_stream(sessions, redis, "jobs", 7, apply=True))[
                    "deleted"
                ] == 0
                await redis.xack(key, "first", "2-0")
                assert (await maintain_stream(sessions, redis, "jobs", 7, apply=True))[
                    "deleted"
                ] == 1
                for group in ("first", "second"):
                    await redis.xreadgroup(group, "consumer", {key: ">"}, count=10)
                    await redis.xack(key, group, "3-0", "4-0")
                assert (await maintain_stream(sessions, redis, "jobs", 7, apply=True))[
                    "deleted"
                ] == 1
                assert [item[0] for item in await redis.xrange(key)] == ["4-0"]
                for group in ("first", "second"):
                    await redis.xgroup_destroy(key, group)
                async with sessions() as session:
                    await session.execute(
                        text("""UPDATE jobs SET status='SUCCEEDED',
                        updated_at=now()-interval '60 days' WHERE id=:id"""),
                        {"id": jobs[3]["id"]},
                    )
                    await session.commit()
                assert (await maintain_stream(sessions, redis, "jobs", 7, apply=True))[
                    "deleted"
                ] == 0
        finally:
            await redis.delete(key)
            await redis.aclose()

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_REDIS_URL"), reason="requires isolated Redis")
def test_dead_retention_bounds_cursor_and_unrecognized_entries(monkeypatch):
    async def run():
        key = "dead-test-" + str(uuid4())
        monkeypatch.setattr("app.operations.maintenance.DEAD", key)
        redis = Redis.from_url(os.environ["TEST_REDIS_URL"], decode_responses=True)
        try:
            async with database_case() as (sessions, _):
                await redis.xadd(key, {"error": "INVALID_NOTIFICATION"}, id="1-0")
                await redis.xadd(key, {"unrecognized": "retained"}, id="2-0")
                await redis.xadd(key, {"error": "INVALID_NOTIFICATION"})  # recent: retained
                first = await maintain_stream(sessions, redis, "dead", 30, batch=1)
                assert first["batch_full"] and first["next_cursor"] == "1-0"
                assert await redis.xlen(key) == 3
                later = await maintain_stream(
                    sessions, redis, "dead", 30, after=first["next_cursor"]
                )
                assert later["eligible_after_group_checks"] == 0
                assert (await maintain_stream(sessions, redis, "dead", 30, apply=True))[
                    "deleted"
                ] == 1
                assert await redis.xlen(key) == 2
                with pytest.raises(ValueError):
                    await maintain_stream(sessions, redis, "dead", 0, apply=True)
                for cursor in ("0-0;DEL", "-1-0", "18446744073709551616-0"):
                    with pytest.raises(ValueError):
                        valid_cursor(cursor)
        finally:
            await redis.delete(key)
            await redis.aclose()

    asyncio.run(run())
