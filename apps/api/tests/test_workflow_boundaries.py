import asyncio
import io
import os
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from test_jobs import database_case, submit

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")


def test_quota_rejects_new_work_but_preserves_idempotent_retry(monkeypatch):
    from app import security
    from app.config import Settings

    monkeypatch.setattr(security, "get_settings", lambda: Settings(jobs_concurrent_limit=1))

    async def run():
        async with database_case() as (sessions, owner):
            key = str(uuid4())
            first = await submit(sessions, owner, key)
            assert (await submit(sessions, owner, key))["id"] == first["id"]
            with pytest.raises(HTTPException) as error:
                await submit(sessions, owner)
            assert error.value.status_code == 429

    asyncio.run(run())


def test_revoked_voice_consent_fences_publication():
    from app.jobs import JobCreate, enqueue
    from app.singing import VoiceConsent, consent, revoke
    from app.worker import claim, finish

    async def run():
        async with database_case() as (sessions, owner):
            sample = str(uuid4())
            async with sessions() as session:
                await session.execute(
                    text("""INSERT INTO audio_assets
                  (id,owner_id,object_key,sha256,content_type,size_bytes,purpose,status)
                  VALUES (:id,:owner,:key,:hash,'audio/wav',10,'assessment','ready')"""),
                    {"id": sample, "owner": owner, "key": f"test/{sample}", "hash": "a" * 64},
                )
                await session.commit()
                granted = await consent(
                    VoiceConsent(
                        asset_id=sample,
                        owns_voice=True,
                        permits_generation=True,
                        statement_version="voice-consent-v1",
                    ),
                    {"id": owner},
                    session,
                )
            async with sessions() as session:
                queued = await enqueue(
                    session,
                    owner,
                    JobCreate(
                        kind="singing",
                        input={"lyrics": "原创歌词", "consent_id": granted["id"]},
                        input_asset_id=sample,
                        idempotency_key=str(uuid4()),
                    ),
                )
            claimed = await claim(sessions, queued["id"])
            async with sessions() as session:
                await revoke(UUID(granted["id"]), {"id": owner}, session)
            assert not await finish(
                sessions, claimed, {"status": "generated", "asset_id": str(uuid4())}, None
            )
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM audio_assets WHERE owner_id=:owner"),
                        {"owner": owner},
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        text("SELECT status FROM jobs WHERE id=:id"), {"id": queued["id"]}
                    )
                    == "CANCELLED"
                )

    asyncio.run(run())


def test_invalid_upload_is_not_published_and_completion_is_owner_scoped(monkeypatch):
    from app import database
    from app.storage.s3 import S3Storage
    from app.upload_workflows import complete_upload
    from app.worker import process_one

    class Storage:
        config = type("Config", (), {"bucket": "test"})()
        client = None

        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(b"tampered"), "ContentLength": 8}

    storage = Storage()
    storage.client = storage
    monkeypatch.setattr(S3Storage, "from_settings", lambda _: storage)

    async def run():
        async with database_case() as (sessions, owner):
            monkeypatch.setattr(database, "_sessions", sessions)
            asset = str(uuid4())
            async with sessions() as session:
                await session.execute(
                    text("""INSERT INTO audio_assets
                  (id,owner_id,object_key,sha256,content_type,size_bytes,purpose,upload_expires_at)
                  VALUES (:id,:owner,:key,:hash,'audio/wav',8,'assessment',now()+interval '10 minutes')"""),
                    {"id": asset, "owner": owner, "key": f"test/{asset}", "hash": "a" * 64},
                )
                await session.commit()
            async with sessions() as session:
                with pytest.raises(HTTPException) as error:
                    await complete_upload(UUID(asset), {"id": str(uuid4())}, session)
                assert error.value.status_code == 404
            for _ in range(2):
                async with sessions() as session:
                    assert (await complete_upload(UUID(asset), {"id": owner}, session))[
                        "status"
                    ] == "verifying"
            async with sessions() as session:
                job = await session.scalar(
                    text("SELECT verification_job_id FROM audio_assets WHERE id=:id"), {"id": asset}
                )
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM jobs WHERE owner_id=:owner"), {"owner": owner}
                    )
                    == 1
                )
            assert await process_one(sessions, job_id=str(job))
            async with sessions() as session:
                row = (
                    await session.execute(
                        text("SELECT status,verified_key FROM audio_assets WHERE id=:id"),
                        {"id": asset},
                    )
                ).one()
                assert row.status == "invalid" and row.verified_key is None

    asyncio.run(run())
