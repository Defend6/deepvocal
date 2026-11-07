import asyncio
import os
from uuid import UUID, uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import ToolBoundaryError, ToolRegistry, build_workflow_graph, thread_config
from app.agents.intent_input import planning_input
from app.agents.router import RuleRouter, wants_evidence
from app.planning import PlanEdit, PlanningInput, create_plan


@pytest.mark.parametrize(
    "question,agents",
    [
        ("什么是高音？然后帮我制定音准练习计划，每天10分钟，共3天", ["knowledge", "planning"]),
        (
            "请分析我的录音，然后解释什么是胸声，并帮我制定练习计划",
            ["assessment", "knowledge", "planning"],
        ),
        ("生成歌声然后制定计划", ["singing", "planning"]),
        ("先制定练习计划，再分析我的录音", ["assessment", "planning"]),
        ("高音上不去，我需要怎么练习？", ["knowledge"]),
        ("解释胸声和制定音准计划", ["knowledge", "planning"]),
        ("不要分析录音，请解释胸声，并制定节奏计划", ["knowledge", "planning"]),
    ],
)
def test_composite_intent_decomposition(question, agents):
    route = RuleRouter().route(question)
    assert [s.agent for s in route.steps] == agents
    if "assessment" in agents and "planning" in agents:
        assert next(s for s in route.steps if s.agent == "planning").depends_on == ("assessment",)


def test_durations_and_negation_do_not_pollute_planning_and_decimal_is_not_truncated():
    route = RuleRouter().route("解释30秒录音中的问题，再制定音准和节奏练习计划，每天10分钟，共3天")
    plan_step = next(s for s in route.steps if s.agent == "planning")
    config, question = planning_input(plan_step.text)
    assert not question and config["minutes"] == 10 and config["days"] == 3
    assert config["goals"] == ["pitch", "rhythm"]
    route = RuleRouter().route("解释胸声，再制定每天10.5分钟的练习计划")
    assert planning_input(route.steps[1].text)[1]
    route = RuleRouter().route("不要制定音准计划，帮我制定节奏计划")
    assert "音准" not in route.steps[0].text


@pytest.mark.parametrize(
    "text,expected",
    [
        ("什么是胸声", False),
        ("什么是胸声，请提供来源", True),
        ("请给出回答依据", True),
        ("不要显示回答依据", False),
        ("依据报告制定计划", False),
        ("请列出参考文献", True),
    ],
)
def test_sources_are_explicit_opt_in(text, expected):
    assert wants_evidence(text) is expected


@pytest.mark.parametrize("minutes", [5, 10, 13, 30])
def test_combined_goals_share_one_valid_budget(minutes):
    plan = create_plan(PlanningInput(goals=["pitch", "rhythm", "stability"], minutes=minutes))[
        "plan"
    ]
    assert plan["goals"] == ["pitch", "rhythm", "stability"]
    assert sum(e["seconds"] for e in plan["exercises"]) == minutes * 60
    PlanEdit(version=1, goal=plan["goal"], days=plan["days"], exercises=plan["exercises"])
    assert any("模唱" in e["title"] for e in plan["exercises"])
    assert any("节拍" in e["title"] for e in plan["exercises"])


def state_for(question):
    route = RuleRouter().route(question)
    steps = [{**s.as_dict(), "input": {"text": s.text}} for s in route.steps]
    return {
        "owner_id": "owner",
        "job_id": "job",
        "text": question,
        "expected_agent": route.agent,
        "execution_steps": steps,
    }


def test_sequential_workflow_hands_measurement_to_plan_and_preserves_other_results():
    async def run():
        calls, progress = [], []
        state = state_for("解释胸声，然后分析我的录音，并制定计划")

        async def knowledge(s):
            calls.append("knowledge")
            assert "录音" not in s["text"] and s["skill_context"] == {"agent": "knowledge"}
            return {"status": "answered", "answer": "answer [1]"}

        async def assessment(s):
            calls.append("assessment")
            return {"status": "measured", "quality": "usable"}

        async def planning(s):
            calls.append("planning")
            assert s["measurement_report"]["quality"] == "usable"
            assert s["skill_context"] == {"agent": "planning"}
            return {"status": "planned", "plan": {}}

        async def publish(output):
            progress.append(output)

        async def skills(agent):
            return {"agent": agent}

        graph = build_workflow_graph(
            ToolRegistry(
                {
                    "knowledge.retrieve": knowledge,
                    "assessment.submit": assessment,
                    "plans.create": planning,
                }
            ),
            InMemorySaver(),
            progress=publish,
            skills=skills,
        )
        result = await graph.ainvoke(state, thread_config("owner", "job"))
        assert calls == ["knowledge", "assessment", "planning"]
        assert result["output"]["status"] == "completed"
        assert len(result["output"]["steps"]) == 3
        assert progress[1]["steps"][0]["result"]["answer"] == "answer [1]"

    asyncio.run(run())


