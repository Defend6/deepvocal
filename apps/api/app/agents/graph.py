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
    return {"agent": route.agent, "route_reason": route.reason, "tool_trace": []}


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
                "message": "请补充希望解决的具体问题，例如解释一个声乐概念、分析自己的录音，或安排几天练习。一次先描述一个目标即可。",
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
    return {"configurable": {"thread_id": f"owner:{owner_id}:job:{job_id}"}, "recursion_limit": 12}
