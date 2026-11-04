from uuid import UUID

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agents.graph import build_routing_graph
from app.agents.intent_input import planning_input
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


@router.post("")
async def ask(request: AssistantRequest, user: User, session: Session) -> dict:
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
            "message": "请补充希望解决的具体问题：是解释概念、分析自己的录音，还是安排练习？若有多个目标，请先描述这次最想完成的一件事。",
        }
    payload = {**request.input, "text": request.text}
    if automatic and agent == "planning":
        payload, question = planning_input(request.text)
        if question:
            return {"status": "clarify", "message": question}
    if agent == "assessment" and request.input_asset_id is None:
        return {
            "status": "clarify",
            "message": "请先附上本人的单人清唱录音，并说明想观察什么。目前上传文件仍需校验，校验开放前无法开始测评。",
        }
    if (
        automatic
        and agent == "assessment"
        and any(word in request.text for word in ("全曲", "整首", "长音", "参考曲"))
    ):
        return {
            "status": "clarify",
            "message": "全曲参考评分需要参考音符，长音任务需要目标时长；请在专业工作台补充对应材料。当前仅预签名上传，尚不能开始测评。",
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
        )
    except HTTPException as exc:
        if agent == "assessment" and exc.status_code == 409 and "presign-only" in str(exc.detail):
            return {
                "status": "blocked",
                "message": "录音已上传，但仍待校验。上传完成校验尚未开放，本次不会创建测评任务；开放后请重新提交。",
            }
        raise
    return {"status": "queued", "agent": agent, "job": job}
