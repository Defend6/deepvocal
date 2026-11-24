import asyncio
import os
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import ToolBoundaryError, ToolRegistry, build_workflow_graph, thread_config
from app.agents.router import RuleRouter


def test_ambiguous_audio_theory_and_explicit_routes():
    router = RuleRouter()
    assert router.route("什么是音准").agent == "knowledge"
    assert router.route("根据录音生成歌声").agent == "singing"
    assert router.route("请做评估", "unknown.action").agent == "clarify"
    assert router.route("随便输入", "planning.create").agent == "planning"


def test_four_real_subgraphs_enforce_tool_boundaries_and_owner_context():
    async def handler(state):
        status = {
            "knowledge": "answered",
            "assessment": "measured",
            "planning": "planned",
            "singing": "generated",
        }[state["agent"]]
        return {"status": status, "owner": state["owner_id"]}

    async def run():
        registry = ToolRegistry(
            {
                name: handler
                for name in (
                    "knowledge.retrieve",
                    "assessment.submit",
                    "plans.create",
                    "singing.capabilities",
                )
            }
        )
        graph = build_workflow_graph(registry, InMemorySaver())
        for agent, action, tool in (
            ("knowledge", "knowledge.ask", "knowledge.retrieve"),
            ("assessment", "assessment.submit", "assessment.submit"),
            ("planning", "planning.create", "plans.create"),
            ("singing", "singing.generate", "singing.capabilities"),
        ):
            route = RuleRouter().route("ignored", action)
            state = {
                "owner_id": "a",
                "job_id": agent,
                "text": "ignored",
                "explicit_action": action,
                "expected_agent": agent,
                "execution_steps": [
                    {**route.steps[0].as_dict(), "input": {"text": "ignored"}, "question": None}
                ],
            }
            result = await graph.ainvoke(state, thread_config("a", agent))
            assert result["agent"] == agent and result["tool_trace"] == [tool]
            assert result["output"]["steps"][0]["result"]["owner"] == "a"
        with pytest.raises(ToolBoundaryError):
            await registry.call("knowledge", "assessment.submit", {"owner_id": "a", "job_id": "j"})
        with pytest.raises(ToolBoundaryError):
            await registry.call("knowledge", "knowledge.retrieve", {})
        other = await graph.aget_state(thread_config("b", "knowledge"))
        assert not other.values

    asyncio.run(run())


def test_graph_resume_does_not_rerun_successful_tool():
    async def run():
        calls = []

        async def handler(state):
            calls.append(state["job_id"])
            return {"status": "answered"}

        route = RuleRouter().route("什么是共鸣")
        graph = build_workflow_graph(ToolRegistry({"knowledge.retrieve": handler}), InMemorySaver())
        config = thread_config("owner", "job")
        await graph.ainvoke({
            "owner_id": "owner", "job_id": "job", "text": "什么是共鸣",
            "expected_agent": "knowledge",
            "execution_steps": [{**route.steps[0].as_dict(), "input": {"text": "什么是共鸣"}, "question": None}],
        }, config)
        state = await graph.aget_state(config)
        assert not state.next
        assert state.values["output"]["steps"][0]["result"]["status"] == "answered"
        await graph.ainvoke(None, config)
        assert calls == ["job"]

    asyncio.run(run())


def test_automatic_route_mismatch_stops_before_any_tool():
    async def run():
        calls = []

        async def handler(state):
            calls.append(state)
            return {"status": "unexpected"}

        graph = build_workflow_graph(ToolRegistry({"plans.create": handler}))
        with pytest.raises(ToolBoundaryError, match="admission and execution"):
            await graph.ainvoke(
                {
                    "owner_id": "owner",
                    "job_id": "job",
                    "text": "制定音准练习计划",
                    "expected_agent": "knowledge",
                    "execution_steps": [{
                        "id": "planning", "agent": "planning", "title": "练习安排",
                        "text": "制定音准练习计划", "depends_on": [],
                        "input": {"text": "制定音准练习计划"}, "question": None,
                    }],
                }
            )
        assert calls == []

    asyncio.run(run())


def test_postgres_checkpoint_survives_new_graph_instance():
    if not os.getenv("TEST_DATABASE_URL"):
        pytest.skip("requires PostgreSQL")

    async def run():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg import AsyncConnection
        from psycopg.rows import dict_row

        dsn = os.environ["TEST_DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")
        config = thread_config("test-owner", str(uuid4()))

        async def handler(_state):
            return {"status": "answered"}

        async with await AsyncConnection.connect(
            dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
        ) as conn:
            saver = AsyncPostgresSaver(conn)
            await saver.setup()
            route = RuleRouter().route("什么是气息")
            graph = build_workflow_graph(ToolRegistry({"knowledge.retrieve": handler}), saver)
            await graph.ainvoke(
                {
                    "owner_id": "test-owner", "job_id": config["configurable"]["thread_id"],
                    "text": "什么是气息", "expected_agent": "knowledge",
                    "execution_steps": [{
                        **route.steps[0].as_dict(), "input": {"text": "什么是气息"}, "question": None
                    }],
                }, config
            )
        async with await AsyncConnection.connect(
            dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
        ) as conn:
            saver = AsyncPostgresSaver(conn)
            graph = build_workflow_graph(ToolRegistry({}), saver)
            state = await graph.aget_state(config)
            assert state.values["output"]["steps"][0]["result"]["status"] == "answered"
            await saver.adelete_thread(config["configurable"]["thread_id"])

    asyncio.run(run())
