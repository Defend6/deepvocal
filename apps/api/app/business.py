from app.worker import PermanentJobError


async def knowledge_tool(state: dict) -> dict:
    return {"status": "disabled", "reason": "KNOWLEDGE_PIPELINE_NOT_READY"}


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
