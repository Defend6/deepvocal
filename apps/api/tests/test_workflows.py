import asyncio
import base64
import hashlib
import io
import os
from datetime import UTC
from uuid import UUID, uuid4

import jwt
import mido
import numpy as np
import pytest
import soundfile as sf
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from sqlalchemy import text
from test_auth_uploads import make_request
from test_jobs import database_case

from app.auth import authenticate
from app.config import Settings
from app.conversations import ConversationCreate, TurnCreate, create_conversation, post_turn
from app.planning import PlanningInput, create_plan
from app.songs import midi_notes


def test_conversation_replaces_previous_time_constraints():
    from app.conversations import resolve_turn

    resolved = resolve_turn("每天15分钟，共5天", {"last_text": "安排计划，每天10分钟，共3天"})
    assert "10分钟" not in resolved and "3天" not in resolved
    assert "15分钟" in resolved and "5天" in resolved


def test_operator_role_is_required():
    from app.security import require_operator

    with pytest.raises(HTTPException) as error:
        require_operator({"roles": ["learner"]})
    assert error.value.status_code == 403
    require_operator({"roles": ["deepvocal-admin"]})


def test_oidc_verifies_issuer_audience_signature_expiry_and_ignores_debug_header(monkeypatch):
    from datetime import datetime, timedelta
    from types import SimpleNamespace

    from app import auth

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    settings = Settings(
        auth_mode="oidc",
        oidc_issuer="https://identity.test/realm",
        oidc_audience="deepvocal-api",
        oidc_jwks_url="https://identity.test/keys",
    )
    now = datetime.now(UTC)
    claims = {
        "iss": settings.oidc_issuer,
        "sub": "learner",
        "aud": "deepvocal-api",
        "iat": now,
        "exp": now + timedelta(minutes=5),
        "realm_access": {"roles": ["learner"]},
    }
    monkeypatch.setattr(
        auth,
        "jwks_client",
        lambda _: SimpleNamespace(
            get_signing_key_from_jwt=lambda token: SimpleNamespace(key=key.public_key())
        ),
    )

    def principal(payload):
        token = jwt.encode(payload, key, algorithm="RS256")
        return authenticate(
            make_request(
                {"Authorization": f"Bearer {token}", "X-Debug-Identity": "attacker|admin"}
            ),
            settings,
        )

    assert principal(claims).subject == "learner"
    for changed in (
        {"aud": "wrong"},
        {"iss": "https://evil.test"},
        {"exp": now - timedelta(minutes=5)},
        {"typ": "ID"},
    ):
        with pytest.raises(HTTPException) as error:
            principal({**claims, **changed})
        assert error.value.status_code == 401


def test_reference_midi_rejects_polyphony_and_preserves_tempo():
    midi = mido.MidiFile()
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.extend(
        [
            mido.MetaMessage("set_tempo", tempo=1000000),
            mido.Message("note_on", note=60, velocity=80, time=0),
            mido.Message("note_off", note=60, time=480),
        ]
    )
    stream = io.BytesIO()
    midi.save(file=stream)
    notes = midi_notes(base64.b64encode(stream.getvalue()).decode(), 0)
    assert notes[0].end_ms == 1000
    track.insert(2, mido.Message("note_on", note=64, velocity=80, time=0))
    stream = io.BytesIO()
    midi.save(file=stream)
    with pytest.raises(HTTPException):
        midi_notes(base64.b64encode(stream.getvalue()).decode(), 0)


def test_personal_plan_uses_weakest_dimension_and_feedback():
    report = {
        "profile": "reference_song",
        "quality": "usable",
        "total": {"available": True},
        "dimensions": {
            "pitch": {"available": True, "value": 45},
            "rhythm": {"available": True, "value": 85},
        },
    }
    result = create_plan(PlanningInput(minutes=20), report, [{"difficulty": 5}])
    assert result["plan"]["goal"] == "pitch"
    assert result["plan"]["minutes"] == 16
    assert result["plan"]["adaptation"]
    assert create_plan(PlanningInput(), report, [{"discomfort": True}])["status"] == "refused"


