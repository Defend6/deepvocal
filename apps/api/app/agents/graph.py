from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from app.agents.plan import TOOL_AGENT, PlanValidationError, create_plan, validate_plan
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
    execution_plan: dict
    react_step: dict
    react_action_index: int
    react_selected_action: str
    react_decision: str
    react_result: dict
    react_final: dict
    react_trace_sink: list[str]


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
    if not processing and completed == 0:
        status = "failed" if any(step["status"] == "failed" for step in steps) else "needs_input"
    return {"status": status, "steps": steps}


def build_workflow_graph(
    registry: ToolRegistry, checkpointer=None, *, progress=None, skills=None, final_attempt=False
):
    """Plan-and-Execute supervisor with bounded Reasoning/Action/Observation agent cycles."""
    def identify_intent(state):
        return supervisor(state)

    def plan_tasks(state):
        approved = state["execution_steps"]
        # Immutable intent identity and ordering must still match admission before planning.
        expected = state["intent_steps"]
        actual = [
            {key: step.get(key) for key in ("id", "agent", "title", "text", "depends_on")}
            for step in approved
        ]
        if expected != actual:
            raise ToolBoundaryError("admission and execution intent differ; no tool called")
        return {"execution_plan": create_plan(approved).as_dict()}

    def validate_tasks(state):
        try:
            validate_plan(state["execution_plan"], state["execution_steps"])
        except PlanValidationError as exc:
            raise ToolBoundaryError(f"execution plan rejected: {exc}") from exc
        return {"step_index": 0, "step_results": [], "tool_trace": []}

    def reason(state):
        step = state["execution_plan"]["steps"][state["step_index"]]
        if not state.get("owner_id") or not state.get("job_id"):
            return {
                "react_final": {
                    "status": "failed",
                    "message": "执行上下文校验失败。",
                    "decision": "reject_missing_server_context",
                }
            }
        previous = {item["id"]: item for item in state.get("step_results", [])}
        unmet = [dep for dep in step["depends_on"] if previous.get(dep, {}).get("status") != "completed"]
        if unmet:
            return {
                "react_final": {
                    "status": "needs_input",
                    "message": "请先完成所需的前置步骤。",
                    "decision": "request_prerequisite",
                }
            }
        if step.get("question"):
            return {
                "react_final": {
                    "status": "needs_input",
                    "message": step["question"],
                    "decision": "request_user_input",
                }
            }
        index = state.get("react_action_index", 0)
        if index >= step["max_iterations"] or index >= len(step["actions"]):
            return {
                "react_final": {
                    "status": "failed",
                    "message": "Agent 已达到本步骤的执行预算。",
                    "decision": "budget_exhausted",
                }
            }
        return {
            "react_step": step,
            "react_selected_action": step["actions"][index],
            "react_decision": "execute_approved_action",
        }

    async def act(state):
        import asyncio

        from app.worker import PermanentJobError, RetryableJobError

        step = state["react_step"]
        action = state["react_selected_action"]
        agent = step["agent"]
        if action not in step["actions"] or TOOL_AGENT.get(action) != agent:
            raise ToolBoundaryError("Reasoning selected a tool outside the validated plan")
        if len(state.get("tool_trace", [])) >= sum(
            item["max_tool_calls"] for item in state["execution_plan"]["steps"]
        ):
            raise ToolBoundaryError("workflow tool-call budget exceeded")
        tool_state = {
            **state,
            "agent": agent,
            "text": step["text"],
            "input": step["input"],
            "skill_context": await skills(agent) if skills else {},
        }
        previous = {item["id"]: item for item in state.get("step_results", [])}
        if "assessment" in step["depends_on"]:
            report = previous.get("assessment", {}).get("result")
            if report is None:
                return {
                    "react_result": {
                        "status": "needs_input",
                        "message": "测评报告尚不可用，请先完成录音分析。",
                    }
                }
            tool_state["measurement_report"] = report
        trace_sink = state.get("react_trace_sink")
        if isinstance(trace_sink, list):
            trace_sink.append(action)
        try:
            async with asyncio.timeout(step["timeout_seconds"]):
                result = await registry.call(agent, action, tool_state)
        except TimeoutError as exc:
            if action in step["idempotent_tools"] and not final_attempt:
                raise RetryableJobError("AGENT_STEP_TIMEOUT") from exc
            result = {
                "status": "unavailable",
                "message": (
                    "歌声任务超时，系统无法确认外部执行状态；为避免重复生成，请先查看任务记录。"
                    if action not in step["idempotent_tools"]
                    else "本步骤处理超时，请稍后重试。"
                ),
            }
        except PermanentJobError:
            if len(state["execution_plan"]["steps"]) == 1:
                raise
            result = {"status": "failed", "message": "此项处理失败，请检查输入后重试。"}
        except RetryableJobError:
            if action in step["idempotent_tools"] and not final_attempt:
                raise
            result = {
                "status": "unavailable",
                "message": (
                    "外部歌声任务状态无法确认，为避免重复生成已停止自动重试。"
                    if action not in step["idempotent_tools"]
                    else "此项服务重试后仍不可用。"
                ),
            }
        return {
            "react_result": result,
            "tool_trace": state.get("tool_trace", []) + [action],
        }

    def observe(state):
        step = state["react_step"]
        action = state["react_selected_action"]
        result = state.get("react_result", {})
        index = state.get("react_action_index", 0)
        if action == "reports.read":
            report = result.get("report")
            if result.get("status") not in {"observed", "completed"} or not isinstance(report, dict):
                return {
                    "react_final": {
                        "status": "needs_input",
                        "message": result.get("message", "没有找到可用于制定练习计划的测评报告。"),
                        "decision": "request_user_input",
                    }
                }
            return {
                "measurement_report": report,
                "react_action_index": index + 1,
                "react_decision": "continue_with_plan_creation",
            }
        status = result.get("status")
        if status == "needs_input":
            final = {**result, "decision": "request_user_input"}
        elif status in set(step["terminal_statuses"]):
            final = {**result, "decision": "complete"}
        elif status in {"failed", "unavailable", "disabled"}:
            final = {**result, "decision": "return_failure_to_coordinator"}
        else:
            final = {
                "status": "failed",
                "message": "Agent 返回了无法识别的业务状态。",
                "decision": "return_failure_to_coordinator",
            }
        return {"react_final": final}

    def build_agent_subgraph(agent: str):
        """Compile a role-bound child graph; role cannot be changed by planner/tool output."""
        def role_reason(state):
            if state["execution_plan"]["steps"][state["step_index"]]["agent"] != agent:
                raise ToolBoundaryError("agent subgraph role mismatch")
            return reason(state)

        react = StateGraph(AgentState)
        react.add_node("reasoning", role_reason)
        react.add_node("action", act)
        react.add_node("observation", observe)
        react.add_edge(START, "reasoning")
        react.add_conditional_edges(
            "reasoning",
            lambda state: "END" if state.get("react_final") else "action",
            {"END": END, "action": "action"},
        )
        react.add_edge("action", "observation")
        react.add_conditional_edges(
            "observation",
            lambda state: (
                "reasoning"
                if state.get("react_decision") == "continue_with_plan_creation"
                else "END"
            ),
            {"reasoning": "reasoning", "END": END},
        )
        return react.compile()

    agent_subgraphs = {
        agent: build_agent_subgraph(agent)
        for agent in ("knowledge", "assessment", "planning", "singing")
    }

    async def announce(state):
        if progress:
            steps = list(state["step_results"])
            for index, step in enumerate(state["execution_plan"]["steps"][state["step_index"] :]):
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

    async def dispatch(state):
        step = state["execution_plan"]["steps"][state["step_index"]]
        entry = {key: step[key] for key in ("id", "agent", "title")}
        previous = {item["id"]: item for item in state["step_results"]}
        if step.get("blocked_reason"):
            return {
                "pending_step": {
                    **entry,
                    "status": "blocked",
                    "decision": "replanned_dependency_block",
                    "message": step["blocked_reason"],
                }
            }
        if any(previous.get(dep, {}).get("status") != "completed" for dep in step["depends_on"]):
            return {
                "pending_step": {
                    **entry,
                    "status": "blocked",
                    "decision": "dependency_failed",
                    "message": "需先完成本次录音分析，再根据分析结果安排练习。",
                }
            }
        if step.get("question"):
            return {
                "pending_step": {
                    **entry,
                    "status": "needs_input",
                    "decision": "request_user_input",
                    "message": step["question"],
                }
            }
        import asyncio

        from app.worker import RetryableJobError

        trace_sink = []
        try:
            async with asyncio.timeout(step["timeout_seconds"]):
                result = await agent_subgraphs[step["agent"]].ainvoke(
                    {
                        **state,
                        "react_step": step,
                        "react_action_index": 0,
                        "react_final": {},
                        "react_trace_sink": trace_sink,
                    }
                )
            final = result.get("react_final", {})
        except TimeoutError as exc:
            idempotent = all(action in step["idempotent_tools"] for action in step["actions"])
            if idempotent and not final_attempt:
                raise RetryableJobError("AGENT_TOTAL_TIMEOUT") from exc
            final = {
                "status": "unavailable",
                "message": (
                    "Agent 执行超时且外部任务状态无法确认，为避免重复执行已停止自动重试。"
                    if not idempotent
                    else "Agent 执行超时，已达到本步骤的总时限。"
                ),
                "decision": "timeout",
            }
            result = {**state, "tool_trace": state.get("tool_trace", []) + trace_sink}
        status = final.get("status")
        workflow_status = (
            "completed"
            if status in {"answered", "measured", "planned", "generated"}
            else "needs_input"
            if status == "needs_input"
            else "failed"
        )
        return {
            "pending_step": {
                **entry,
                "status": workflow_status,
                "decision": final.get("decision"),
                "result": final,
                **({"message": final["message"]} if final.get("message") else {}),
            },
            "tool_trace": result.get("tool_trace", state.get("tool_trace", [])),
        }

    async def coordinate(state):
        steps = [*state["step_results"], state["pending_step"]]
        index = state["step_index"] + 1
        # Constrained replanning: propagate failed/needs-input prerequisites to descendants,
        # while preserving runnable independent tasks in their original approved order.
        plan_steps = [dict(step) for step in state["execution_plan"]["steps"]]
        failed_ids = {
            item["id"]
            for item in steps
            if item["status"] != "completed"
        }
        blocked_ids = set(failed_ids)
        for planned in plan_steps[index:]:
            blocked_dep = next((dep for dep in planned["depends_on"] if dep in blocked_ids), None)
            if blocked_dep:
                source = next(item for item in steps if item["id"] == blocked_dep)
                planned["blocked_reason"] = (
                    f"前置步骤“{source['title']}”未完成，已将本步骤调整为等待前置条件。"
                )
                blocked_ids.add(planned["id"])
        updated_plan = {**state["execution_plan"], "steps": plan_steps}
        output = workflow_output(steps, processing=index < len(plan_steps))
        return {
            "step_results": steps,
            "step_index": index,
            "execution_plan": updated_plan,
            "output": output,
        }

    def summarize(state):
        return {"output": workflow_output(state["step_results"])}

    graph = StateGraph(AgentState)
    for name, handler in (
        ("identify_intent", identify_intent),
        ("plan", plan_tasks),
        ("validate_plan", validate_tasks),
        ("dispatch", dispatch),
        ("announce", announce),
        ("coordinate", coordinate),
        ("summarize", summarize),
    ):
        graph.add_node(name, handler)
    graph.add_edge(START, "identify_intent")
    graph.add_edge("identify_intent", "plan")
    graph.add_edge("plan", "validate_plan")
    graph.add_edge("validate_plan", "announce")
    graph.add_edge("announce", "dispatch")
    graph.add_edge("dispatch", "coordinate")
    graph.add_conditional_edges(
        "coordinate",
        lambda state: "announce" if state["step_index"] < len(state["execution_plan"]["steps"]) else "summarize",
        {"announce": "announce", "summarize": "summarize"},
    )
    graph.add_edge("summarize", END)
    return graph.compile(checkpointer=checkpointer)
