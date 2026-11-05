"""Consented reference-conditioned generation through a private local model bridge."""

import asyncio
import base64
import hashlib
import json
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import text

from app.config import get_settings
from app.identity import Session, User
from app.storage.s3 import S3Storage
from app.upload_workflows import asset_owned

router = APIRouter(prefix="/v1/singing", tags=["singing"])


class GenerationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(default="", max_length=4000)
    lyrics: str = Field(min_length=1, max_length=2000)
    style: str = Field(default="Chinese solo vocal, clean singing", min_length=1, max_length=500)
    duration_seconds: int = Field(default=10, ge=5, le=60)
    seed: int = Field(default=42, ge=0, le=2147483647)
    consent_id: UUID
    input_asset_id: str | None = None


class VoiceConsent(BaseModel):
    asset_id: UUID
    owns_voice: bool
    permits_generation: bool
    statement_version: str = Field(pattern="^voice-consent-v1$")

    @model_validator(mode="after")
    def explicit_consent(self):
        if not self.owns_voice or not self.permits_generation:
            raise ValueError("explicit voice authorization required")
        return self


@router.get("/consents")
async def list_consents(user: User, session: Session):
    from fastapi.encoders import jsonable_encoder

    rows = (
        await session.execute(
            text("""SELECT id,asset_id,created_at,revoked_at
      FROM generation_consents WHERE owner_id=:owner ORDER BY created_at DESC LIMIT 100"""),
            {"owner": user["id"]},
        )
    ).mappings()
    return jsonable_encoder([dict(row) for row in rows])


@router.post("/consents", status_code=201)
async def consent(body: VoiceConsent, user: User, session: Session):
    asset = await asset_owned(session, body.asset_id, user["id"])
    if asset["status"] != "ready":
        raise HTTPException(409, "voice sample is not ready")
    identifier = str(uuid4())
    await session.execute(
        text("""INSERT INTO generation_consents
      (id,owner_id,asset_id,statement_version) VALUES (:id,:owner,:asset,:version)"""),
        {
            "id": identifier,
            "owner": user["id"],
            "asset": str(body.asset_id),
            "version": body.statement_version,
        },
    )
    from app.security import audit

    await audit(session, user["id"], "voice.consented", str(body.asset_id))
    await session.commit()
    return {"id": identifier, "asset_id": str(body.asset_id)}


@router.delete("/consents/{consent_id}", status_code=204)
async def revoke(consent_id: UUID, user: User, session: Session):
    await session.execute(text("SELECT id FROM users WHERE id=:id FOR UPDATE"), {"id": user["id"]})
    result = await session.execute(
        text("""UPDATE generation_consents SET revoked_at=now(),revision=revision+1
      WHERE id=:id AND owner_id=:owner"""),
        {"id": str(consent_id), "owner": user["id"]},
    )
    if not result.rowcount:
        raise HTTPException(404, "consent not found")
    # Revoke publication as well as future dispatches.
    await session.execute(
        text("""UPDATE jobs SET status='CANCELLED',cancel_requested=true,
      version=version+1,worker_token=NULL,lease_until=NULL WHERE owner_id=:owner
      AND kind='singing' AND status IN ('QUEUED','RUNNING') AND request->'input'->>'consent_id'=:id"""),
        {"owner": user["id"], "id": str(consent_id)},
    )
    await session.commit()


def available(settings):
    return (
        settings.singing_enabled
        and bool(settings.singing_service_url)
        and len(settings.singing_service_token) >= 32
    )


async def service_ready(settings=None):
    settings = settings or get_settings()
    if not available(settings):
        return False
    try:
        async with httpx.AsyncClient(timeout=2, trust_env=False) as client:
            response = await client.get(
                settings.singing_service_url.rstrip("/") + "/health",
                headers={"Authorization": "Bearer " + settings.singing_service_token},
            )
            response.raise_for_status()
            return response.json().get("status") == "ready"
    except (httpx.HTTPError, ValueError):
        return False


async def validate_consent(session, owner, config, asset_id):
    valid = await session.scalar(
        text("""SELECT 1 FROM generation_consents
      WHERE id=:id AND owner_id=:owner AND asset_id=:asset AND revoked_at IS NULL"""),
        {"id": str(config.consent_id), "owner": owner, "asset": str(asset_id)},
    )
    if not valid:
        raise HTTPException(403, "active voice authorization required")


