from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.agents.graph import ToolRegistry, build_graph, thread_config
from app.config import get_settings


async def execute_job(job: dict) -> dict:
    from app.business import assessment_tool, knowledge_tool, planning_tool, singing_tool
    from app.skills import selected_skill

    async def knowledge_for_current_claim(state):
        return await knowledge_tool(state, claim_context=job)

    handlers = {
        "knowledge.retrieve": knowledge_for_current_claim,
        "assessment.submit": assessment_tool,
        "plans.create": planning_tool,
        "singing.capabilities": singing_tool,
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
        "explicit_action": actions[job["kind"]],
        "input": {
            **job["request"].get("input", {}),
            "input_asset_id": job["request"].get("input_asset_id"),
        },
        "skill_context": await selected_skill(str(job["owner_id"]), job["kind"]),
    }
    dsn = get_settings().database_url.replace("postgresql+asyncpg://", "postgresql://", 1)
    async with await AsyncConnection.connect(
        dsn, autocommit=True, prepare_threshold=0, row_factory=dict_row
    ) as conn:
        saver = AsyncPostgresSaver(conn)
        await saver.setup()
        graph = build_graph(ToolRegistry(handlers), saver)
        config = thread_config(state["owner_id"], state["job_id"])
        # Re-run failed pending nodes, reuse completed checkpoints for retry idempotency.
        snapshot = await graph.aget_state(config)
        if snapshot.values and not snapshot.next:
            result = snapshot.values
        else:
            result = await graph.ainvoke(None if snapshot.values else state, config)
        return {
            "agent": result["agent"],
            "route_reason": result["route_reason"],
            "tool_trace": result.get("tool_trace", []),
            "skill": {key: value for key, value in state["skill_context"].items() if key != "body"},
            **result["output"],
        }
