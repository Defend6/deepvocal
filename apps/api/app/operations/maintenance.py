"""Bounded explicit maintenance; never trim streams by length or discard a pending entry."""

import re
from uuid import UUID, uuid4

from sqlalchemy import text

from app.queue_constants import DEAD, STREAM

# Group creation, delivery, PEL checks and deletion run in one Redis script.
# Decimal string comparison avoids rounding 64-bit Stream ID sequence numbers in Lua.
SAFE_DELETE = """
local function less(a,b)
  local am,as=string.match(a,'^(%d+)%-(%d+)$')
  local bm,bs=string.match(b,'^(%d+)%-(%d+)$')
  if #am~=#bm then return #am<#bm end
  if am~=bm then return am<bm end
  if #as~=#bs then return #as<#bs end
  return as<bs
end
if redis.call('EXISTS',KEYS[1])==0 then return {} end
local groups=redis.call('XINFO','GROUPS',KEYS[1])
local allowed={}
for i=3,#ARGV do
  local id=ARGV[i]
  local safe=(ARGV[2]=='dead' or #groups>0)
  for _,g in ipairs(groups) do
    local name,last
    for k=1,#g,2 do
      if g[k]=='name' then name=g[k+1] end
      if g[k]=='last-delivered-id' then last=g[k+1] end
    end
    if not name or not last or less(last,id) then safe=false; break end
    if #redis.call('XPENDING',KEYS[1],name,id,id,1)>0 then safe=false; break end
  end
  if safe and #redis.call('XRANGE',KEYS[1],id,id,'COUNT',1)>0 then
    table.insert(allowed,id)
    if ARGV[1]=='apply' then redis.call('XDEL',KEYS[1],id) end
  end
end
return allowed
"""


def valid_cursor(value: str) -> str:
    if not re.fullmatch(r"(?:0|[1-9]\d{0,19})-(?:0|[1-9]\d{0,19})", value):
        raise ValueError("INVALID_STREAM_CURSOR")
    if any(int(part) > 18446744073709551615 for part in value.split("-")):
        raise ValueError("INVALID_STREAM_CURSOR")
    return value


async def maintain_stream(
    sessions, redis, kind: str, days: int, batch: int = 200, after: str = "0-0", apply: bool = False
) -> dict:
    if kind not in {"jobs", "dead"} or not 1 <= days <= 3650 or not 1 <= batch <= 500:
        raise ValueError("INVALID_MAINTENANCE_LIMIT")
    valid_cursor(after)
    stream = STREAM if kind == "jobs" else DEAD
    async with sessions() as session:
        # Retention uses database time, rather than a potentially skewed CLI clock.
        cutoff = await session.scalar(
            text("SELECT floor(EXTRACT(EPOCH FROM now()-:days*interval '1 day')*1000)::bigint"),
            {"days": days},
        )
        entries = await redis.xrange(stream, min="(" + after, max=f"{cutoff}-0", count=batch)
        candidates, invalid_notices = {}, []
        for message_id, data in entries:
            try:
                candidates[message_id] = (str(UUID(data["event_id"])), str(UUID(data["job_id"])))
            except (KeyError, ValueError, TypeError, AttributeError):
                if kind == "dead" and data.get("error") == "INVALID_NOTIFICATION":
                    invalid_notices.append(message_id)
        events = [event for event, _ in candidates.values()]
        terminal = set()
        if events:
            rows = (
                await session.execute(
                    text("""SELECT e.id,e.aggregate_id
                FROM outbox_events e JOIN jobs j ON e.aggregate_id=j.id AND e.owner_id=j.owner_id
                WHERE e.id=ANY(CAST(:events AS uuid[])) AND e.published_at IS NOT NULL
                AND e.published_at<to_timestamp(:cutoff/1000.0)
                AND j.updated_at<to_timestamp(:cutoff/1000.0)
                AND j.status IN ('SUCCEEDED','FAILED','CANCELLED')
                AND e.event_type=:event_type FOR SHARE OF j,e"""),
                    {
                        "events": events,
                        "cutoff": cutoff,
                        "event_type": "job.ready" if kind == "jobs" else "job.dead",
                    },
                )
            ).all()
            terminal = {(str(row.id), str(row.aggregate_id)) for row in rows}
        eligible = [key for key, value in candidates.items() if value in terminal] + invalid_notices
        allowed = (
            await redis.eval(
                SAFE_DELETE, 1, stream, "apply" if apply else "preview", kind, *eligible
            )
            if eligible
            else []
        )
        # No PostgreSQL rows, job idempotency keys, checkpoints or assets are deleted.
        return {
            "mode": "apply" if apply else "preview",
            "stream": kind,
            "retention_days": days,
            "scanned": len(entries),
            "eligible_after_group_checks": len(allowed),
            "deleted": len(allowed) if apply else 0,
            "protected_or_unrecognized": len(entries) - len(allowed),
            "next_cursor": entries[-1][0] if entries else after,
            "batch_full": len(entries) == batch,
            "notice": "Pending, undelivered, active-job and unrecognized entries are retained.",
        }


async def recovery_snapshot(session) -> dict:
    row = (
        (
            await session.execute(
                text("""SELECT
        count(*) FILTER(WHERE status='QUEUED' AND available_at<=now() AND attempt<max_attempts
            AND cancel_requested=false) AS due_queued,
        count(*) FILTER(WHERE status='RUNNING' AND lease_until<now() AND attempt<max_attempts
            AND cancel_requested=false) AS reclaimable,
        count(*) FILTER(WHERE status='RUNNING' AND lease_until<now() AND attempt>=max_attempts) AS exhausted
        FROM jobs""")
            )
        )
        .mappings()
        .one()
    )
    return dict(row)


async def recover_notifications(sessions, batch: int = 100, apply: bool = False) -> dict:
    if not 1 <= batch <= 500:
        raise ValueError("INVALID_MAINTENANCE_LIMIT")
    async with sessions() as session:
        before = await recovery_snapshot(session)
    if not apply:
        return {
            "mode": "preview",
            "eligible": before,
            "exhausted_failed": 0,
            "notifications_created": 0,
        }
    from app.worker import recover_exhausted

    exhausted = await recover_exhausted(sessions, batch)
    async with sessions() as session:
        rows = (
            (
                await session.execute(
                    text("""SELECT j.id,j.owner_id FROM jobs j
            WHERE cancel_requested=false AND attempt<max_attempts AND
            ((status='QUEUED' AND available_at<=now()) OR (status='RUNNING' AND lease_until<now()))
            AND NOT EXISTS(SELECT 1 FROM outbox_events e WHERE e.aggregate_id=j.id
                AND e.event_type='job.ready' AND e.published_at IS NULL)
            ORDER BY available_at,created_at FOR UPDATE OF j SKIP LOCKED LIMIT :batch"""),
                    {"batch": batch},
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            await session.execute(
                text("""INSERT INTO outbox_events
                (id,owner_id,event_type,aggregate_id,payload)
                VALUES (:id,:owner,'job.ready',:job,'{}'::jsonb)"""),
                {"id": str(uuid4()), "owner": str(row["owner_id"]), "job": str(row["id"])},
            )
        await session.commit()
    return {
        "mode": "apply",
        "eligible": before,
        "exhausted_failed": exhausted,
        "notifications_created": len(rows),
        "notice": "No handler executed, attempt reset or terminal-job retry. Worker handles execution.",
    }
