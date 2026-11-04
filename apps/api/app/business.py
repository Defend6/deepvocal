import asyncio
import hashlib

from sqlalchemy import text

from app.worker import PermanentJobError


async def knowledge_tool(state: dict) -> dict:
    from app.config import get_settings
    from app.knowledge.service import KnowledgeService

    return await KnowledgeService(get_settings()).answer(state["text"])


async def planning_tool(state: dict) -> dict:
    from app.database import _session_factory
    from app.planning import PlanningInput, create_plan

    config = PlanningInput.model_validate(state["input"])
    report = None
    if config.report_id:
        async with _session_factory()() as session:
            report = await session.scalar(
                text("SELECT payload FROM assessment_reports WHERE id=:id AND owner_id=:owner"),
                {"id": str(config.report_id), "owner": state["owner_id"]},
            )
        if report is None:
            raise PermanentJobError("REPORT_NOT_FOUND")
    return create_plan(config, report)


async def assessment_tool(state: dict) -> dict:
    from app.audio import AssessmentInput, assess_bytes
    from app.config import get_settings
    from app.database import _session_factory
    from app.storage.s3 import S3Storage

    async with _session_factory()() as session:
        asset = (
            (
                await session.execute(
                    text(
                        "SELECT * FROM audio_assets WHERE id=:id AND owner_id=:owner AND status='ready'"
                    ),
                    {"id": state["input"]["input_asset_id"], "owner": state["owner_id"]},
                )
            )
            .mappings()
            .first()
        )
    if asset is None:
        raise PermanentJobError("READY_ASSET_REQUIRED")
    storage = S3Storage.from_settings(get_settings())

    def load():
        response = storage.client.get_object(Bucket=storage.config.bucket, Key=asset["object_key"])
        body = response["Body"]
        try:
            data = body.read(get_settings().max_audio_size_bytes + 1)
        finally:
            body.close()
        if len(data) != asset["size_bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise PermanentJobError("ASSET_CONTENT_MISMATCH")
        return data

    data = await asyncio.to_thread(load)
    try:
        return await asyncio.to_thread(
            assess_bytes, data, AssessmentInput.model_validate(state["input"])
        )
    except ValueError as exc:
        raise PermanentJobError(str(exc)[:80]) from exc


async def singing_tool(state: dict) -> dict:
    return {
        "status": "disabled",
        "reason": "EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED",
        "requires_confirmation": True,
    }