def test_global_alignment_recovers_offset_and_keeps_bad_pitch_unavailable():
    from app.alignment import global_alignment
    from app.audio import ReferenceNote

    notes = [
        ReferenceNote(start_ms=i * 1000, end_ms=(i + 1) * 1000, midi=p)
        for i, p in enumerate([60, 64, 67, 62, 65, 69])
    ]
    times = np.arange(0, 7, 0.02)
    f0 = []
    for t in times:
        index = int(t - 0.6) if t >= 0.6 else -1
        f0.append(
            float(440 * 2 ** ((notes[index].midi - 69) / 12)) if 0 <= index < len(notes) else None
        )
    result = global_alignment({"times": times.tolist(), "f0": f0}, notes)
    assert result["available"] and abs(result["offset_ms"] - 600) < 130
    assert abs(result["tempo_ratio"] - 1) < 0.06
    assert not global_alignment({"times": times.tolist(), "f0": [60.0] * len(times)}, notes)[
        "available"
    ]


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_cancellation_interrupts_running_handler_and_never_publishes():
    async def run():
        from app.jobs import JobCreate, cancel_job, enqueue, read_job
        from app.worker import process_one

        async with database_case() as (sessions, owner):
            async with sessions() as session:
                job = await enqueue(
                    session,
                    owner,
                    JobCreate(
                        kind="evaluation",
                        input={"check": "infrastructure"},
                        idempotency_key=str(uuid4()),
                    ),
                )
            started, cancelled = asyncio.Event(), asyncio.Event()

            async def handler(_):
                started.set()
                try:
                    await asyncio.sleep(30)
                finally:
                    cancelled.set()
                return {"unexpected": True}

            task = asyncio.create_task(process_one(sessions, handler, job["id"]))
            await asyncio.wait_for(started.wait(), 5)
            async with sessions() as session:
                await cancel_job(UUID(job["id"]), {"id": owner}, session)
            await asyncio.wait_for(task, 5)
            assert cancelled.is_set()
            async with sessions() as session:
                saved = await read_job(UUID(job["id"]), {"id": owner}, session)
            assert saved["status"] == "CANCELLED" and saved["result"] is None

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_expired_upload_and_erasure_ledger_remove_only_owner_data(monkeypatch):
    async def run():
        from app import database, privacy
        from app.privacy import ErasureRequest, erase, housekeep
        from app.upload_workflows import complete_upload

        class Storage:
            config = type("Config", (), {"bucket": "test"})()
            client = None

            def delete_object(self, key):
                pass

        monkeypatch.setattr(privacy.S3Storage, "from_settings", lambda _: Storage())
        monkeypatch.setattr(privacy, "erase_objects", lambda storage, owner: None)

        async def no_sweep(*args):
            pass

        monkeypatch.setattr(privacy, "sweep_snapshots", no_sweep)
        async with database_case() as (sessions, owner), database_case() as (_, other):
            monkeypatch.setattr(database, "_sessions", sessions)
            asset = str(uuid4())
            async with sessions() as session:
                await session.execute(
                    text("""INSERT INTO audio_assets
                  (id,owner_id,object_key,sha256,content_type,size_bytes,purpose,upload_expires_at)
                  VALUES (:id,:owner,:key,:hash,'audio/wav',10,'assessment',now()-interval '1 hour')"""),
                    {"id": asset, "owner": owner, "key": f"test/{asset}", "hash": "a" * 64},
                )
                await session.commit()
            async with sessions() as session:
                with pytest.raises(HTTPException) as exc:
                    await complete_upload(UUID(asset), {"id": owner}, session)
                assert exc.value.status_code == 410
            async with sessions() as session:
                result = await erase(
                    ErasureRequest(confirmation="删除我的全部项目数据"), {"id": owner}, session
                )
                await session.execute(
                    text(
                        "UPDATE privacy_requests SET created_at=now()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": result["id"]},
                )
                await session.commit()
            await housekeep(sessions)
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT status FROM privacy_requests WHERE id=:id"),
                        {"id": result["id"]},
                    )
                    == "completed"
                )
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM audio_assets WHERE owner_id=:owner"),
                        {"owner": owner},
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        text("SELECT status FROM users WHERE id=:owner"), {"owner": other}
                    )
                    == "active"
                )

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_conversation_clarification_idempotency_foreign_owner_and_revision(monkeypatch):
    async def run():
        from app import database

        async with database_case() as (sessions, owner), database_case() as (_, other):
            monkeypatch.setattr(database, "_sessions", sessions)
            async with sessions() as session:
                thread = await create_conversation(ConversationCreate(), {"id": owner}, session)
            identifier = UUID(thread["id"])
            request = TurnCreate(
                text="制定练习计划，每天60分钟", revision=0, idempotency_key=str(uuid4())
            )
            async with sessions() as session:
                first = await post_turn(identifier, request, {"id": owner}, session)
            assert first["status"] == "clarify"
            async with sessions() as session:
                assert await post_turn(identifier, request, {"id": owner}, session) == first
            async with sessions() as session:
                with pytest.raises(HTTPException) as exc:
                    await post_turn(
                        identifier,
                        TurnCreate(
                            text="每天10分钟，共3天", revision=1, idempotency_key=str(uuid4())
                        ),
                        {"id": other},
                        session,
                    )
                assert exc.value.status_code == 404
            async with sessions() as session:
                second = await post_turn(
                    identifier,
                    TurnCreate(text="每天10分钟，共3天", revision=1, idempotency_key=str(uuid4())),
                    {"id": owner},
                    session,
                )
            assert second["status"] == "queued", second
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM jobs WHERE owner_id=:owner"), {"owner": owner}
                    )
                    == 1
                )

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_verified_snapshot_survives_overwrite_and_followup_plan(monkeypatch):
    async def run():
        from app import database
        from app.jobs import read_job
        from app.schemas import UploadCreateRequest
        from app.storage.s3 import S3Storage
        from app.upload_workflows import complete_upload, get_asset
        from app.uploads import UploadService
        from app.worker import process_one

        settings = Settings()
        storage = S3Storage.from_settings(settings)
        try:
            storage.ensure_bucket()
        except Exception:
            if not os.getenv("TEST_OBJECT_STORE"):
                pytest.skip("requires explicit TEST_OBJECT_STORE and local S3")
            raise
        async with database_case() as (sessions, owner):
            monkeypatch.setattr(database, "_sessions", sessions)
            signal = 0.25 * np.sin(2 * np.pi * 220 * np.arange(80000) / 16000)
            buffer = io.BytesIO()
            sf.write(buffer, signal, 16000, format="WAV")
            data = buffer.getvalue()
            keys = []
            try:
                async with sessions() as session:
                    upload = await UploadService(session, storage, settings).create(
                        owner,
                        UploadCreateRequest(
                            filename="voice.wav",
                            content_type="audio/wav",
                            size_bytes=len(data),
                            sha256=hashlib.sha256(data).hexdigest(),
                            purpose="assessment",
                        ),
                    )
                keys.append(upload["object_key"])
                storage.client.put_object(
                    Bucket=storage.config.bucket, Key=upload["object_key"], Body=data
                )
                async with sessions() as session:
                    await complete_upload(UUID(upload["asset_id"]), {"id": owner}, session)
                async with sessions() as session:
                    job_id = await session.scalar(
                        text("SELECT verification_job_id FROM audio_assets WHERE id=:id"),
                        {"id": upload["asset_id"]},
                    )
                assert await process_one(sessions, job_id=str(job_id))
                async with sessions() as session:
                    asset = await get_asset(UUID(upload["asset_id"]), {"id": owner}, session)
                    assert asset["status"] == "ready", asset
                    key = await session.scalar(
                        text("SELECT verified_key FROM audio_assets WHERE id=:id"),
                        {"id": upload["asset_id"]},
                    )
                    keys.append(key)
                assert key != upload["object_key"]
                storage.client.put_object(
                    Bucket=storage.config.bucket, Key=upload["object_key"], Body=b"overwritten"
                )
                async with sessions() as session:
                    from app.assistant import AssistantRequest, ask

                    result = await ask(
                        AssistantRequest(
                            text="分析录音并制定练习计划，每天10分钟，共3天",
                            input_asset_id=upload["asset_id"],
                            idempotency_key=str(uuid4()),
                        ),
                        {"id": owner},
                        session,
                    )
                assert result["status"] == "queued", result
                assert await process_one(sessions, job_id=result["job"]["id"])
                async with sessions() as session:
                    job = await read_job(UUID(result["job"]["id"]), {"id": owner}, session)
                    assert job["status"] == "SUCCEEDED", job
                    assert job["result"]["report_id"] and job["result"]["plan_id"]
            finally:
                for key in keys:
                    if key:
                        storage.delete_object(key)

    asyncio.run(run())
