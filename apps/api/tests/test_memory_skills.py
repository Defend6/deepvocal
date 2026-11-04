import asyncio
import os
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import text
from test_jobs import database_case, submit

from app.jobs import cancel_job, read_job
from app.memory import (
    Consent,
    Preferences,
    clear_memory,
    consent,
    delete_fact,
    effective_preferences,
    preferences,
    read_memory,
)
from app.skills import Candidate, LazyRegistry, Transition, propose, selected_skill, transition
from app.worker import claim, finish, process_one


def test_lazy_discovery_hash_and_agent_boundary():
    registry = LazyRegistry()
    assert all("steps" not in item for item in registry.discover())
    loaded = registry.load("knowledge")
    assert "证据" in loaded["body"]
    with pytest.raises(ValueError):
        registry.load("knowledge", expected_hash="0" * 64)
    with pytest.raises(ValueError):
        registry.load(
            "knowledge", {"agent": "assessment", "steps": ["quality"], "status": "published"}
        )
    for status in ("candidate", "retired"):
        with pytest.raises(ValueError):
            registry.load(
                "planning", {"agent": "planning", "steps": ["constraints"], "status": status}
            )


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_memory_consent_revision_sources_delete_and_no_resurrection():
    async def run():
        async with database_case() as (sessions, owner):
            user = {"id": owner}
            async with sessions() as session:
                initial = await read_memory(user, session)
                assert initial["enabled"] is False and initial["l2_facts"] == []
                with pytest.raises(HTTPException):
                    await preferences(
                        Preferences(revision=1, goal="pitch", minutes=10), user, session
                    )
                await session.rollback()
                enabled = await consent(
                    Consent(enabled=True, revision=initial["revision"]), user, session
                )
                saved = await preferences(
                    Preferences(revision=enabled["revision"], goal="pitch", minutes=15),
                    user,
                    session,
                )
                assert saved["l3_profile"]["preferences"] == {"goal": "pitch", "minutes": 15}
                assert saved["l3_profile"]["source_event_ids"] == [saved["l1_events"][0]["id"]]
                with pytest.raises(HTTPException) as exc:
                    await preferences(
                        Preferences(revision=enabled["revision"], goal="rhythm", minutes=10),
                        user,
                        session,
                    )
                assert exc.value.status_code == 409
                await session.rollback()
                deleted = await delete_fact("minutes", user, session)
                assert "minutes" not in deleted["l3_profile"]["preferences"]
                stopped = await consent(
                    Consent(enabled=False, revision=deleted["revision"]), user, session
                )
                assert stopped["enabled"] is False
                assert await effective_preferences(session, owner) == {}
                await clear_memory(user, session)
                cleared = await read_memory(user, session)
                assert (
                    cleared["l1_events"] == []
                    and cleared["l2_facts"] == []
                    and cleared["l3_profile"] == {}
                )

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_skill_candidate_review_publish_retire_and_owner_isolation(monkeypatch):
    async def run():
        async with database_case() as (sessions, owner):
            from app import database

            monkeypatch.setattr(database, "_sessions", sessions)
            async with sessions() as session:
                candidate = await propose(
                    Candidate(
                        agent="planning", skill_id="my-plan", steps=["constraints", "feedback"]
                    ),
                    {"id": owner},
                    session,
                )
                version_id = UUID(candidate["id"])
                assert candidate["critic"]["passed"]
                with pytest.raises(HTTPException):
                    await transition(
                        version_id, Transition(action="publish"), {"id": owner}, session
                    )
                await session.rollback()
                with pytest.raises(HTTPException) as exc:
                    await transition(
                        version_id, Transition(action="review"), {"id": str(uuid4())}, session
                    )
                assert exc.value.status_code == 404
                await session.rollback()
                await transition(version_id, Transition(action="review"), {"id": owner}, session)
                await transition(version_id, Transition(action="publish"), {"id": owner}, session)
                assert (await selected_skill(owner, "planning"))["skill_id"] == "my-plan"
                await transition(version_id, Transition(action="retire"), {"id": owner}, session)
                assert (await selected_skill(owner, "planning"))["skill_id"] == "planning-baseline"
                assert (await selected_skill(str(uuid4()), "planning"))[
                    "skill_id"
                ] == "planning-baseline"

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_background_memory_dedup_revision_consent_and_fenced_cancellation():
    async def run():
        async with database_case() as (sessions, owner):
            user = {"id": owner}
            async with sessions() as session:
                enabled = await consent(Consent(enabled=True, revision=1), user, session)
                await preferences(
                    Preferences(revision=enabled["revision"], goal="pitch", minutes=10),
                    user,
                    session,
                )

            async def completed_task():
                created = await submit(sessions, owner)
                job = await claim(sessions, created["id"])
                assert await finish(sessions, job, {"status": "ok"}, None)
                assert not await finish(sessions, job, {"status": "ok"}, None)
                async with sessions() as session:
                    return dict(
                        (
                            await session.execute(
                                text(
                                    "SELECT * FROM jobs WHERE owner_id=:owner AND kind='memory' AND status='QUEUED' ORDER BY created_at DESC LIMIT 1"
                                ),
                                {"owner": owner},
                            )
                        )
                        .mappings()
                        .one()
                    )

            first = await completed_task()
            assert await process_one(sessions, job_id=str(first["id"]))
            async with sessions() as session:
                assert (await read_job(first["id"], user, session))["result"][
                    "status"
                ] == "compacted"
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM jobs WHERE owner_id=:owner AND kind='memory'"),
                        {"owner": owner},
                    )
                    == 1
                )
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM memory_events WHERE owner_id=:owner AND event_kind='task.completed'"
                        ),
                        {"owner": owner},
                    )
                    == 1
                )

            second = await completed_task()
            async with sessions() as session:
                before = await read_memory(user, session)
                await delete_fact("minutes", user, session)
            assert await process_one(sessions, job_id=str(second["id"]))
            async with sessions() as session:
                assert (await read_job(second["id"], user, session))["result"][
                    "status"
                ] == "superseded"
                assert (
                    "minutes" not in (await read_memory(user, session))["l3_profile"]["preferences"]
                )
                assert (await read_memory(user, session))["revision"] == before["revision"] + 1

            third = await completed_task()
            claimed = await claim(sessions, str(third["id"]))
            async with sessions() as session:
                await cancel_job(third["id"], user, session)
                await clear_memory(user, session)
            assert not await finish(sessions, claimed, {"status": "prepared"}, None)
            async with sessions() as session:
                cleared = await read_memory(user, session)
                assert (
                    not cleared["enabled"]
                    and not cleared["l1_events"]
                    and not cleared["l3_profile"]
                )

    asyncio.run(run())