def test_missing_audio_blocks_dependent_plan_but_keeps_independent_answer():
    async def run():
        state = state_for("分析录音，制定计划，并解释胸声")
        state["execution_steps"][0]["question"] = "请上传录音"

        async def knowledge(_):
            return {"status": "answered", "answer": "胸声解答"}

        result = await build_workflow_graph(
            ToolRegistry({"knowledge.retrieve": knowledge})
        ).ainvoke(state)
        steps = result["output"]["steps"]
        assert [s["status"] for s in steps] == ["needs_input", "blocked", "completed"]
        assert result["tool_trace"] == ["knowledge.retrieve"]
        assert result["output"]["status"] == "partial"

    asyncio.run(run())


def test_unavailable_answer_does_not_block_independent_plan():
    async def run():
        async def knowledge(_):
            return {"status": "refused", "reason": "INSUFFICIENT_EVIDENCE"}

        async def planning(_):
            return {"status": "planned", "plan": {}}

        result = await build_workflow_graph(
            ToolRegistry({"knowledge.retrieve": knowledge, "plans.create": planning})
        ).ainvoke(state_for("解释胸声，并制定计划"))
        assert [s["status"] for s in result["output"]["steps"]] == ["failed", "completed"]

    asyncio.run(run())


def test_retry_resumes_failed_step_and_progress_failure_does_not_repeat_successful_tool():
    async def run():
        from app.worker import RetryableJobError

        calls, fail = [], True

        async def knowledge(_):
            calls.append("knowledge")
            return {"status": "answered", "answer": "回答"}

        async def planning(_):
            nonlocal fail
            calls.append("planning")
            if fail:
                fail = False
                raise RetryableJobError()
            return {"status": "planned", "plan": {}}

        saver = InMemorySaver()
        registry = ToolRegistry({"knowledge.retrieve": knowledge, "plans.create": planning})
        config = thread_config("owner", "job")
        graph = build_workflow_graph(registry, saver)
        with pytest.raises(RetryableJobError):
            await graph.ainvoke(state_for("解释胸声，并制定计划"), config)
        graph = build_workflow_graph(registry, saver)
        result = await graph.ainvoke(None, config)
        assert calls == ["knowledge", "planning", "planning"]
        assert result["output"]["status"] == "completed"

        # A completed tool also survives a later progress publication failure.
        async def progress(output):
            if output["steps"][0]["status"] == "completed":
                raise RuntimeError("simulated DB outage")

        config = thread_config("owner", "job2")
        graph = build_workflow_graph(registry, saver, progress=progress)
        with pytest.raises(RuntimeError):
            await graph.ainvoke({**state_for("解释胸声，并制定计划"), "job_id": "job2"}, config)
        before = calls.count("knowledge")
        await build_workflow_graph(registry, saver).ainvoke(None, config)
        assert calls.count("knowledge") == before

    asyncio.run(run())


def test_workflow_tampering_and_cancellation_stop_subsequent_tools():
    async def run():
        calls = []

        async def handler(_):
            calls.append(1)
            return {"status": "answered"}

        registry = ToolRegistry({"knowledge.retrieve": handler, "plans.create": handler})
        state = state_for("解释胸声，并制定计划")
        state["execution_steps"][0]["agent"] = "planning"
        with pytest.raises(ToolBoundaryError):
            await build_workflow_graph(registry).ainvoke(state)
        assert not calls

        async def progress(output):
            if output["steps"][0]["status"] == "completed":
                raise asyncio.CancelledError()

        with pytest.raises(asyncio.CancelledError):
            await build_workflow_graph(registry, progress=progress).ainvoke(
                state_for("解释胸声，并制定计划")
            )
        assert calls == [1]

    asyncio.run(run())


