from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Tool permissions and bounded execution budgets are server-owned policy, never model output.
AGENT_POLICIES = {
    "knowledge": {
        "tools": ("knowledge.retrieve",),
        "max_iterations": 1,
        "max_tool_calls": 1,
        "timeout_seconds": 120,
        "idempotent_tools": ("knowledge.retrieve",),
    },
    "assessment": {
        "tools": ("assessment.submit",),
        "max_iterations": 1,
        "max_tool_calls": 1,
        "timeout_seconds": 120,
        "idempotent_tools": ("assessment.submit",),
    },
    "planning": {
        "tools": ("plans.create",),
        "max_iterations": 1,
        "max_tool_calls": 1,
        "timeout_seconds": 30,
        "idempotent_tools": ("reports.read", "plans.create"),
    },
    "singing": {
        "tools": ("singing.capabilities",),
        "max_iterations": 1,
        "max_tool_calls": 1,
        "timeout_seconds": 180,
        "idempotent_tools": (),
    },
}

TOOL_AGENT = {
    "knowledge.retrieve": "knowledge",
    "assessment.submit": "assessment",
    "plans.create": "planning",
    "reports.read": "planning",
    "singing.capabilities": "singing",
}


class PlanValidationError(ValueError):
    """A request plan does not match the server-approved intent and execution policy."""


@dataclass(frozen=True)
class WorkflowPlan:
    version: int
    steps: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {"version": self.version, "steps": [dict(step) for step in self.steps]}


def create_plan(steps: list[dict[str, Any]]) -> WorkflowPlan:
    """Attach execution policy to deterministic, server-admitted intent steps."""
    planned: list[dict[str, Any]] = []
    for step in steps:
        agent = step.get("agent")
        policy = AGENT_POLICIES.get(agent)
        if policy is None:
            raise PlanValidationError("unknown agent")
        actions = list(policy["tools"])
        # A dependent planning step first performs an owner-scoped report read, then creates the plan.
        if agent == "planning" and (
            "assessment" in step.get("depends_on", [])
            or (step.get("input") or {}).get("report_id")
        ):
            actions.insert(0, "reports.read")
        planned.append(
            {
                **step,
                "actions": actions,
                "max_iterations": max(1, len(actions)),
                "max_tool_calls": len(actions),
                "timeout_seconds": policy["timeout_seconds"],
                "idempotent_tools": list(policy["idempotent_tools"]),
                "terminal_statuses": ["answered", "measured", "planned", "generated"],
            }
        )
    plan = WorkflowPlan(version=1, steps=tuple(planned))
    validate_plan(plan.as_dict(), steps)
    return plan


def validate_plan(plan: dict[str, Any], approved_steps: list[dict[str, Any]]) -> None:
    """Validate ordering, dependencies, tool permissions and finite budgets before dispatch."""
    if not isinstance(plan, dict) or plan.get("version") != 1:
        raise PlanValidationError("unsupported plan version")
    steps = plan.get("steps")
    if not isinstance(steps, list) or not 1 <= len(steps) <= 4 or len(steps) != len(approved_steps):
        raise PlanValidationError("invalid plan size")

    approved_ids = [step.get("id") for step in approved_steps]
    if len(set(approved_ids)) != len(approved_ids):
        raise PlanValidationError("duplicate approved step")
    seen: set[str] = set()
    for planned, approved in zip(steps, approved_steps, strict=True):
        for field in ("id", "agent", "title", "text", "depends_on", "input", "question"):
            if planned.get(field) != approved.get(field):
                raise PlanValidationError(f"plan changed approved field: {field}")
        step_id, agent = planned.get("id"), planned.get("agent")
        policy = AGENT_POLICIES.get(agent)
        actions = planned.get("actions")
        if not policy or step_id in seen or not isinstance(actions, list) or not actions:
            raise PlanValidationError("invalid step identity or actions")
        if any(TOOL_AGENT.get(tool) != agent for tool in actions):
            raise PlanValidationError("tool outside agent allowlist")
        needs_report_read = agent == "planning" and (
            "assessment" in planned["depends_on"] or planned["input"].get("report_id")
        )
        if needs_report_read:
            if actions != ["reports.read", "plans.create"]:
                raise PlanValidationError("report-based planning must verify its report first")
        elif actions != list(policy["tools"]):
            raise PlanValidationError("tool sequence differs from approved policy")
        max_iterations = planned.get("max_iterations")
        max_calls = planned.get("max_tool_calls")
        timeout = planned.get("timeout_seconds")
        if (
            type(max_iterations) is not int
            or not 1 <= max_iterations <= 2
            or max_iterations < len(actions)
            or type(max_calls) is not int
            or not len(actions) <= max_calls <= 2
            or type(timeout) is not int
            or not 1 <= timeout <= policy["timeout_seconds"]
        ):
            raise PlanValidationError("unbounded agent budget")
        if planned.get("idempotent_tools") != list(policy["idempotent_tools"]):
            raise PlanValidationError("idempotency policy mismatch")
        if planned.get("terminal_statuses") != ["answered", "measured", "planned", "generated"]:
            raise PlanValidationError("terminal status policy mismatch")
        deps = planned.get("depends_on")
        if (
            not isinstance(deps, list)
            or len(set(deps)) != len(deps)
            or any(dep not in seen for dep in deps)
        ):
            raise PlanValidationError("dependencies must refer to earlier plan steps")
        seen.add(step_id)


def public_plan(plan: dict[str, Any]) -> dict[str, Any]:
    """Return only concise, user-safe plan information; omit policy internals."""
    return {
        "version": plan["version"],
        "steps": [
            {key: step[key] for key in ("id", "agent", "title", "text", "depends_on")}
            for step in plan["steps"]
        ],
    }
