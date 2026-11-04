from __future__ import annotations

import hashlib
import json
import uuid
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity import Session, User

router = APIRouter(prefix="/v1/jobs", tags=["jobs"])


class JobCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["knowledge", "planning", "assessment", "singing", "evaluation"]
    input: dict = Field(default_factory=dict)
    input_asset_id: uuid.UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


async def enqueue(
    session: AsyncSession, owner: str, request: JobCreate, *, automatic: bool = False
) -> dict:
    try:
        if request.kind == "knowledge":
            from app.knowledge.policy import KnowledgeInput

            KnowledgeInput.model_validate(request.input)
        elif request.kind == "planning":
            from app.planning import PlanningInput

            PlanningInput.model_validate(request.input)
        elif request.kind == "assessment":
            from app.audio import AssessmentInput

            AssessmentInput.model_validate(request.input)
    except ValidationError as exc:
        raise HTTPException(422, "invalid structured job input: " + str(exc)) from exc
    body = request.model_dump(mode="json", exclude={"idempotency_key"})
    # Server-only provenance, outside client-controlled input. Included in idempotency hash.
    if automatic:
        body["routing"] = {"mode": "automatic", "version": "rules-v2"}
    if len(json.dumps(body, ensure_ascii=False).encode()) > 65536:
        raise HTTPException(413, "job input exceeds 64 KiB")
    digest = hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()
    # The unique constraint is the concurrency barrier, not an application-only precheck.
    if request.kind == "assessment" and request.input_asset_id is None:
        raise HTTPException(422, "assessment requires a verified ready audio asset")
    if request.input_asset_id:
        asset = (
            (
                await session.execute(
                    text("SELECT status FROM audio_assets WHERE id=:id AND owner_id=:owner"),
                    {"id": str(request.input_asset_id), "owner": owner},
                )
            )
            .mappings()
            .first()
        )
        if asset is None:
            raise HTTPException(404, "audio asset not found")
        if asset["status"] != "ready":
            raise HTTPException(
                409, "audio asset is pending or unverified; presign-only uploads cannot be assessed"
            )
    job_id = str(uuid.uuid4())
    row = (
        (
            await session.execute(
                text("""
        INSERT INTO jobs (id,owner_id,kind,input_asset_id,request,trace_id,idempotency_key,request_hash)
        VALUES (:id,:owner,:kind,:asset,CAST(:request AS jsonb),:trace,:key,:hash)
        ON CONFLICT (owner_id,idempotency_key) DO NOTHING RETURNING *
    """),
                {
                    "id": job_id,
                    "owner": owner,
                    "kind": request.kind,
                    "asset": str(request.input_asset_id) if request.input_asset_id else None,
                    "request": json.dumps(body),
                    "trace": str(uuid.uuid4()),
                    "key": request.idempotency_key,
                    "hash": digest,
                },
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        row = (
            (
                await session.execute(
                    text("SELECT * FROM jobs WHERE owner_id=:owner AND idempotency_key=:key"),
                    {"owner": owner, "key": request.idempotency_key},
                )
            )
            .mappings()
            .one()
        )
        if row["request_hash"] != digest:
            raise HTTPException(409, "idempotency key reused with different input")
    else:
        await session.execute(
            text("""
            INSERT INTO outbox_events(id,owner_id,event_type,aggregate_id,payload)
            VALUES (:event,:owner,'job.ready',:id,CAST(:payload AS jsonb))
        """),
            {
                "event": str(uuid.uuid4()),
                "owner": owner,
                "id": job_id,
                "payload": json.dumps({"job_id": job_id, "kind": request.kind}),
            },
        )
    await session.commit()
    return public_job(row)


def public_job(row) -> dict:
    return jsonable_encoder(
        {
            key: row[key]
            for key in (
                "id",
                "kind",
                "status",
                "input_asset_id",
                "result",
                "error_code",
                "attempt",
                "max_attempts",
                "cancel_requested",
                "trace_id",
                "created_at",
                "updated_at",
            )
        }
    )


@router.post("", status_code=202)
async def create_job(request: JobCreate, user: User, session: Session) -> dict:
    return await enqueue(session, user["id"], request)


@router.get("")
async def list_jobs(
    user: User, session: Session, limit: int = Query(30, ge=1, le=100)
) -> list[dict]:
    rows = (
        await session.execute(
            text(
                "SELECT * FROM jobs WHERE owner_id=:owner ORDER BY created_at DESC,id DESC LIMIT :limit"
            ),
            {"owner": user["id"], "limit": limit},
        )
    ).mappings()
    return [public_job(row) for row in rows]


@router.get("/{job_id}")
async def read_job(job_id: uuid.UUID, user: User, session: Session) -> dict:
    row = (
        (
            await session.execute(
                text("SELECT * FROM jobs WHERE id=:id AND owner_id=:owner"),
                {"id": str(job_id), "owner": user["id"]},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "job not found")
    return public_job(row)


@router.post("/{job_id}/cancel")
async def cancel_job(job_id: uuid.UUID, user: User, session: Session) -> dict:
    row = (
        (
            await session.execute(
                text("SELECT * FROM jobs WHERE id=:id AND owner_id=:owner FOR UPDATE"),
                {"id": str(job_id), "owner": user["id"]},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "job not found")
    if row["status"] in {"QUEUED", "RUNNING"}:
        row = (
            (
                await session.execute(
                    text("""
            UPDATE jobs SET status='CANCELLED',cancel_requested=true,version=version+1,
            worker_token=NULL,lease_until=NULL,updated_at=now()
            WHERE id=:id AND owner_id=:owner RETURNING *
        """),
                    {"id": str(job_id), "owner": user["id"]},
                )
            )
            .mappings()
            .one()
        )
    await session.commit()
    return public_job(row)
