"""Confirmed user preferences only; deterministic compaction never diagnoses a user."""

import json
from typing import Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.identity import Session, User

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class Consent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    revision: int = Field(ge=1)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    goal: Literal["foundation", "pitch", "rhythm", "stability"]
    minutes: int = Field(ge=5, le=30)


async def settings_row(session, owner, lock=False):
    await session.execute(
        text("INSERT INTO memory_settings(owner_id) VALUES (:owner) ON CONFLICT DO NOTHING"),
        {"owner": owner},
    )
    return (
        (
            await session.execute(
                text(
                    "SELECT * FROM memory_settings WHERE owner_id=:owner"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"owner": owner},
            )
        )
        .mappings()
        .one()
    )


@router.get("")
async def read_memory(user: User, session: Session):
    row = await settings_row(session, user["id"])
    facts = (
        (
            await session.execute(
                text(
                    "SELECT semantic_key,value,source_event_id,revision,status FROM memory_facts WHERE owner_id=:owner AND status='confirmed' ORDER BY semantic_key"
                ),
                {"owner": user["id"]},
            )
        )
        .mappings()
        .all()
    )
    events = (
        (
            await session.execute(
                text(
                    "SELECT id,event_kind,payload,revision,created_at FROM memory_events WHERE owner_id=:owner ORDER BY created_at DESC,id DESC LIMIT 20"
                ),
                {"owner": user["id"]},
            )
        )
        .mappings()
        .all()
    )
    await session.commit()
    return jsonable_encoder(
        {
            "enabled": row["enabled"],
            "revision": row["revision"],
            "l1_events": [dict(r) for r in events],
            "l2_facts": [dict(r) for r in facts],
            "l3_profile": row["profile"],
            "compaction": "deterministic_confirmed_preferences_v1",
        }
    )


@router.put("/consent")
async def consent(request: Consent, user: User, session: Session):
    row = await settings_row(session, user["id"], True)
    if row["revision"] != request.revision:
        raise HTTPException(409, "memory changed; reload")
    await session.execute(
        text(
            "UPDATE memory_settings SET enabled=:enabled,revision=revision+1,updated_at=now() WHERE owner_id=:owner"
        ),
        {"enabled": request.enabled, "owner": user["id"]},
    )
    await session.commit()
    return await read_memory(user, session)


@router.put("/preferences")
async def preferences(request: Preferences, user: User, session: Session):
    row = await settings_row(session, user["id"], True)
    if not row["enabled"]:
        raise HTTPException(409, "enable cross-session memory first")
    if row["revision"] != request.revision:
        raise HTTPException(409, "memory changed; reload")
    revision, event_id = row["revision"] + 1, str(uuid4())
    values = request.model_dump(exclude={"revision"})
    await session.execute(
        text("""INSERT INTO memory_events(id,owner_id,source_key,event_kind,payload,revision)
        VALUES (:id,:owner,:source,'user.preferences',CAST(:payload AS jsonb),:revision)"""),
        {
            "id": event_id,
            "owner": user["id"],
            "source": f"preferences:{revision}",
            "payload": json.dumps(values),
            "revision": revision,
        },
    )
    for key, value in values.items():
        await session.execute(
            text("""INSERT INTO memory_facts(owner_id,semantic_key,value,source_event_id,revision,status)
            VALUES (:owner,:key,CAST(:value AS jsonb),:event,:revision,'confirmed')
            ON CONFLICT(owner_id,semantic_key) DO UPDATE SET value=EXCLUDED.value,source_event_id=EXCLUDED.source_event_id,
            revision=EXCLUDED.revision,status='confirmed'"""),
            {
                "owner": user["id"],
                "key": key,
                "value": json.dumps(value),
                "event": event_id,
                "revision": revision,
            },
        )
    await compact(session, user["id"], revision)
    await session.commit()
    return await read_memory(user, session)


async def compact(session, owner, revision):
    facts = (
        (
            await session.execute(
                text(
                    "SELECT semantic_key,value,source_event_id FROM memory_facts WHERE owner_id=:owner AND status='confirmed'"
                ),
                {"owner": owner},
            )
        )
        .mappings()
        .all()
    )
    profile = {
        "preferences": {r["semantic_key"]: r["value"] for r in facts},
        "source_event_ids": sorted({str(r["source_event_id"]) for r in facts}),
        "confidence_class": "user_confirmed",
        "version": "memory-v1",
    }
    await session.execute(
        text(
            "UPDATE memory_settings SET profile=CAST(:profile AS jsonb),revision=:revision,updated_at=now() WHERE owner_id=:owner"
        ),
        {"profile": json.dumps(profile), "revision": revision, "owner": owner},
    )