def test_exhausted_transient_failure_still_allows_independent_plan():
    async def run():
        from app.worker import RetryableJobError

        async def knowledge(_):
            raise RetryableJobError()

        async def planning(_):
            return {"status": "planned", "plan": {}}

        result = await build_workflow_graph(
            ToolRegistry({"knowledge.retrieve": knowledge, "plans.create": planning}),
            final_attempt=True,
        ).ainvoke(state_for("解释胸声，并制定计划"))
        assert result["output"]["status"] == "partial"
        assert [s["status"] for s in result["output"]["steps"]] == ["failed", "completed"]

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
@pytest.mark.parametrize(
    "question,expected,pending",
    [
        ("解释胸声，并分析我的录音，然后制定计划", ["completed", "needs_input", "blocked"], True),
        ("生成歌声，然后制定音准和节奏计划", ["needs_input", "completed"], True),
        ("解释胸声，并制定高音和音准计划", ["completed", "needs_input"], True),
    ],
)
def test_missing_prerequisites_do_not_discard_independent_work(
    monkeypatch, question, expected, pending
):
    async def run():
        from test_jobs import database_case

        from app import business, database
        from app.assistant import AssistantRequest, dispatch
        from app.jobs import read_job
        from app.worker import process_one

        async def knowledge(_state, claim_context=None):
            return {"status": "answered", "answer": "胸声解答"}

        monkeypatch.setattr(business, "knowledge_tool", knowledge)
        async with database_case() as (sessions, owner):
            monkeypatch.setattr(database, "_sessions", sessions)
            async with sessions() as session:
                result = await dispatch(
                    AssistantRequest(text=question, idempotency_key=str(uuid4())),
                    {"id": owner},
                    session,
                )
            assert result["status"] == "queued", result
            assert bool(result["pending_text"]) == pending
            assert "解释胸声" not in result["pending_text"]
            assert await process_one(sessions, job_id=result["job"]["id"])
            async with sessions() as session:
                job = await read_job(UUID(result["job"]["id"]), {"id": owner}, session)
                assert job["status"] == "SUCCEEDED", job
                assert [s["status"] for s in job["result"]["steps"]] == expected
                assert "singing.capabilities" not in job["result"]["tool_trace"]

    asyncio.run(run())


@pytest.mark.skipif(not os.getenv("TEST_DATABASE_URL"), reason="requires PostgreSQL")
def test_real_job_persists_combined_artifacts_and_sources_followup_without_duplicate_work(
    monkeypatch,
):
    async def run():
        from sqlalchemy import text
        from test_jobs import database_case

        from app import business, database
        from app.conversations import (
            ConversationCreate,
            TurnCreate,
            create_conversation,
            get_conversation,
            post_turn,
        )
        from app.jobs import read_job
        from app.worker import process_one

        async def knowledge(state, claim_context=None):
            assert state["text"] == "解释胸声"
            return {"status": "answered", "answer": "胸声说明。[1]", "citations": [{"number": 1}]}

        monkeypatch.setattr(business, "knowledge_tool", knowledge)
        async with database_case() as (sessions, owner):
            monkeypatch.setattr(database, "_sessions", sessions)
            user = {"id": owner}
            async with sessions() as session:
                thread = await create_conversation(ConversationCreate(), user, session)
            identifier = UUID(thread["id"])
            async with sessions() as session:
                response = await post_turn(
                    identifier,
                    TurnCreate(
                        text="解释胸声，并制定音准和节奏练习计划，每天10分钟，共3天",
                        revision=0,
                        idempotency_key=str(uuid4()),
                    ),
                    user,
                    session,
                )
            job_id = response["job"]["id"]
            assert not response["show_evidence"]
            assert await process_one(sessions, job_id=job_id)
            async with sessions() as session:
                job = await read_job(UUID(job_id), user, session)
                assert job["status"] == "SUCCEEDED", job
                assert job["result"]["status"] == "completed"
                assert job["result"]["plan_id"]
                payload = await session.scalar(
                    text("SELECT payload FROM practice_plans WHERE source_job_id=:id"),
                    {"id": job_id},
                )
                assert payload["goals"] == ["pitch", "rhythm"]
                followup = await post_turn(
                    identifier,
                    TurnCreate(
                        text="请给出回答依据和来源", revision=1, idempotency_key=str(uuid4())
                    ),
                    user,
                    session,
                )
                assert followup["show_evidence"] and followup["job"]["id"] == job_id
            async with sessions() as session:
                assert (
                    await session.scalar(
                        text("SELECT count(*) FROM jobs WHERE owner_id=:owner"), {"owner": owner}
                    )
                    == 1
                )
                saved = await get_conversation(identifier, user, session)
                assert not saved["turns"][0]["response"]["show_evidence"]
                assert saved["turns"][1]["response"]["show_evidence"]

    asyncio.run(run())
