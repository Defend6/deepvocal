"""Owner quotas, minimal audit and independently protected operator visibility."""

import hashlib
import json
from collections import Counter
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from redis.asyncio import Redis
from sqlalchemy import text

from app.config import get_settings
from app.identity import Session, User

router = APIRouter(prefix="/v1", tags=["security and operations"])
metrics = Counter()


async def rate_limit(issuer, subject):
    settings = get_settings()
    key = "dv:rate:" + hashlib.sha256(f"{issuer}|{subject}".encode()).hexdigest()
    redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
    try:
        count = await redis.eval(
            """local n=redis.call('INCR',KEYS[1]);
          if n==1 then redis.call('EXPIRE',KEYS[1],60) end; return n""",
            1,
            key,
        )
        if count > settings.request_limit_per_minute:
            raise HTTPException(
                429, "too many requests; retry shortly", headers={"Retry-After": "60"}
            )
    except HTTPException:
        raise
    except Exception as exc:
        # Quotas cannot silently disappear when Redis is unavailable.
        raise HTTPException(503, "request admission temporarily unavailable") from exc
    finally:
        await redis.aclose()


async def job_quota(session, owner):
    settings = get_settings()
    status = await session.scalar(
        text("SELECT status FROM users WHERE id=:owner FOR UPDATE"), {"owner": owner}
    )
    if status != "active":
        raise HTTPException(403, "user account is not active")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:owner,0))"), {"owner": owner}
    )
    counts = (
        (
            await session.execute(
                text("""SELECT
      count(*) FILTER(WHERE status IN ('QUEUED','RUNNING')) AS active,
      count(*) FILTER(WHERE created_at >= date_trunc('day',now() AT TIME ZONE 'Asia/Shanghai')
        AT TIME ZONE 'Asia/Shanghai') AS daily
      FROM jobs WHERE owner_id=:owner AND kind NOT IN ('memory','upload_verify','deletion')"""),
                {"owner": owner},
            )
        )
        .mappings()
        .one()
    )
    if (
        counts["active"] >= settings.jobs_concurrent_limit
        or counts["daily"] >= settings.jobs_daily_limit
    ):
        raise HTTPException(429, "job quota reached; retry later", headers={"Retry-After": "60"})


async def audit(session, owner, action, resource=None, details=None):
    await session.execute(
        text("""INSERT INTO audit_events(id,owner_id,action,resource_id,details)
      VALUES (:id,:owner,:action,:resource,CAST(:details AS jsonb))"""),
        {
            "id": str(uuid4()),
            "owner": owner,
            "action": action,
            "resource": resource,
            "details": json.dumps(details or {}),
        },
    )


def require_operator(user):
    if get_settings().operator_role not in user.get("roles", []):
        raise HTTPException(403, "operator permission required")


@router.get("/auth/config")
def auth_config():
    settings = get_settings()
    return {
        "mode": settings.auth_mode,
        "issuer": settings.oidc_issuer if settings.auth_mode == "oidc" else "",
        "client_id": settings.oidc_client_id,
        "audience": settings.oidc_audience,
    }


@router.get("/audit")
async def own_audit(user: User, session: Session):
    from fastapi.encoders import jsonable_encoder

    rows = (
        await session.execute(
            text("""SELECT action,resource_id,created_at FROM audit_events
      WHERE owner_id=:owner ORDER BY created_at DESC LIMIT 100"""),
            {"owner": user["id"]},
        )
    ).mappings()
    return jsonable_encoder([dict(row) for row in rows])


@router.get("/admin/metrics")
async def operator_metrics(user: User, session: Session):
    require_operator(user)
    return await metrics_response(session)


async def metrics_response(session):
    from fastapi.responses import PlainTextResponse

    rows = (
        await session.execute(text("SELECT status,count(*) AS count FROM jobs GROUP BY status"))
    ).mappings()
    lines = ["# TYPE deepvocal_jobs gauge"]
    lines += [f'deepvocal_jobs{{status="{row["status"]}"}} {row["count"]}' for row in rows]
    for (status,), count in sorted(metrics.items()):
        lines.append(f'deepvocal_http_requests_total{{status="{status}"}} {count}')
    return PlainTextResponse("\n".join(lines) + "\n", media_type="text/plain; version=0.0.4")
