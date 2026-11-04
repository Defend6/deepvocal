"""Local operator entry point. No administrative mutation endpoints are exposed over HTTP."""

import argparse
import asyncio
import json
import sys

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.config import get_settings
from app.database import _session_factory
from app.operations.maintenance import maintain_stream, recover_notifications, recovery_snapshot
from app.queue_constants import DEAD, STREAM


async def operational_status(sessions, redis) -> dict:
    async with sessions() as session:
        workers = (
            (
                await session.execute(
                    text("""SELECT
            count(*) FILTER(WHERE phase<>'stopped' AND seen_at>now()-interval '30 seconds') AS live,
            count(*) FILTER(WHERE phase<>'stopped' AND seen_at<=now()-interval '30 seconds') AS stale,
            count(*) FILTER(WHERE phase='degraded' AND seen_at>now()-interval '30 seconds') AS degraded,
            count(*) FILTER(WHERE phase<>'stopped' AND seen_at>now()-interval '30 seconds'
                AND COALESCE(last_tick_at,started_at)<now()-interval '15 minutes') AS stalled
            FROM worker_heartbeats""")
                )
            )
            .mappings()
            .one()
        )
        jobs = await recovery_snapshot(session)
        pending_outbox = await session.scalar(
            text("SELECT count(*) FROM outbox_events WHERE published_at IS NULL")
        )
        budget = await session.scalar(
            text("""SELECT count(*) FROM model_call_reservations
            WHERE status='billing_anomaly' OR (status IN ('reserved','dispatched')
            AND created_at<now()-interval '10 minutes')""")
        )
    queues = {}
    try:
        for name, key in (("jobs", STREAM), ("dead", DEAD)):
            groups = await redis.xinfo_groups(key) if await redis.exists(key) else []
            queues[name] = {
                "length": await redis.xlen(key),
                "groups": [
                    {"pending": group["pending"], "lag": group.get("lag")} for group in groups
                ],
            }
    except RedisError:
        queues = {"status": "unavailable", "reason": "QUEUE_UNAVAILABLE"}
    signals = []
    if workers["live"] == 0:
        signals.append("NO_LIVE_WORKER")
    if workers["degraded"] or workers["stalled"]:
        signals.append("WORKER_DEGRADED_OR_STALLED")
    if jobs["reclaimable"] or jobs["exhausted"]:
        signals.append("EXPIRED_JOB_LEASE")
    if pending_outbox:
        signals.append("OUTBOX_PENDING")
    if budget:
        signals.append("MODEL_ACCOUNTING_REVIEW")
    if queues.get("status") == "unavailable":
        signals.append("QUEUE_UNAVAILABLE")
    return {
        "workers": dict(workers),
        "jobs": jobs,
        "outbox_pending": pending_outbox,
        "budget_needs_review": budget,
        "queues": queues,
        "signals": signals,
        "scope": "Local account-wide aggregates; no input, result, identity, key or signed URL.",
    }


async def execute(args):
    settings = get_settings()
    sessions = _session_factory()
    redis = Redis.from_url(
        settings.redis_url, decode_responses=True, socket_connect_timeout=3, socket_timeout=5
    )
    try:
        if args.command == "status":
            return await operational_status(sessions, redis)
        if args.command == "recover":
            return await recover_notifications(sessions, args.batch, args.apply)
        return await maintain_stream(
            sessions,
            redis,
            args.stream,
            args.retention_days or (7 if args.stream == "jobs" else 30),
            args.batch,
            args.after_id,
            args.apply,
        )
    finally:
        await redis.aclose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    subs.add_parser("status")
    for name in ("recover", "maintain"):
        command = subs.add_parser(name)
        command.add_argument("--apply", action="store_true", help="otherwise preview only")
        command.add_argument(
            "--batch", type=int, choices=range(1, 501), default=100, metavar="1..500"
        )
        if name == "maintain":
            command.add_argument("--stream", choices=["jobs", "dead"], default="jobs")
            command.add_argument(
                "--retention-days", type=int, choices=range(1, 3651), metavar="1..3650"
            )
            command.add_argument("--after-id", default="0-0")
    try:
        report = asyncio.run(execute(parser.parse_args()))
    except (SQLAlchemyError, RedisError, ValueError) as exc:
        reason = (
            "DATABASE_UNAVAILABLE"
            if isinstance(exc, SQLAlchemyError)
            else (
                "QUEUE_UNAVAILABLE" if isinstance(exc, RedisError) else "INVALID_MAINTENANCE_INPUT"
            )
        )
        print(json.dumps({"status": "failed", "reason": reason}))
        raise SystemExit(1) from None
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get("queues", {}).get("status") == "unavailable":
        sys.exit(1)


if __name__ == "__main__":
    main()
