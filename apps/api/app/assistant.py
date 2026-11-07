from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agents.graph import build_routing_graph
from app.agents.intent_input import planning_input
from app.agents.router import wants_evidence
from app.identity import Session, User
from app.jobs import JobCreate, enqueue

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    action: str | None = Field(default=None, min_length=1, max_length=80)
    input: dict = Field(default_factory=dict)
    input_asset_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


async def dispatch(
    request: AssistantRequest, user, session, *, commit=True, report_job_id=None
) -> dict:
    automatic = request.action is None
    if automatic and request.input:
        raise HTTPException(422, "automatic requests accept text and optional audio only")
    route = await build_routing_graph().ainvoke(
        {"text": request.text, "explicit_action": request.action}
    )
    agent = route["agent"]
    if agent == "clarify":
        return {
            "status": "clarify",
            "message": "请告诉我具体的声乐问题，也可以同时提出录音分析、练习安排等多个需求。",
        }
    if automatic and len(route.get("intent_steps", [])) > 1:
        return await dispatch_workflow(
            request,
            route["intent_steps"],
            user,
            session,
            commit=commit,
            report_job_id=report_job_id,
        )
    if automatic and agent == "singing":
        return {
            "status": "clarify",
            "message": "生成个性化歌声前，请在歌声生成工作台上传本人声音、填写歌词并确认使用授权。",
        }
    payload = {**request.input, "text": request.text}
    workflow = {"show_evidence": wants_evidence(request.text)}
    if automatic and agent == "planning":
        import re

        mentions_report = any(word in request.text for word in ("报告", "上次测评", "刚才测评"))
        plan_text = (
            re.sub(r"报告|上次|之前|刚才", "", request.text) if mentions_report else request.text
        )
        payload, question = planning_input(plan_text)
        if question:
            return {"status": "clarify", "message": question}
        payload["text"] = request.text
        if mentions_report:
            from sqlalchemy import text

            report_id = await session.scalar(
                text("""SELECT id FROM assessment_reports
              WHERE owner_id=:owner AND (CAST(:job AS uuid) IS NULL OR source_job_id=CAST(:job AS uuid))
              ORDER BY created_at DESC,id DESC LIMIT 1"""),
                {"owner": user["id"], "job": report_job_id},
            )
            if not report_id:
                return {
                    "status": "clarify",
                    "message": "还没有可用的测评报告，请先上传录音完成分析。",
                }
            payload["report_id"] = str(report_id)
    if agent == "assessment" and request.input_asset_id is None:
        if automatic and report_job_id:
            from sqlalchemy import text as sql_text

            previous_asset = await session.scalar(
                sql_text("""SELECT input_asset_id FROM jobs
              WHERE id=:id AND owner_id=:owner AND kind='assessment'"""),
                {"id": report_job_id, "owner": user["id"]},
            )
            if previous_asset:
                request = request.model_copy(update={"input_asset_id": previous_asset})
        if request.input_asset_id is None:
            return {
                "status": "clarify",
                "message": "请附上本人的清唱录音，上传完成后就可以继续分析。",
            }
    if (
        automatic
        and agent == "assessment"
        and any(word in request.text for word in ("全曲", "整首", "长音", "参考曲"))
    ):
        return {
            "status": "clarify",
            "message": "请选择参考曲目，或在专业工作台补充长音目标时长后开始测评。",
        }
    try:
        job = await enqueue(
            session,
            user["id"],
            JobCreate(
                kind=agent,
                input=payload,
                input_asset_id=request.input_asset_id if agent == "assessment" else None,
                idempotency_key=request.idempotency_key,
            ),
            automatic=automatic,
            commit=commit,
            workflow=workflow,
        )
    except HTTPException as exc:
        if agent == "assessment" and exc.status_code == 409 and "unverified" in str(exc.detail):
            return {
                "status": "blocked",
                "message": "录音正在准备中，请等待检查完成后重新发送。",
            }
        raise
    return {"status": "queued", "agent": agent, "job": job}


