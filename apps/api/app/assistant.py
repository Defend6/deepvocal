from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.agents.router import RuleRouter
from app.identity import Session, User
from app.jobs import JobCreate, enqueue

router = APIRouter(prefix="/v1/assistant", tags=["assistant"])


class AssistantRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000)
    action: str | None = Field(default=None, max_length=80)
    input: dict = Field(default_factory=dict)
    input_asset_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("")
async def ask(request: AssistantRequest, user: User, session: Session) -> dict:
    route = RuleRouter().route(request.text, request.action)
    if route.agent == "clarify":
        return {"status": "clarify", "message": "意图不明确，请选择功能或补充问题。"}
    job = await enqueue(
        session,
        user["id"],
        JobCreate(
            kind=route.agent,
            input={**request.input, "text": request.text},
            input_asset_id=request.input_asset_id,
            idempotency_key=request.idempotency_key,
        ),
    )
    return {"status": "queued", "agent": route.agent, "job": job}
