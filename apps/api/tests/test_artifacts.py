import asyncio
import os
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from test_jobs import database_case

from app.artifacts import edit_plan, plan, record_practice, reports
from app.jobs import JobCreate, cancel_job, enqueue, read_job
from app.planning import PlanEdit, PlanningInput, PracticeCreate, create_plan
from app.worker import claim, finish, process_one

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")


def test_planning_real_langgraph_worker_persistence_owner_version_and_feedback(monkeypatch):
    async def run():
        async with database_case() as (sessions, owner):
            from app import database

            monkeypatch.setattr(database, "_sessions", sessions)
            async with sessions() as session:
                created = await enqueue(
                    session,
                    owner,
                    JobCreate(
                        kind="planning",
                        input={"goal": "pitch", "minutes": 10},
                        idempotency_key=str(uuid4()),
                    ),
                )
            assert await process_one(sessions, job_id=created["id"])
            async with sessions() as session:
                job = await read_job(UUID(created["id"]), {"id": owner}, session)
                assert job["status"] == "SUCCEEDED", job
                result = job["result"]
                assert result["tool_trace"] == ["plans.create"]
                plan_id = UUID(result["plan_id"])
                saved = await plan(plan_id, {"id": owner}, session)
                assert saved["version"] == 1
                with pytest.raises(HTTPException) as exc:
                    await plan(plan_id, {"id": str(uuid4())}, session)
                assert exc.value.status_code == 404
                edit = PlanEdit(
                    version=1, goal="rhythm", days=2, exercises=saved["payload"]["exercises"]
                )
                updated = await edit_plan(plan_id, edit, {"id": owner}, session)
                assert updated["version"] == 2 and updated["payload"]["goal"] == "rhythm"
                with pytest.raises(HTTPException) as exc:
                    await edit_plan(plan_id, edit, {"id": owner}, session)
                assert exc.value.status_code == 409
                await session.rollback()
                request = PracticeCreate(
                    idempotency_key=uuid4(),
                    plan_version=2,
                    duration_seconds=120,
                    difficulty=2,
                    note="实际用户反馈",
                )
                first = await record_practice(plan_id, request, {"id": owner}, session)
                again = await record_practice(plan_id, request, {"id": owner}, session)
                assert first["id"] == again["id"]
                assert first["payload"]["note"] == "实际用户反馈"
            assert not await process_one(sessions, job_id=created["id"])

    asyncio.run(run())


def test_cancelled_worker_cannot_materialize_plan_or_report():
    async def run():
        async with database_case() as (sessions, owner):
            async with sessions() as session:
                created = await enqueue(
                    session, owner, JobCreate(kind="planning", idempotency_key=str(uuid4()))
                )
            claimed = await claim(sessions, created["id"])
            async with sessions() as session:
                await cancel_job(UUID(created["id"]), {"id": owner}, session)
            assert not await finish(sessions, claimed, create_plan(PlanningInput()), None)
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM practice_plans WHERE owner_id=:owner"),
                        {"owner": owner},
                    )
                    == 0
                )
                assert await reports({"id": owner}, session) == []

    asyncio.run(run())


def test_assessment_storage_hash_and_owner_report_end_to_end(monkeypatch):
    import hashlib
    import io

    from test_audio import wav

    class Storage:
        config = type("Config", (), {"bucket": "test"})()
        client = None

        def get_object(self, **kwargs):
            return {"Body": io.BytesIO(audio)}

    audio = wav()
    storage = Storage()
    storage.client = storage
    monkeypatch.setattr("app.storage.s3.S3Storage.from_settings", lambda _: storage)

    async def run():
        async with database_case() as (sessions, owner):
            from app import database

            monkeypatch.setattr(database, "_sessions", sessions)
            asset = str(uuid4())
            async with sessions() as session:
                # Test fixture only: no runtime upload endpoint marks pending assets ready.
                await session.execute(
                    text("""INSERT INTO audio_assets(id,owner_id,object_key,sha256,content_type,size_bytes,purpose,status)
                    VALUES (:id,:owner,:key,:sha,'audio/wav',:size,'assessment','ready')"""),
                    {
                        "id": asset,
                        "owner": owner,
                        "key": f"test/{asset}",
                        "sha": hashlib.sha256(audio).hexdigest(),
                        "size": len(audio),
                    },
                )
                await session.commit()
                created = await enqueue(
                    session,
                    owner,
                    JobCreate(
                        kind="assessment",
                        input_asset_id=asset,
                        input={"profile": "long_tone", "goal_duration_ms": 4000},
                        idempotency_key=str(uuid4()),
                    ),
                )
            await process_one(sessions, job_id=created["id"])
            async with sessions() as session:
                job = await read_job(UUID(created["id"]), {"id": owner}, session)
                assert job["status"] == "SUCCEEDED", job
                assert job["result"]["total"]["value"] > 95
                assert len(await reports({"id": owner}, session)) == 1
                await session.execute(
                    text("UPDATE audio_assets SET sha256=:sha WHERE id=:id"),
                    {"sha": "f" * 64, "id": asset},
                )
                await session.commit()
                bad = await enqueue(
                    session,
                    owner,
                    JobCreate(
                        kind="assessment", input_asset_id=asset, idempotency_key=str(uuid4())
                    ),
                )
            await process_one(sessions, job_id=bad["id"])
            async with sessions() as session:
                assert (await read_job(UUID(bad["id"]), {"id": owner}, session))[
                    "error_code"
                ] == "ASSET_CONTENT_MISMATCH"

    asyncio.run(run())