async def dispatch_workflow(request, steps, user, session, *, commit, report_job_id):
    """Prepare independent prerequisites without discarding the rest of a composite request."""
    import re

    from sqlalchemy import text

    asset = request.input_asset_id
    needs_audio = any(step["agent"] == "assessment" for step in steps)
    if needs_audio and asset is None and report_job_id:
        asset = await session.scalar(
            text("SELECT input_asset_id FROM jobs WHERE id=:id AND owner_id=:owner"),
            {"id": report_job_id, "owner": user["id"]},
        )
    asset_pending = False
    if needs_audio and asset:
        status = await session.scalar(
            text("SELECT status FROM audio_assets WHERE id=:id AND owner_id=:owner"),
            {"id": str(asset), "owner": user["id"]},
        )
        if status is None:
            raise HTTPException(404, "audio asset not found")
        asset_pending = status != "ready"
    prepared = []
    for step in steps:
        payload = {"text": step["text"]}
        question = None
        if step["agent"] == "planning":
            mentions_report = any(word in step["text"] for word in ("报告", "上次测评", "刚才测评"))
            plan_text = (
                re.sub(r"报告|上次|之前|刚才", "", step["text"])
                if mentions_report
                else step["text"]
            )
            payload, question = planning_input(plan_text)
            # Health constraints apply across clauses, while durations stay scoped to this step.
            from app.knowledge.policy import health_boundary

            if health_boundary(request.text):
                payload["discomfort"] = True
            if mentions_report and not step["depends_on"] and not question:
                report = await session.scalar(
                    text("""SELECT id FROM assessment_reports WHERE owner_id=:owner
                    AND (CAST(:job AS uuid) IS NULL OR source_job_id=CAST(:job AS uuid))
                    ORDER BY created_at DESC,id DESC LIMIT 1"""),
                    {"owner": user["id"], "job": report_job_id},
                )
                if report:
                    payload["report_id"] = str(report)
                else:
                    question = "还没有可用的测评报告，请先上传录音完成分析。"
        elif step["agent"] == "assessment":
            payload["input_asset_id"] = str(asset) if asset else None
            if not asset:
                question = "请附上本人的清唱录音，上传完成后就可以继续分析。"
            elif asset_pending:
                question = "录音正在准备中，请等待检查完成后重新发送。"
            elif any(word in step["text"] for word in ("全曲", "整首", "长音", "参考曲")):
                question = "请选择参考曲目，或在专业工作台补充长音目标时长后开始测评。"
        elif step["agent"] == "singing":
            question = "生成个性化歌声前，请在歌声生成工作台上传本人声音、填写歌词并确认使用授权。"
        prepared.append({**step, "input": payload, "question": question})
    executable = [
        step
        for step in prepared
        if not step["question"]
        and not any(
            previous["question"] for previous in prepared if previous["id"] in step["depends_on"]
        )
    ]
    if not executable:
        return {
            "status": "clarify",
            "message": "\n".join(
                dict.fromkeys(step["question"] for step in prepared if step["question"])
            ),
        }
    job = await enqueue(
        session,
        user["id"],
        JobCreate(
            kind=steps[0]["agent"],
            input={"text": request.text},
            input_asset_id=asset if needs_audio and not asset_pending else None,
            idempotency_key=request.idempotency_key,
        ),
        automatic=True,
        commit=commit,
        workflow={"steps": prepared, "show_evidence": wants_evidence(request.text)},
    )
    unmet = {step["id"] for step in prepared if step["question"]}
    pending = "；".join(
        step["text"]
        for step in prepared
        if step["id"] in unmet or any(dep in unmet for dep in step["depends_on"])
    )
    return {"status": "queued", "agent": steps[0]["agent"], "job": job, "pending_text": pending}


@router.post("")
async def ask(request: AssistantRequest, user: User, session: Session) -> dict:
    return await dispatch(request, user, session)
