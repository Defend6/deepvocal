import uuid
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.identity import Session, User

router = APIRouter(prefix="/v1/operations", tags=["operations"])


class JobFinding(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    attempt: int
    max_attempts: int
    code: str


class OwnDiagnostics(BaseModel):
    counts: dict[str, int]
    active_retries: int
    oldest_queued_seconds: int | None
    budget_needs_review: int
    worker_signal: Literal["recent_heartbeat", "no_recent_heartbeat"]
    alerts: list[str]
    findings: list[JobFinding]


async def own_diagnostics(session, owner: str) -> dict:
    # Database time is authoritative for ages, leases and liveness comparisons.
    row = (
        (
            await session.execute(
                text("""SELECT
        count(*) FILTER(WHERE status='QUEUED') AS queued,
        count(*) FILTER(WHERE status='RUNNING') AS running,
        count(*) FILTER(WHERE status='SUCCEEDED') AS succeeded,
        count(*) FILTER(WHERE status='FAILED') AS failed,
        count(*) FILTER(WHERE status='CANCELLED') AS cancelled,
        count(*) FILTER(WHERE status IN ('QUEUED','RUNNING') AND attempt>1) AS retries,
        count(*) FILTER(WHERE status='RUNNING' AND lease_until<now()) AS expired,
        count(*) FILTER(WHERE status='FAILED' AND updated_at>now()-interval '24 hours') AS recent_failures,
        CAST(EXTRACT(EPOCH FROM now()-min(created_at) FILTER(WHERE status='QUEUED')) AS bigint) AS oldest
        FROM jobs WHERE owner_id=:owner"""),
                {"owner": owner},
            )
        )
        .mappings()
        .one()
    )
    budget = await session.scalar(
        text("""SELECT count(*) FROM model_call_reservations
        WHERE owner_id=:owner AND (status='billing_anomaly' OR
        (status IN ('reserved','dispatched') AND created_at<now()-interval '10 minutes'))"""),
        {"owner": owner},
    )
    alive = await session.scalar(
        text("""SELECT EXISTS(SELECT 1 FROM worker_heartbeats
        WHERE seen_at>now()-interval '30 seconds' AND phase IN ('idle','busy'))""")
    )
    findings = (
        (
            await session.execute(
                text("""SELECT id,kind,status,attempt,max_attempts,
        CASE WHEN status='RUNNING' AND lease_until<now() THEN 'LEASE_EXPIRED'
        WHEN status='QUEUED' AND available_at>now() THEN 'RETRY_BACKOFF'
        WHEN status='QUEUED' THEN 'QUEUE_WAIT'
        ELSE COALESCE(error_code,'JOB_FAILED') END AS code
        FROM jobs WHERE owner_id=:owner AND (
        (status='RUNNING' AND lease_until<now()) OR status='QUEUED' OR
        (status='FAILED' AND updated_at>now()-interval '24 hours'))
        ORDER BY CASE WHEN status='RUNNING' THEN 0 WHEN status='FAILED' THEN 1 ELSE 2 END,
        created_at LIMIT 10"""),
                {"owner": owner},
            )
        )
        .mappings()
        .all()
    )
    alerts = []
    if not alive:
        alerts.append("WORKER_HEARTBEAT_MISSING")
    if row["expired"]:
        alerts.append("EXPIRED_JOB_LEASE")
    if row["oldest"] is not None and row["oldest"] > 60:
        alerts.append("QUEUE_WAIT_OVER_60S")
    if row["recent_failures"]:
        alerts.append("RECENT_JOB_FAILURES")
    if budget:
        alerts.append("MODEL_ACCOUNTING_REVIEW")
    return {
        "counts": {
            key.upper(): int(row[key])
            for key in ("queued", "running", "succeeded", "failed", "cancelled")
        },
        "active_retries": row["retries"],
        "oldest_queued_seconds": row["oldest"],
        "budget_needs_review": budget,
        "worker_signal": "recent_heartbeat" if alive else "no_recent_heartbeat",
        "alerts": alerts,
        "findings": [dict(item) for item in findings],
    }


@router.get("/diagnostics", response_model=OwnDiagnostics)
async def diagnostics(user: User, session: Session):
    return await own_diagnostics(session, user["id"])
