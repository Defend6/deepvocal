"""PostgreSQL owns task state; Redis Streams provide at-least-once notifications."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time
import uuid
from collections.abc import Awaitable, Callable

from redis.asyncio import Redis
from redis.exceptions import RedisError, ResponseError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import _session_factory
from app.operations.heartbeat import WorkerState, worker_presence
from app.queue_constants import DEAD, GROUP, STREAM

logger = logging.getLogger(__name__)


class RetryableJobError(Exception):
    pass


class PermanentJobError(Exception):
    pass


async def publish_outbox(sessions, redis: Redis, limit: int = 50) -> int:
    async with sessions() as session:
        rows = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM outbox_events WHERE published_at IS NULL ORDER BY created_at,id FOR UPDATE SKIP LOCKED LIMIT :limit"
                    ),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            # Crash after XADD before commit causes a duplicate; worker fencing handles it.
            await redis.xadd(
                DEAD if row["event_type"] == "job.dead" else STREAM,
                {"job_id": str(row["aggregate_id"]), "event_id": str(row["id"])},
            )
            await session.execute(
                text("UPDATE outbox_events SET published_at=now() WHERE id=:id"),
                {"id": str(row["id"])},
            )
        await session.commit()
        return len(rows)


async def recover_exhausted(sessions, limit: int = 50) -> int:
    async with sessions() as session:
        rows = (
            (
                await session.execute(
                    text("""
            WITH exhausted AS (SELECT id FROM jobs
                WHERE status='RUNNING' AND lease_until < now() AND attempt>=max_attempts
                ORDER BY lease_until FOR UPDATE SKIP LOCKED LIMIT :limit)
            UPDATE jobs SET status='FAILED',error_code='LEASE_EXHAUSTED',worker_token=NULL,
            lease_until=NULL,version=version+1,updated_at=now()
            WHERE id IN (SELECT id FROM exhausted)
            RETURNING id,owner_id
        """),
                    {"limit": limit},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            await dead_event(session, row)
        await session.commit()
        return len(rows)


async def claim(sessions, job_id: str | None = None) -> dict | None:
    async with sessions() as session:
        row = (
            (
                await session.execute(
                    text("""
            WITH candidate AS (
                SELECT id FROM jobs WHERE cancel_requested=false AND attempt<max_attempts
                AND ((status='QUEUED' AND available_at<=now())
                    OR (status='RUNNING' AND lease_until<now()))
                AND (CAST(:job AS uuid) IS NULL OR id=CAST(:job AS uuid))
                ORDER BY available_at,created_at FOR UPDATE SKIP LOCKED LIMIT 1
            )
            UPDATE jobs j SET status='RUNNING',attempt=attempt+1,version=version+1,
                worker_token=:token,lease_until=now()+(timeout_seconds+30)*interval '1 second',
                updated_at=now() FROM candidate c WHERE j.id=c.id RETURNING j.*
        """),
                    {"job": job_id, "token": str(uuid.uuid4())},
                )
            )
            .mappings()
            .first()
        )
        await session.commit()
        return dict(row) if row else None


async def dead_event(session, row) -> None:
    await session.execute(
        text("""
        INSERT INTO outbox_events(id,owner_id,event_type,aggregate_id,payload)
        VALUES (:event,:owner,'job.dead',:id,'{}'::jsonb)
    """),
        {"event": str(uuid.uuid4()), "owner": str(row["owner_id"]), "id": str(row["id"])},
    )


async def finish(
    sessions, job: dict, result: dict | None, error: str | None, retryable: bool = False
) -> bool:
    retry = bool(error and retryable and job["attempt"] < job["max_attempts"])
    status = "QUEUED" if retry else "FAILED" if error else "SUCCEEDED"
    async with sessions() as session:
        if job["kind"] == "singing":
            # Consent revocation and privacy erasure take the same owner lock first.
            active = await session.scalar(
                text("SELECT status FROM users WHERE id=:owner FOR UPDATE"),
                {"owner": str(job["owner_id"])},
            )
            if active != "active":
                return False
        row = (
            (
                await session.execute(
                    text("""
            UPDATE jobs SET status=:status,result=CAST(:result AS jsonb),error_code=:error,
                worker_token=NULL,lease_until=NULL,updated_at=now(),
                available_at=now()+:delay*interval '1 second'
            WHERE id=:id AND owner_id=:owner AND status='RUNNING' AND worker_token=:token
                AND version=:version AND cancel_requested=false AND lease_until>now()
            RETURNING id,owner_id
        """),
                    {
                        "status": status,
                        "result": json.dumps(result) if result is not None else None,
                        "error": error,
                        "delay": min(60, 2 ** job["attempt"]) if retry else 0,
                        "id": str(job["id"]),
                        "owner": str(job["owner_id"]),
                        "token": str(job["worker_token"]),
                        "version": job["version"],
                    },
                )
            )
            .mappings()
            .first()
        )
        if row and status == "FAILED":
            await dead_event(session, row)
        if row:
            from app.upload_workflows import finish_verification

            await finish_verification(session, job, result, error, retry)
        if row and status == "SUCCEEDED" and result is not None:
            from app.singing import publish_generation

            result = await publish_generation(session, job, result)
            from app.artifacts import materialize

            result = await materialize(session, job, result)
            from app.memory import complete_compaction, record_completion

            if job["kind"] == "memory":
                result = await complete_compaction(session, job)
            await record_completion(session, job, result)
            await session.execute(
                text("UPDATE jobs SET result=CAST(:result AS jsonb) WHERE id=:id"),
                {"result": json.dumps(result, allow_nan=False), "id": str(job["id"])},
            )
        await session.commit()
        return row is not None


async def default_handler(job: dict) -> dict:
    if job["kind"] == "upload_verify":
        from app.upload_workflows import verify_upload

        return await verify_upload(job)
    if job["kind"] == "memory":
        # All mutations happen in fenced finish, in the same transaction as job success.
        return {"status": "prepared"}
    if job["kind"] == "evaluation":
        if job["request"].get("input", {}) != {"check": "infrastructure"}:
            raise PermanentJobError("UNSUPPORTED_EVALUATION")
        return {"status": "ok", "check": "infrastructure", "algorithm_version": "queue-v1"}
    from app.runtime import execute_job

    return await execute_job(job)


async def process_one(
    sessions,
    handler: Callable[[dict], Awaitable[dict]] = default_handler,
    job_id: str | None = None,
    state: WorkerState | None = None,
) -> bool:
    job = await claim(sessions, job_id)
    if job is None:
        return False
    if state:
        state.phase, state.active_job_id = "busy", str(job["id"])
    started = time.monotonic()
    logger.info("job_started id=%s kind=%s attempt=%s", str(job["id"]), job["kind"], job["attempt"])
    try:
        await process_claimed(sessions, handler, job)
    finally:
        logger.info(
            "job_handler_finished id=%s duration_ms=%s",
            str(job["id"]),
            round((time.monotonic() - started) * 1000),
        )
        if state:
            state.phase, state.active_job_id = "idle", None
            state.tick_generation += 1
    return True


async def process_claimed(sessions, handler, job):
    from contextlib import suppress

    async def watch_claim():
        while True:
            await asyncio.sleep(0.5)
            async with sessions() as session:
                active = await session.scalar(
                    text("""SELECT 1 FROM jobs
                  WHERE id=:id AND owner_id=:owner AND status='RUNNING'
                  AND worker_token=:token AND version=:version
                  AND cancel_requested=false AND lease_until>now()"""),
                    {
                        "id": str(job["id"]),
                        "owner": str(job["owner_id"]),
                        "token": str(job["worker_token"]),
                        "version": job["version"],
                    },
                )
            if not active:
                return

    task = asyncio.create_task(handler(job))
    watcher = asyncio.create_task(watch_claim())
    try:
        finished, _ = await asyncio.wait(
            {task, watcher}, timeout=job["timeout_seconds"], return_when=asyncio.FIRST_COMPLETED
        )
        if watcher in finished:
            # Cancellation, ownership loss and monitor failures all prevent publication.
            await watcher
            return
        if task not in finished:
            raise TimeoutError()
        result = await task
    except (TimeoutError, RetryableJobError) as exc:
        await finish(
            sessions,
            job,
            None,
            "TIMEOUT" if isinstance(exc, TimeoutError) else "PROVIDER_UNAVAILABLE",
            True,
        )
    except PermanentJobError as exc:
        await finish(sessions, job, None, str(exc)[:80])
    except Exception:  # noqa: BLE001 -- worker boundary redacts private input and persists failure
        logger.error("job_failed id=%s code=INTERNAL_ERROR", str(job["id"]))
        await finish(sessions, job, None, "INTERNAL_ERROR")
    else:
        await finish(sessions, job, result, None)
    finally:
        task.cancel()
        watcher.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await task
        with suppress(asyncio.CancelledError, Exception):
            await watcher


async def tick(sessions, redis: Redis, consumer: str, state: WorkerState | None = None) -> None:
    if state:
        state.last_error_code = None
    await recover_exhausted(sessions)
    try:
        await publish_outbox(sessions, redis)
        try:
            await redis.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        except ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise
        recovered = await redis.xautoclaim(
            STREAM, GROUP, consumer, 30000, state.reclaim_cursor if state else "0-0", count=10
        )
        if state:
            state.reclaim_cursor = recovered[0]
        messages = list(recovered[1])
        if not messages:
            entries = await redis.xreadgroup(GROUP, consumer, {STREAM: ">"}, count=10, block=500)
            messages = [entry for _, batch in entries for entry in batch]
        for message_id, data in messages:
            try:
                job_id = str(uuid.UUID(data["job_id"]))
            except (KeyError, ValueError):
                await redis.xadd(DEAD, {"error": "INVALID_NOTIFICATION", "message_id": message_id})
            else:
                await process_one(sessions, job_id=job_id, state=state)
            await redis.xack(STREAM, GROUP, message_id)
    except RedisError:
        logger.warning("queue_degraded code=QUEUE_UNAVAILABLE; using database recovery")
        if state:
            state.last_error_code = "QUEUE_UNAVAILABLE"
    # DB sweep also recovers delayed retries and lost Redis notifications.
    await process_one(sessions, state=state)
    if state:
        state.tick_generation += 1
        state.phase = "degraded" if state.last_error_code else "idle"


async def run(once: bool = False) -> None:
    redis = Redis.from_url(
        get_settings().redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5
    )
    consumer = "worker-" + str(uuid.uuid4())
    try:
        sessions = _session_factory()
        async with worker_presence(sessions, consumer) as state:
            next_maintenance = time.monotonic() + 60
            while True:
                try:
                    await tick(sessions, redis, consumer, state)
                    if time.monotonic() >= next_maintenance:
                        from app.privacy import housekeep

                        try:
                            await housekeep(sessions)
                        except Exception:  # noqa: BLE001 -- maintenance must not stop task consumption
                            logger.warning("maintenance_degraded code=HOUSEKEEPING_RETRY")
                        next_maintenance = time.monotonic() + 60
                except SQLAlchemyError:
                    state.phase, state.last_error_code = "degraded", "DATABASE_UNAVAILABLE"
                    logger.warning("worker_degraded code=DATABASE_UNAVAILABLE; retrying")
                    if once:
                        raise
                    await asyncio.sleep(2)
                if once:
                    return
                await asyncio.sleep(0.25)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    logging.basicConfig(level=logging.INFO)
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run(parser.parse_args().once))
