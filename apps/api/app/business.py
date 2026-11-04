from app.worker import PermanentJobError


async def knowledge_tool(state: dict) -> dict:
    from app.config import get_settings
    from app.knowledge.service import KnowledgeService

    return await KnowledgeService(get_settings()).answer(state["text"])


async def planning_tool(state: dict) -> dict:
    return {"status": "disabled", "reason": "PLANNING_PIPELINE_NOT_READY"}


async def assessment_tool(state: dict) -> dict:
    raise PermanentJobError("ASSESSMENT_PIPELINE_NOT_READY")


async def singing_tool(state: dict) -> dict:
    return {
        "status": "disabled",
        "reason": "EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED",
        "requires_confirmation": True,
    }
