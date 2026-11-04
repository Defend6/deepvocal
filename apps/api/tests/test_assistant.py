import asyncio
import os
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from test_jobs import database_case

from app.database import get_session
from app.identity import current_user
from app.jobs import read_job
from app.main import app
from app.worker import process_one

pytestmark = pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")


def test_assistant_routes_without_explicit_action_and_persists_plan(monkeypatch):
    async def run():
        async with database_case() as (sessions, owner):
            from app import database

            monkeypatch.setattr(database, "_sessions", sessions)

            async def identity():
                return {"id": owner}

            async def session_dependency():
                async with sessions() as session:
                    yield session

            app.dependency_overrides[current_user] = identity
            app.dependency_overrides[get_session] = session_dependency
            try:
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=app), base_url="http://test"
                ) as client:
                    body = {
                        "text": "如何制定音准练习计划，每天15分钟，共5天",
                        "idempotency_key": str(uuid4()),
                    }
                    response = await client.post("/v1/assistant", json=body)
                    assert response.status_code == 200, response.text
                    created = response.json()["job"]
                    again = await client.post("/v1/assistant", json=body)
                    assert again.json()["job"]["id"] == created["id"]
                    forged = await client.post(
                        "/v1/assistant",
                        json={
                            **body,
                            "input": {"routing": {"mode": "explicit"}},
                        },
                    )
                    assert forged.status_code == 422
                    async with sessions() as session:
                        request = await session.scalar(
                            text("SELECT request FROM jobs WHERE id=:id"), {"id": created["id"]}
                        )
                        assert request["routing"]["mode"] == "automatic"
                        assert "explicit_action" not in request
                    assert await process_one(sessions, job_id=created["id"])
                    async with sessions() as session:
                        saved = await read_job(UUID(created["id"]), {"id": owner}, session)
                        assert saved["status"] == "SUCCEEDED", saved
                        assert saved["result"]["route_reason"] == "practice planning intent"
                        assert saved["result"]["tool_trace"] == ["plans.create"]
                        payload = await session.scalar(
                            text("SELECT payload FROM practice_plans WHERE source_job_id=:id"),
                            {"id": created["id"]},
                        )
                        assert (payload["goal"], payload["minutes"], payload["days"]) == (
                            "pitch",
                            15,
                            5,
                        )
            finally:
                app.dependency_overrides.clear()

    asyncio.run(run())


def test_clarifications_and_pending_or_foreign_audio_never_enqueue():
    async def run():
        async with database_case() as (sessions, owner), database_case() as (_, other):
            from app.assistant import AssistantRequest, ask

            async def request(question, asset=None):
                async with sessions() as session:
                    return await ask(
                        AssistantRequest(
                            text=question,
                            input_asset_id=asset,
                            idempotency_key=str(uuid4()),
                        ),
                        {"id": owner},
                        session,
                    )

            for question in (
                "你好",
                "请分析我的录音",
                "制定每天60分钟的练习计划",
                "请分析我的录音并制定计划",
            ):
                assert (await request(question))["status"] == "clarify"
            for asset_owner in (owner, other):
                asset = uuid4()
                async with sessions() as session:
                    await session.execute(
                        text("""
                        INSERT INTO audio_assets
                        (id,owner_id,object_key,sha256,content_type,size_bytes,purpose)
                        VALUES (:id,:owner,:key,:sha,'audio/wav',10,'assessment')
                    """),
                        {
                            "id": str(asset),
                            "owner": asset_owner,
                            "key": f"test/{asset}",
                            "sha": "a" * 64,
                        },
                    )
                    await session.commit()
                if asset_owner == owner:
                    assert (await request("分析我的录音", asset))["status"] == "blocked"
                else:
                    from fastapi import HTTPException

                    with pytest.raises(HTTPException) as exc:
                        await request("分析我的录音", asset)
                    assert exc.value.status_code == 404
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM jobs WHERE owner_id=:owner"), {"owner": owner}
                    )
                    == 0
                )
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM outbox_events WHERE owner_id=:owner"),
                        {"owner": owner},
                    )
                    == 0
                )

    asyncio.run(run())
