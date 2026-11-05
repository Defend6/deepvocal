"""Publish only a decoded, hash-verified snapshot. Incoming PUT URLs never address it."""

import asyncio
import hashlib
import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from sqlalchemy import text

from app.config import get_settings
from app.identity import Session, User
from app.storage.s3 import S3Storage

router = APIRouter(prefix="/v1/uploads", tags=["audio assets"])


async def asset_owned(session, asset_id, owner, lock=False):
    row = (
        (
            await session.execute(
                text(
                    "SELECT * FROM audio_assets WHERE id=:id AND owner_id=:owner"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"id": str(asset_id), "owner": owner},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "audio asset not found")
    return row


def public_asset(row):
    return jsonable_encoder(
        {
            key: row[key]
            for key in (
                "id",
                "filename",
                "status",
                "duration_ms",
                "size_bytes",
                "purpose",
                "error_code",
            )
        }
    )


@router.get("/{asset_id}")
async def get_asset(asset_id: UUID, user: User, session: Session):
    return public_asset(await asset_owned(session, asset_id, user["id"]))


@router.post("/{asset_id}/complete", status_code=202)
async def complete_upload(asset_id: UUID, user: User, session: Session):
    row = await asset_owned(session, asset_id, user["id"], True)
    if row["status"] in {"ready", "verifying"}:
        return public_asset(row)
    if row["status"] != "pending":
        raise HTTPException(409, "upload cannot be completed; request a new upload")
    if row["upload_expires_at"] and row["upload_expires_at"] <= datetime.now(UTC):
        await session.execute(
            text("UPDATE audio_assets SET status='expired' WHERE id=:id"), {"id": str(asset_id)}
        )
        await session.commit()
        raise HTTPException(410, "upload expired; request a new upload")
    job_id = str(uuid4())
    payload = {"asset_id": str(asset_id), "source_key": row["object_key"]}
    await session.execute(
        text("""INSERT INTO jobs
      (id,owner_id,kind,request,trace_id,idempotency_key,request_hash)
      VALUES (:id,:owner,'upload_verify',CAST(:request AS jsonb),:trace,:key,:hash)"""),
        {
            "id": job_id,
            "trace": str(uuid4()),
            "owner": user["id"],
            "request": json.dumps(payload),
            "key": f"verify:{asset_id}",
            "hash": hashlib.sha256(str(asset_id).encode()).hexdigest(),
        },
    )
    await session.execute(
        text("""INSERT INTO outbox_events
      (id,owner_id,event_type,aggregate_id,payload)
      VALUES (:id,:owner,'job.ready',:job,'{}')"""),
        {"id": str(uuid4()), "owner": user["id"], "job": job_id},
    )
    await session.execute(
        text("""UPDATE audio_assets SET status='verifying',
      verification_job_id=:job,error_code=NULL WHERE id=:id AND owner_id=:owner"""),
        {"id": str(asset_id), "owner": user["id"], "job": job_id},
    )
    await session.commit()
    return {"id": str(asset_id), "status": "verifying"}


@router.get("/{asset_id}/playback")
async def playback(asset_id: UUID, user: User, session: Session):
    row = await asset_owned(session, asset_id, user["id"])
    if row["status"] != "ready" or not row["verified_key"]:
        raise HTTPException(409, "audio is not ready")
    storage = S3Storage.from_settings(get_settings())
    url = getattr(storage, "signing_client", storage.client).generate_presigned_url(
        "get_object",
        Params={"Bucket": storage.config.bucket, "Key": row["verified_key"]},
        ExpiresIn=300,
    )
    return {"url": url, "expires_in": 300}


@router.delete("/{asset_id}", status_code=202)
async def delete_asset(asset_id: UUID, user: User, session: Session):
    row = await asset_owned(session, asset_id, user["id"], True)
    if row["status"] != "deleted":
        await session.execute(
            text("UPDATE audio_assets SET status='pending_delete' WHERE id=:id"),
            {"id": str(asset_id)},
        )
        await session.commit()
    return {"status": "pending_delete" if row["status"] != "deleted" else "deleted"}


async def verify_upload(job):
    from app.audio import AssessmentInput
    from app.audio_process import assess_isolated
    from app.database import _session_factory
    from app.worker import PermanentJobError, RetryableJobError

    settings = get_settings()
    async with _session_factory()() as session:
        row = await asset_owned(session, job["request"]["asset_id"], str(job["owner_id"]))
        if row["status"] != "verifying" or str(row["verification_job_id"]) != str(job["id"]):
            raise PermanentJobError("UPLOAD_NO_LONGER_ACTIVE")
    storage = S3Storage.from_settings(settings)

    def snapshot():
        response = storage.client.get_object(Bucket=storage.config.bucket, Key=row["object_key"])
        body = response["Body"]
        try:
            if response["ContentLength"] != row["size_bytes"]:
                raise ValueError("AUDIO_SIZE_MISMATCH")
            data = body.read(settings.max_audio_size_bytes + 1)
        finally:
            body.close()
        if len(data) != row["size_bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("AUDIO_HASH_MISMATCH")
        return data

    try:
        data = await asyncio.to_thread(snapshot)
        probe = await assess_isolated(data, AssessmentInput(), settings)
        # Unique attempt key: expired workers cannot overwrite a newer attempt's snapshot.
        key = f"users/{job['owner_id']}/verified/{row['id']}/{job['worker_token']}.audio"
        await asyncio.to_thread(
            storage.client.put_object,
            Bucket=storage.config.bucket,
            Key=key,
            Body=data,
            ContentType=row["content_type"],
            ServerSideEncryption="AES256",
        )
    except ValueError as exc:
        raise PermanentJobError(str(exc)[:80]) from exc
    except Exception as exc:
        raise RetryableJobError("UPLOAD_STORAGE_UNAVAILABLE") from exc
    return {
        "status": "verified",
        "asset_id": str(row["id"]),
        "verified_key": key,
        "duration_ms": probe["duration_ms"],
    }


async def finish_verification(session, job, result, error, retry):
    if job["kind"] != "upload_verify" or retry:
        return
    await session.execute(
        text("""UPDATE audio_assets SET
      status=:status, verified_key=:key, duration_ms=:duration,
      completed_at=CASE WHEN :ok THEN now() ELSE NULL END, error_code=:error
      WHERE id=:asset AND owner_id=:owner AND verification_job_id=:job AND status='verifying'"""),
        {
            "status": "ready" if not error else "invalid",
            "key": (result or {}).get("verified_key"),
            "duration": (result or {}).get("duration_ms"),
            "ok": not bool(error),
            "error": error,
            "asset": job["request"]["asset_id"],
            "owner": str(job["owner_id"]),
            "job": str(job["id"]),
        },
    )
