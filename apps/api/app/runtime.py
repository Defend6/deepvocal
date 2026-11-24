from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.agents.graph import ToolBoundaryError, ToolRegistry, build_workflow_graph, thread_config
from app.agents.router import RuleRouter
from app.config import get_settings


def format_workflow_result(agent: str, route_reason: str, tool_trace: list[str], output: dict) -> dict:
    """Preserve the established single-agent result contract and expose orchestration metadata."""
    base = {"agent": agent, "route_reason": route_reason, "tool_trace": tool_trace}
    steps = output.get("steps", [])
    if len(steps) == 1:
        business_result = steps[0].get("result")
        if isinstance(business_result, dict):
            return {**base, **business_result, "workflow": output}
        return {**base, "status": steps[0].get("status", output.get("status")), "workflow": output}
    return {**base, **output}


async def execute_job(job: dict) -> dict:
    from app.business import assessment_tool, knowledge_tool, planning_tool, read_report_tool
    from app.skills import selected_skill

    async def knowledge_for_current_claim(state):
        return await knowledge_tool(state, claim_context=job)

    async def singing_for_current_claim(state):
        from app.singing import generate

        return await generate(state, job)

    async def assessment_for_current_claim(state):
        return await assessment_tool(state, claim_context=job)

    handlers = {
        "knowledge.retrieve": knowledge_for_current_claim,
        "assessment.submit": assessment_for_current_claim,
        "plans.create": planning_tool,
        "reports.read": read_report_tool,
        "singing.capabilities": singing_for_current_claim,
    }
    actions = {
        "knowledge": "knowledge.ask",
        "assessment": "assessment.submit",
        "planning": "planning.create",
        "singing": "singing.generate",
    }
    state = {
        "owner_id": str(job["owner_id"]),
        "job_id": str(job["id"]),
        "text": job["request"].get("input", {}).get("text", ""),
        "explicit_action": (
            None
            if job["request"].get("routing", {}).get("mode") == "automatic"
            else actions[job["kind"]]
        ),
        "expected_agent": job["kind"],
        "input": {
            **job["request"].get("input", {}),
            "input_asset_id": job["request"].get("input_asset_id"),
        },
        "skill_context": await selected_skill(str(job["owner_id"]), job["kind"]),
        "followup_plan": job["request"].get("workflow", {}).get("followup_plan"),
    }
    steps = job["request"].get("workflow", {}).get("steps")
    if steps is None:
        route = RuleRouter().route(state["text"], state["explicit_action"])
        if route.agent != job["kind"] or len(route.steps) != 1:
            raise ToolBoundaryError("single-job admission does not match the server route")
        steps = [
            {**step.as_dict(), "input": state["input"], "question": None}
            for step in route.steps
        ]
    state["execution_steps"] = steps

    async def progress(output):
        from app.database import _session_factory
        from app.worker import publish_progress

        await publish_progress(_session_factory(), job, output)

    async def skills(agent):
        return await selected_skill(str(job["owner_id"]), agent)

    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with await AsyncConnection.connect(
        dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = AsyncPostgresSaver(conn)
        await saver.setup()
        graph = build_workflow_graph(
            ToolRegistry(handlers),
            saver,
            progress=progress,
            skills=skills,
            final_attempt=job["attempt"] >= job["max_attempts"],
        )
        config = thread_config(state["owner_id"], state["job_id"])
        # Re-run failed pending nodes, reuse completed checkpoints for retry idempotency.
        snapshot = await graph.aget_state(config)
        if snapshot.values and not snapshot.next:
            result = snapshot.values
        else:
            result = await graph.ainvoke(None if snapshot.values else state, config)
        formatted = format_workflow_result(
            result["agent"], result["route_reason"], result.get("tool_trace", []), result["output"]
        )
        return {
            **formatted,
            "skill": {key: value for key, value in state["skill_context"].items() if key != "body"},
        }