async def generate(state, claim_context):
    from app.audio import AssessmentInput
    from app.audio_process import assess_isolated
    from app.database import _session_factory
    from app.worker import PermanentJobError, RetryableJobError

    settings = get_settings()
    if not available(settings):
        return {"status": "disabled", "reason": "EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED"}
    config = GenerationInput.model_validate(state["input"])
    async with _session_factory()() as session:
        await validate_consent(session, state["owner_id"], config, config.input_asset_id)
        sample = await asset_owned(session, config.input_asset_id, state["owner_id"])
        if sample["status"] != "ready":
            raise PermanentJobError("READY_ASSET_REQUIRED")
    storage = S3Storage.from_settings(settings)

    def read():
        response = storage.client.get_object(
            Bucket=storage.config.bucket, Key=sample["verified_key"] or sample["object_key"]
        )
        body = response["Body"]
        try:
            data = body.read(settings.max_audio_size_bytes + 1)
        finally:
            body.close()
        if hashlib.sha256(data).hexdigest() != sample["sha256"]:
            raise ValueError("ASSET_CONTENT_MISMATCH")
        return data

    data = await asyncio.to_thread(read)
    url = settings.singing_service_url.rstrip("/")
    headers = {"Authorization": f"Bearer {settings.singing_service_token}"}
    identifier = str(uuid4())
    # The bridge's idempotency namespace is unique to this worker claim; cancellations
    # cannot kill a replacement attempt that reclaimed the same business job.
    bridge_job = str(claim_context["worker_token"])
    try:
        async with httpx.AsyncClient(timeout=1750, trust_env=False) as client:
            async with client.stream(
                "POST",
                url + "/generate",
                headers=headers,
                json={
                    "job_id": bridge_job,
                    "owner_id": state["owner_id"],
                    "reference_audio_base64": base64.b64encode(data).decode(),
                    "lyrics": config.lyrics,
                    "style": config.style,
                    "duration_seconds": config.duration_seconds,
                    "seed": config.seed,
                },
            ) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > settings.max_audio_size_bytes * 4 // 3 + 65536:
                        raise PermanentJobError("GENERATED_AUDIO_TOO_LARGE")
            encoded = json.loads(chunks)
            audio = base64.b64decode(encoded["audio_base64"], validate=True)
        if len(audio) > settings.max_audio_size_bytes:
            raise PermanentJobError("GENERATED_AUDIO_TOO_LARGE")
        probe = await assess_isolated(audio, AssessmentInput(), settings)
        key = f"users/{state['owner_id']}/generated/{identifier}.wav"
        await asyncio.to_thread(
            storage.client.put_object,
            Bucket=storage.config.bucket,
            Key=key,
            Body=audio,
            ContentType="audio/wav",
            ServerSideEncryption="AES256",
        )
        return {
            "status": "generated",
            "asset_id": identifier,
            "verified_key": key,
            "sha256": hashlib.sha256(audio).hexdigest(),
            "size_bytes": len(audio),
            "duration_ms": probe["duration_ms"],
            "consent_id": str(config.consent_id),
            "engine": encoded["engine"],
            "voice_identity_guaranteed": False,
        }
    except asyncio.CancelledError:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            try:
                await client.delete(url + f"/jobs/{bridge_job}", headers=headers)
            except httpx.HTTPError:
                pass
        raise
    except (httpx.HTTPError, KeyError, ValueError) as exc:
        raise RetryableJobError("SINGING_SERVICE_UNAVAILABLE") from exc


async def publish_generation(session, job, result):
    if job["kind"] != "singing" or result.get("status") != "generated":
        return result
    config = GenerationInput.model_validate(job["request"]["input"])
    try:
        await validate_consent(
            session, str(job["owner_id"]), config, job["request"]["input_asset_id"]
        )
    except HTTPException:
        return {"status": "refused", "reason": "VOICE_CONSENT_REVOKED"}
    await session.execute(
        text("""INSERT INTO audio_assets
      (id,owner_id,object_key,verified_key,sha256,content_type,size_bytes,duration_ms,purpose,status,filename,completed_at)
      VALUES (:id,:owner,:key,:key,:hash,'audio/wav',:size,:duration,'generated_reference','ready','生成歌声.wav',now())
      ON CONFLICT(id) DO NOTHING"""),
        {
            "id": result["asset_id"],
            "owner": str(job["owner_id"]),
            "key": result["verified_key"],
            "hash": result["sha256"],
            "size": result["size_bytes"],
            "duration": result["duration_ms"],
        },
    )
    return {key: value for key, value in result.items() if key not in {"verified_key", "sha256"}}
