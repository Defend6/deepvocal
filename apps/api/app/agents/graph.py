from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.router import RuleRouter

TOOLS = {
    "knowledge": frozenset({"knowledge.retrieve"}),
    "assessment": frozenset({"audio.inspect", "assessment.submit"}),
    "planning": frozenset({"reports.read", "plans.create"}),
    "singing": frozenset({"singing.capabilities"}),
    "clarify": frozenset(),
}


class AgentState(TypedDict, total=False):
    owner_id: str
    job_id: str
    text: str
    explicit_action: str | None
    expected_agent: str
    input: dict
    skill_context: dict
    agent: str
    route_reason: str
    tool_trace: list[str]
    output: dict
    followup_plan: dict | None
    measurement_report: dict
    intent_steps: list[dict]
    execution_steps: list[dict]
    step_index: int
    step_results: list[dict]
    pending_step: dict


class ToolBoundaryError(RuntimeError):
    pass


class ToolRegistry:
    def __init__(self, handlers: dict[str, Callable[[AgentState], Awaitable[dict]]]):
        self.handlers = handlers

    async def call(self, agent: str, name: str, state: AgentState) -> dict:
        if name not in TOOLS.get(agent, ()):
            raise ToolBoundaryError(f"{agent} cannot call {name}")
        if not state.get("owner_id") or not state.get("job_id"):
            raise ToolBoundaryError("server owner/job context required")
        if name not in self.handlers:
            return {"status": "disabled", "reason": "TOOL_NOT_CONFIGURED", "tool": name}
        return await self.handlers[name](state)


def supervisor(state: AgentState) -> dict:
    route = RuleRouter().route(state.get("text", ""), state.get("explicit_action"))
    if state.get("expected_agent") and route.agent != state["expected_agent"]:
        raise ToolBoundaryError("admission and execution routes differ; no tool called")
    return {
        "agent": route.agent,
        "route_reason": route.reason,
        "tool_trace": [],
        "intent_steps": [step.as_dict() for step in route.steps],
    }


def build_routing_graph():
    """Admission uses the same LangGraph supervisor, without executing business tools."""
    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_edge(START, "supervisor")
    graph.add_edge("supervisor", END)
    return graph.compile()


def build_graph(registry: ToolRegistry, checkpointer=None):

    def subgraph(agent: str, tool: str):
        async def boundary(state: AgentState) -> dict:
            if state.get("agent") != agent:
                raise ToolBoundaryError("subgraph route mismatch")
            return {"tool_trace": [tool]}

        async def execute(state: AgentState) -> dict:
            return {"output": await registry.call(agent, tool, state)}

        child = StateGraph(AgentState)
        child.add_node("boundary", boundary)
        child.add_node("execute", execute)
        child.add_edge(START, "boundary")
        child.add_edge("boundary", "execute")
        child.add_edge("execute", END)
        return child.compile()

    graph = StateGraph(AgentState)
    graph.add_node("supervisor", supervisor)
    graph.add_node("knowledge", subgraph("knowledge", "knowledge.retrieve"))
    graph.add_node("assessment", subgraph("assessment", "assessment.submit"))
    graph.add_node("planning", subgraph("planning", "plans.create"))
    graph.add_node("singing", subgraph("singing", "singing.capabilities"))
    graph.add_node(
        "clarify",
        lambda _: {
            "output": {
                "status": "clarify",
                "message": "请告诉我具体的声乐问题，也可以同时提出录音分析、练习安排等多个需求。",
            }
        },
    )
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges(
        "supervisor", lambda state: state["agent"], {name: name for name in TOOLS}
    )

    async def followup(state):
        result = state["output"]
        # No plan is produced from failed measurements.
        if result.get("status") != "measured":
            return {}

        plan = await registry.call(
            "planning",
            "plans.create",
            {**state, "input": state["followup_plan"], "measurement_report": result},
        )
        return {
            "output": {**result, "followup": plan},
            "tool_trace": state.get("tool_trace", []) + ["plans.create"],
        }

    graph.add_node("followup_plan", followup)
    graph.add_conditional_edges(
        "assessment",
        lambda s: "followup_plan" if s.get("followup_plan") else END,
        {"followup_plan": "followup_plan", END: END},
    )
    graph.add_edge("followup_plan", END)
    for name in TOOLS:
        if name != "assessment":
            graph.add_edge(name, END)
    return graph.compile(checkpointer=checkpointer)


