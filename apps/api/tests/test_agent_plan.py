from __future__ import annotations

import asyncio

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from app.agents.graph import ToolRegistry, build_workflow_graph, thread_config
from app.agents.plan import PlanValidationError, create_plan, validate_plan
from app.agents.router import RuleRouter
from app.runtime import format_workflow_result


def workflow_state(text: str):
    route = RuleRouter().route(text)
    steps = [
        {**step.as_dict(), "input": {"text": step.text}, "question": None}
        for step in route.steps
    ]
    return {
        "owner_id": "owner-1",
        "job_id": "job-1",
        "text": text,
        "expected_agent": route.agent,
        "execution_steps": steps,
    }


def test_plan_is_structured_bounded_and_validates_dependency_order():
    steps = workflow_state("分析我的录音，然后制定练习计划")['execution_steps']
    plan = create_plan(steps).as_dict()
    assert plan["version"] == 1
    assert [step["agent"] for step in plan["steps"]] == ["assessment", "planning"]
    assert plan["steps"][1]["depends_on"] == ["assessment"]
    assert plan["steps"][1]["actions"] == ["reports.read", "plans.create"]
    assert plan["steps"][1]["max_iterations"] == 2
    assert plan["steps"][1]["max_tool_calls"] == 2
    assert all(1 <= step["timeout_seconds"] <= 180 for step in plan["steps"])


def test_plan_rejects_tool_escalation_unbounded_budget_and_forward_dependency():
    steps = workflow_state("分析我的录音，然后制定练习计划")['execution_steps']
    for mutate in (
        lambda p: p["steps"][0].update(actions=["singing.capabilities"]),
        lambda p: p["steps"][1].update(max_iterations=999),
        lambda p: p["steps"][0].update(depends_on=["planning"]),
        lambda p: p["steps"][0].update(terminal_statuses=["whatever"]),
    ):
        plan = create_plan(steps).as_dict()
        mutate(plan)
        with pytest.raises(PlanValidationError):
            validate_plan(plan, steps)


def test_report_observation_can_request_input_without_creating_a_plan():
    async def run():
        calls = []

        async def assessment(_):
            return {"status": "measured", "summary": "ok"}

        async def read_report(_):
            calls.append("reports.read")
            return {"status": "needs_input", "message": "报告缺失"}

        async def plan(_):
            calls.append("plans.create")
            return {"status": "planned"}

        result = await build_workflow_graph(
            ToolRegistry(
                {
                    "assessment.submit": assessment,
                    "reports.read": read_report,
                    "plans.create": plan,
                }
            ),
            InMemorySaver(),
        ).ainvoke(workflow_state("分析我的录音，然后制定练习计划"), thread_config("owner-1", "job-1"))
        assert calls == ["reports.read"]
        assert [step["status"] for step in result["output"]["steps"]] == ["completed", "needs_input"]
        assert result["output"]["steps"][1]["decision"] == "request_user_input"

    asyncio.run(run())


def test_failed_observation_replans_dependents_and_keeps_independent_agent():
    async def run():
        calls = []

        async def assessment(_):
            calls.append("assessment")
            return {"status": "failed", "reason": "bad audio"}

        async def knowledge(_):
            calls.append("knowledge")
            return {"status": "answered", "answer": "可以独立回答"}

        async def unexpected(*_):
            raise AssertionError("dependent planning must not be dispatched")

        result = await build_workflow_graph(
            ToolRegistry(
                {
                    "assessment.submit": assessment,
                    "knowledge.retrieve": knowledge,
                    "reports.read": unexpected,
                    "plans.create": unexpected,
                }
            )
        ).ainvoke(workflow_state("分析我的录音，解释胸声，然后制定练习计划"))
        assert calls == ["assessment", "knowledge"]
        assert [step["status"] for step in result["output"]["steps"]] == [
            "failed",
            "completed",
            "blocked",
        ]
        assert result["execution_plan"]["steps"][2]["blocked_reason"]
        assert result["output"]["steps"][2]["decision"] == "replanned_dependency_block"

    asyncio.run(run())


@pytest.mark.parametrize(
    ("text", "agent", "tool", "status"),
    [
        ("解释胸声", "knowledge", "knowledge.retrieve", "answered"),
        ("分析我的录音", "assessment", "assessment.submit", "measured"),
        ("制定练习计划", "planning", "plans.create", "planned"),
        ("生成歌声", "singing", "singing.capabilities", "generated"),
    ],
)
def test_each_role_bound_agent_subgraph_executes_only_its_approved_action(text, agent, tool, status):
    async def run():
        calls = []

        async def handler(state):
            calls.append((state["agent"], state["job_id"]))
            return {"status": status}

        result = await build_workflow_graph(ToolRegistry({tool: handler})).ainvoke(
            workflow_state(text)
        )
        assert calls == [(agent, "job-1")]
        assert result["tool_trace"] == [tool]
        assert result["output"]["status"] == "completed"

    asyncio.run(run())


def test_agent_timeout_is_bounded_and_non_idempotent_singing_is_not_retried(monkeypatch):
    async def run():
        from app.agents.plan import AGENT_POLICIES
        from app.worker import RetryableJobError

        calls = []

        async def slow(_):
            calls.append("knowledge")
            await asyncio.sleep(1.05)
            return {"status": "answered"}

        monkeypatch.setitem(AGENT_POLICIES["knowledge"], "timeout_seconds", 1)
        timed = await build_workflow_graph(
            ToolRegistry({"knowledge.retrieve": slow}), final_attempt=True
        ).ainvoke(workflow_state("解释胸声"))
        assert calls == ["knowledge"]
        assert timed["output"]["steps"][0]["status"] == "failed"
        assert timed["output"]["status"] == "failed"
        assert "超时" in timed["output"]["steps"][0]["message"]
        assert timed["tool_trace"] == ["knowledge.retrieve"]

        singing_calls = []

        async def uncertain_write(_):
            singing_calls.append(1)
            raise RetryableJobError("remote status unknown")

        singing = await build_workflow_graph(
            ToolRegistry({"singing.capabilities": uncertain_write})
        ).ainvoke(workflow_state("生成歌声"))
        assert singing_calls == [1]
        assert singing["output"]["steps"][0]["status"] == "failed"
        assert "停止自动重试" in singing["output"]["steps"][0]["message"]

    asyncio.run(run())


def test_single_agent_result_keeps_existing_artifact_fields_and_adds_workflow_trace():
    audio_report = {"status": "measured", "total": {"value": 98}, "segments": [{"start": 0}]}
    formatted = format_workflow_result(
        "assessment",
        "explicit action",
        ["assessment.submit"],
        {
            "status": "completed",
            "steps": [
                {"id": "assessment", "status": "completed", "result": audio_report}
            ],
        },
    )
    assert formatted["status"] == "measured"
    assert formatted["total"] == {"value": 98}
    assert formatted["segments"] == [{"start": 0}]
    assert formatted["workflow"]["steps"][0]["status"] == "completed"