@router.delete("/facts/{key}")
async def delete_fact(key: str, user: User, session: Session):
    row = await settings_row(session, user["id"], True)
    if key not in {"goal", "minutes"}:
        raise HTTPException(404, "fact not found")
    await session.execute(
        text(
            "UPDATE memory_facts SET status='deleted',value=NULL,revision=:revision WHERE owner_id=:owner AND semantic_key=:key"
        ),
        {"revision": row["revision"] + 1, "owner": user["id"], "key": key},
    )
    await compact(session, user["id"], row["revision"] + 1)
    await session.commit()
    return await read_memory(user, session)


@router.delete("")
async def clear_memory(user: User, session: Session):
    row = await settings_row(session, user["id"], True)
    await session.execute(
        text("DELETE FROM memory_facts WHERE owner_id=:owner"), {"owner": user["id"]}
    )
    await session.execute(
        text("DELETE FROM memory_events WHERE owner_id=:owner"), {"owner": user["id"]}
    )
    await session.execute(
        text(
            "UPDATE memory_settings SET enabled=false,profile='{}'::jsonb,revision=:revision,updated_at=now() WHERE owner_id=:owner"
        ),
        {"owner": user["id"], "revision": row["revision"] + 1},
    )
    await session.commit()
    return {
        "status": "cleared",
        "scope": "memory tables only; source reports, plans and LangGraph checkpoints retain their own lifecycle",
    }


async def effective_preferences(session, owner):
    row = (
        (
            await session.execute(
                text("SELECT enabled,profile FROM memory_settings WHERE owner_id=:owner"),
                {"owner": owner},
            )
        )
        .mappings()
        .first()
    )
    return row["profile"].get("preferences", {}) if row and row["enabled"] else {}


async def record_completion(session, job, result):
    if job["kind"] == "memory":
        return
    row = await settings_row(session, str(job["owner_id"]), True)
    if not row["enabled"]:
        return
    revision = row["revision"] + 1
    payload = {
        "kind": job["kind"],
        "result_status": result.get("status"),
        "artifact_id": result.get("plan_id") or result.get("report_id"),
    }
    event = await session.scalar(
        text("""INSERT INTO memory_events(id,owner_id,source_key,event_kind,payload,revision)
        VALUES (:id,:owner,:key,'task.completed',CAST(:payload AS jsonb),:revision)
        ON CONFLICT(owner_id,source_key) DO NOTHING RETURNING id"""),
        {
            "id": str(uuid4()),
            "owner": str(job["owner_id"]),
            "key": f"job:{job['id']}",
            "payload": json.dumps(payload),
            "revision": revision,
        },
    )
    if event:
        await session.execute(
            text(
                "UPDATE memory_settings SET revision=:revision,updated_at=now() WHERE owner_id=:owner"
            ),
            {"revision": revision, "owner": str(job["owner_id"])},
        )
        # Keep user preference patches immediately visible, compact task metadata asynchronously.
        compact_job = str(uuid4())
        key = f"memory:{job['owner_id']}:{revision}"
        request = {"input": {"expected_revision": revision, "high_watermark": str(event)}}
        await session.execute(
            text("""INSERT INTO jobs(id,owner_id,kind,request,trace_id,idempotency_key,request_hash)
            VALUES (:id,:owner,'memory',CAST(:request AS jsonb),:trace,:key,:hash)"""),
            {
                "id": compact_job,
                "owner": str(job["owner_id"]),
                "request": json.dumps(request),
                "trace": str(uuid4()),
                "key": key,
                "hash": key,
            },
        )
        await session.execute(
            text("""INSERT INTO outbox_events(id,owner_id,event_type,aggregate_id,payload)
            VALUES (:id,:owner,'job.ready',:job,'{}'::jsonb)"""),
            {"id": str(uuid4()), "owner": str(job["owner_id"]), "job": compact_job},
        )


async def complete_compaction(session, job):
    """Only called by fenced finish; cancelled or stale workers cannot alter memory."""
    owner = str(job["owner_id"])
    row = await settings_row(session, owner, True)
    if not row["enabled"] or row["revision"] != job["request"]["input"]["expected_revision"]:
        return {"status": "superseded", "reason": "MEMORY_REVISION_OR_CONSENT_CHANGED"}
    await compact(session, owner, row["revision"])
    return {
        "status": "compacted",
        "revision": row["revision"],
        "compactor_version": "confirmed-preferences-v1",
    }