def thread_config(owner_id: str, job_id: str) -> dict[str, Any]:
    # Never accept a client-supplied LangGraph thread_id.
    return {"configurable": {"thread_id": f"owner:{owner_id}:job:{job_id}"}, "recursion_limit": 40}


def workflow_output(steps: list[dict], *, processing=False) -> dict:
    completed = sum(step["status"] == "completed" for step in steps)
    status = (
        "processing"
        if processing
        else ("completed" if completed == len(steps) else "partial" if completed else "needs_input")
    )
    return {"status": status, "steps": steps}


def build_workflow_graph(
    registry: ToolRegistry, checkpointer=None, *, progress=None, skills=None, final_attempt=False
):
    """Each tool execution is checkpointed before publishing progress or starting the next step."""
    tool_names = {
        "knowledge": "knowledge.retrieve",
        "assessment": "assessment.submit",
        "planning": "plans.create",
        "singing": "singing.capabilities",
    }

    async def admit(state):
        route = supervisor(state)
        # Compare immutable intent fields, never client-provided tool names or agent instructions.
        expected = route["intent_steps"]
        actual = [
            {key: step.get(key) for key in ("id", "agent", "title", "text", "depends_on")}
            for step in state["execution_steps"]
        ]
        if expected != actual or not 1 <= len(actual) <= 4:
            raise ToolBoundaryError("admission and execution workflow differ; no tool called")
        return {**route, "step_index": 0, "step_results": []}

    async def announce(state):
        if progress:
            steps = list(state["step_results"])
            for index, step in enumerate(state["execution_steps"][state["step_index"] :]):
                steps.append(
                    {
                        "id": step["id"],
                        "agent": step["agent"],
                        "title": step["title"],
                        "status": "running" if index == 0 else "waiting",
                    }
                )
            await progress(workflow_output(steps, processing=True))
        return {}

    async def execute(state):
        from app.worker import PermanentJobError, RetryableJobError

        step = state["execution_steps"][state["step_index"]]
        entry = {key: step[key] for key in ("id", "agent", "title")}
        previous = {item["id"]: item for item in state["step_results"]}
        if any(previous.get(dep, {}).get("status") != "completed" for dep in step["depends_on"]):
            return {
                "pending_step": {
                    **entry,
                    "status": "blocked",
                    "message": "需先完成本次录音分析，再根据分析结果安排练习。",
                }
            }
        if step.get("question"):
            return {"pending_step": {**entry, "status": "needs_input", "message": step["question"]}}
        agent = step["agent"]
        tool_state = {
            **state,
            "agent": agent,
            "text": step["text"],
            "input": step["input"],
            "skill_context": await skills(agent) if skills else {},
        }
        if "assessment" in step["depends_on"]:
            tool_state["measurement_report"] = previous["assessment"]["result"]
        try:
            output = await registry.call(agent, tool_names[agent], tool_state)
        except PermanentJobError:
            # Expected permanent failures are isolated; transient errors use worker retries/checkpoints.
            output = {
                "status": "failed",
                "message": "此项处理失败，请检查输入后重试；其他独立需求会继续处理。",
            }
        except RetryableJobError:
            if not final_attempt:
                raise
            output = {
                "status": "unavailable",
                "message": "此项服务重试后仍不可用，请稍后重试；其他独立需求会继续处理。",
            }
        success = output.get("status") in {"answered", "measured", "planned", "generated"}
        return {
            "pending_step": {
                **entry,
                "status": "completed" if success else "failed",
                "result": output,
            },
            "tool_trace": state.get("tool_trace", []) + [tool_names[agent]],
        }

    async def collect(state):
        steps = [*state["step_results"], state["pending_step"]]
        index = state["step_index"] + 1
        output = workflow_output(steps, processing=index < len(state["execution_steps"]))
        return {"step_results": steps, "step_index": index, "output": output}

    graph = StateGraph(AgentState)
    for name, handler in (
        ("admit", admit),
        ("announce", announce),
        ("execute_step", execute),
        ("collect", collect),
    ):
        graph.add_node(name, handler)
    graph.add_edge(START, "admit")
    graph.add_edge("admit", "announce")
    graph.add_edge("announce", "execute_step")
    graph.add_edge("execute_step", "collect")
    graph.add_conditional_edges(
        "collect", lambda s: "announce" if s["step_index"] < len(s["execution_steps"]) else END
    )
    return graph.compile(checkpointer=checkpointer)
