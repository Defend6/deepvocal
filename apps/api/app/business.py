import asyncio
import hashlib

from sqlalchemy import text

from app.worker import PermanentJobError


async def knowledge_tool(state: dict, claim_context=None) -> dict:
    from app.config import get_settings
    from app.knowledge.service import KnowledgeService

    return await KnowledgeService(
        get_settings(),
        owner_id=state["owner_id"],
        job_id=state["job_id"],
        claim_context=claim_context,
    ).answer(state["text"], state.get("skill_context"))


async def planning_tool(state: dict) -> dict:
    from app.database import _session_factory
    from app.planning import PlanningInput, create_plan

    config = PlanningInput.model_validate(state["input"])
    from app.memory import effective_preferences

    async with _session_factory()() as session:
        preferences = await effective_preferences(session, state["owner_id"])
        history = (
            (
                await session.execute(
                    text("""SELECT payload FROM practice_records
          WHERE owner_id=:owner ORDER BY created_at DESC,id DESC LIMIT 3"""),
                    {"owner": state["owner_id"]},
                )
            )
            .mappings()
            .all()
        )
    for key in ("goal", "minutes"):
        if key not in config.model_fields_set and key in preferences:
            setattr(config, key, preferences[key])
    report = state.get("measurement_report")
    if config.report_id:
        async with _session_factory()() as session:
            report = await session.scalar(
                text("SELECT payload FROM assessment_reports WHERE id=:id AND owner_id=:owner"),
                {"id": str(config.report_id), "owner": state["owner_id"]},
            )
        if report is None:
            raise PermanentJobError("REPORT_NOT_FOUND")
    return create_plan(config, report, [dict(item) for item in history])


async def assessment_tool(state: dict, claim_context=None) -> dict:
    from app.audio import AssessmentInput
    from app.audio_process import assess_isolated
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
        response = storage.client.get_object(
            Bucket=storage.config.bucket, Key=asset["verified_key"] or asset["object_key"]
        )
        body = response["Body"]
        try:
            data = body.read(get_settings().max_audio_size_bytes + 1)
        finally:
            body.close()
        if len(data) != asset["size_bytes"] or hashlib.sha256(data).hexdigest() != asset["sha256"]:
            raise PermanentJobError("ASSET_CONTENT_MISMATCH")
        return data

    data = await asyncio.to_thread(load)
    config = AssessmentInput.model_validate(state["input"])
    if config.separate_vocals:
        from app.separation import separate

        data = await separate(data, claim_context or {"worker_token": state["job_id"]})
    try:
        return await assess_isolated(data, config, get_settings())
    except ValueError as exc:
        raise PermanentJobError(str(exc)[:80]) from exc


async def singing_tool(state: dict) -> dict:
    return {
        "status": "disabled",
        "reason": "EXTERNAL_SINGING_SERVICE_NOT_CONFIGURED",
        "requires_confirmation": True,
    }
