"""Explicit erasure ledger. Revoke writes first, then erase after worker leases expire."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import text

from app.config import get_settings
from app.identity import Session, User
from app.storage.s3 import S3Storage

router = APIRouter(prefix="/v1/privacy", tags=["privacy"])


class ErasureRequest(BaseModel):
    confirmation: str = Field(pattern="^删除我的全部项目数据$")


@router.post("/erase", status_code=202)
async def erase(body: ErasureRequest, user: User, session: Session):
    await session.execute(
        text("SELECT id FROM users WHERE id=:owner FOR UPDATE"), {"owner": user["id"]}
    )
    await session.execute(
        text("UPDATE users SET status='pending_delete' WHERE id=:owner"), {"owner": user["id"]}
    )
    await session.execute(
        text("""UPDATE jobs SET status='CANCELLED',cancel_requested=true,
      version=version+1,worker_token=NULL,lease_until=NULL WHERE owner_id=:owner
      AND status IN ('QUEUED','RUNNING')"""),
        {"owner": user["id"]},
    )
    identifier = str(uuid4())
    await session.execute(
        text("INSERT INTO privacy_requests(id,owner_id) VALUES (:id,:owner)"),
        {"id": identifier, "owner": user["id"]},
    )
    await session.commit()
    return {
        "id": identifier,
        "status": "pending",
        "message": "数据清理已申请，完成后可重新使用账户。",
    }


@router.get("/status")
async def erasure_status(user: User, session: Session):
    from fastapi.encoders import jsonable_encoder

    row = (
        (
            await session.execute(
                text("""SELECT id,status,error_code,created_at,completed_at
      FROM privacy_requests WHERE owner_id=:owner ORDER BY created_at DESC LIMIT 1"""),
                {"owner": user["id"]},
            )
        )
        .mappings()
        .first()
    )
    return jsonable_encoder(dict(row)) if row else {"status": "none"}


async def housekeep(sessions):
    """Run outside business transactions. Fail closed on object store errors; retry next tick."""
    storage = S3Storage.from_settings(get_settings())
    async with sessions() as session:
        await session.execute(
            text("""UPDATE audio_assets SET status='expired',error_code='UPLOAD_EXPIRED'
          WHERE status='pending' AND upload_expires_at<now()""")
        )
        await session.execute(
            text("""UPDATE audio_assets a SET status='invalid',error_code='UPLOAD_NOT_COMPLETED'
          FROM jobs j WHERE a.verification_job_id=j.id AND a.status='verifying'
          AND j.status IN ('FAILED','CANCELLED')""")
        )
        rows = (
            (
                await session.execute(
                    text("""SELECT * FROM audio_assets WHERE
          status IN ('pending_delete','expired','invalid') AND
          (upload_expires_at IS NULL OR upload_expires_at < now()-interval '5 minutes')
          ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 20""")
                )
            )
            .mappings()
            .all()
        )
        for row in rows:
            await asyncio.to_thread(storage.delete_object, row["object_key"])
            if row["verified_key"]:
                await asyncio.to_thread(storage.delete_object, row["verified_key"])
            await session.execute(
                text("UPDATE audio_assets SET status='deleted' WHERE id=:id"),
                {"id": str(row["id"])},
            )
        await session.commit()
    await sweep_snapshots(sessions, storage)
    async with sessions() as session:
        # Delay exceeds configured business task deadlines. Signed incoming PUTs may
        # outlive cancellation, so wait for all of that owner's upload URLs as well.
        requests = (
            (
                await session.execute(
                    text("""SELECT p.* FROM privacy_requests p
          WHERE p.status IN ('pending','retry') AND p.created_at < now()-interval '35 minutes'
          AND NOT EXISTS(SELECT 1 FROM audio_assets a WHERE a.owner_id=p.owner_id
            AND a.upload_expires_at>now())
          ORDER BY p.created_at FOR UPDATE SKIP LOCKED LIMIT 1""")
                )
            )
            .mappings()
            .all()
        )
        for request in requests:
            owner = str(request["owner_id"])
            try:
                used_bridge = await session.scalar(
                    text("""SELECT EXISTS(SELECT 1 FROM jobs
                  WHERE owner_id=:owner AND (kind='singing' OR
                  request->'input'->>'separate_vocals'='true'))"""),
                    {"owner": owner},
                )
                settings = get_settings()
                if used_bridge and settings.singing_service_url and settings.singing_service_token:
                    import httpx

                    async with httpx.AsyncClient(timeout=30, trust_env=False) as client:
                        response = await client.delete(
                            settings.singing_service_url.rstrip("/") + f"/owners/{owner}",
                            headers={"Authorization": "Bearer " + settings.singing_service_token},
                        )
                        response.raise_for_status()
                await asyncio.to_thread(erase_objects, storage, owner)
                # Saver uses owner-prefixed thread IDs; erase blobs, writes and checkpoints.
                for table in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                    exists = await session.scalar(
                        text("SELECT to_regclass(:name)"), {"name": table}
                    )
                    if exists:
                        await session.execute(
                            text(f"DELETE FROM {table} WHERE thread_id LIKE :prefix"),
                            {"prefix": f"owner:{owner}:%"},
                        )
                for table in (
                    "generation_consents",
                    "conversation_turns",
                    "conversations",
                    "song_references",
                    "audit_events",
                    "model_call_reservations",
                    "skill_versions",
                    "memory_facts",
                    "memory_events",
                    "memory_settings",
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
                await session.execute(
                    text("""UPDATE privacy_requests SET status='completed',
                  completed_at=now(),error_code=NULL WHERE id=:id"""),
                    {"id": str(request["id"])},
                )
                await session.execute(
                    text("UPDATE users SET status='active' WHERE id=:owner"), {"owner": owner}
                )
                await session.commit()
            except Exception:  # noqa: BLE001 -- persist retry without exposing private storage errors
                await session.rollback()
                await session.execute(
                    text(
                        "UPDATE privacy_requests SET status='retry',error_code='ERASURE_RETRY' WHERE id=:id"
                    ),
                    {"id": str(request["id"])},
                )
                await session.commit()


async def sweep_snapshots(sessions, storage):
    """Remove snapshots from lost worker claims after the maximum task deadline."""
    cutoff = datetime.now(UTC) - timedelta(hours=2)
    paginator = storage.client.get_paginator("list_objects_v2")
    pages = await asyncio.to_thread(
        lambda: list(paginator.paginate(Bucket=storage.config.bucket, Prefix="users/"))
    )
    removed = 0
    async with sessions() as session:
        for page in pages:
            for item in page.get("Contents", []):
                key = item["Key"]
                if item["LastModified"] >= cutoff or not any(
                    part in key for part in ("/verified/", "/generated/")
                ):
                    continue
                referenced = await session.scalar(
                    text(
                        "SELECT 1 FROM audio_assets WHERE verified_key=:key OR object_key=:key LIMIT 1"
                    ),
                    {"key": key},
                )
                if not referenced:
                    await asyncio.to_thread(storage.delete_object, key)
                    removed += 1
                    if removed >= 50:
                        return


def erase_objects(storage, owner):
    prefix = f"users/{owner}/"
    paginator = storage.client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage.config.bucket, Prefix=prefix):
        for item in page.get("Contents", []):
            if not item["Key"].startswith(prefix):
                raise RuntimeError("object prefix mismatch")
            storage.delete_object(item["Key"])
