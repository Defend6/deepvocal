"""Durable owner-scoped clarification, bounded history and optimistic turn ordering."""

import hashlib
import json
import re
from uuid import UUID, uuid4

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import text

from app.assistant import AssistantRequest, dispatch
from app.identity import Session, User

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


class ConversationCreate(BaseModel):
    title: str = Field(default="新的练声对话", min_length=1, max_length=100)


class TurnCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=4000, pattern=r"\S")
    revision: int = Field(ge=0)
    input_asset_id: UUID | None = None
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")


async def owned_conversation(session, owner, identifier, lock=False):
    row = (
        (
            await session.execute(
                text(
                    "SELECT * FROM conversations WHERE id=:id AND owner_id=:owner"
                    + (" FOR UPDATE" if lock else "")
                ),
                {"id": str(identifier), "owner": owner},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(404, "conversation not found")
    return row


@router.post("", status_code=201)
async def create_conversation(body: ConversationCreate, user: User, session: Session):
    identifier = str(uuid4())
    await session.execute(
        text("INSERT INTO conversations(id,owner_id,title) VALUES (:id,:owner,:title)"),
        {"id": identifier, "owner": user["id"], "title": body.title},
    )
    await session.commit()
    return {"id": identifier, "title": body.title, "revision": 0}


@router.get("")
async def list_conversations(user: User, session: Session):
    rows = (
        await session.execute(
            text("""SELECT id,title,revision,updated_at FROM conversations
      WHERE owner_id=:owner ORDER BY updated_at DESC LIMIT 50"""),
            {"owner": user["id"]},
        )
    ).mappings()
    return jsonable_encoder([dict(row) for row in rows])


@router.get("/{conversation_id}")
async def get_conversation(conversation_id: UUID, user: User, session: Session):
    conversation = await owned_conversation(session, user["id"], conversation_id)
    turns = (
        await session.execute(
            text("""SELECT user_text,response,created_at FROM
      (SELECT * FROM conversation_turns WHERE conversation_id=:id AND owner_id=:owner
       ORDER BY created_at DESC,id DESC LIMIT 30) t ORDER BY created_at,id"""),
            {"id": str(conversation_id), "owner": user["id"]},
        )
    ).mappings()
    return jsonable_encoder(
        {
            "id": str(conversation_id),
            "title": conversation["title"],
            "revision": conversation["revision"],
            "turns": [dict(t) for t in turns],
        }
    )


def resolve_turn(message, context):
    """Only explicit follow-up patterns inherit context; unrelated questions reset it."""
    pending = context.get("pending_text", "")
    short_answer = bool(
        re.fullmatch(r"[每天分钟共天周数字一二三四五六七八九十两\d，,。\s]+", message)
    )
    short_answer |= bool(
        re.fullmatch(
            r"(?:音准|节奏|稳定性|基础|先分析|先规划|制定计划|继续|就按这个)", message.strip()
        )
    )
    if short_answer and (pending or context.get("last_text")):
        pending = pending or context["last_text"]
        if message.strip() == "先分析":
            return "请分析我的录音"
        if message.strip() in {"先规划", "制定计划"}:
            constraints = re.findall(r"[0-9一二两三四五六七八九十]+\s*(?:分钟|天)", pending)
            return "请制定练习计划，" + "，".join(constraints)
        if "分钟" in message:
            pending = re.sub(r"[0-9零一二两三四五六七八九十]+\s*分钟", "", pending)
        if "天" in message:
            pending = re.sub(r"[0-9零一二两三四五六七八九十]+\s*天", "", pending)
        return pending + "；补充：" + message
    if context.get("last_text") and re.match(r"^(那|这个|刚才|再解释|详细说明|举个例子)", message):
        return context["last_text"] + "；追问：" + message
    return message


@router.post("/{conversation_id}/messages")
async def post_turn(conversation_id: UUID, body: TurnCreate, user: User, session: Session):
    conversation = await owned_conversation(session, user["id"], conversation_id, True)
    digest = hashlib.sha256(
        json.dumps(body.model_dump(mode="json"), sort_keys=True).encode()
    ).hexdigest()
    prior = (
        (
            await session.execute(
                text("""SELECT request_hash,response FROM conversation_turns
      WHERE owner_id=:owner AND conversation_id=:id AND idempotency_key=:key"""),
                {"owner": user["id"], "id": str(conversation_id), "key": body.idempotency_key},
            )
        )
        .mappings()
        .first()
    )
    if prior:
        if prior["request_hash"] != digest:
            raise HTTPException(409, "message key reused with different input")
        return prior["response"]
    if conversation["revision"] != body.revision:
        raise HTTPException(409, "conversation changed; reload before sending")
    context = dict(conversation["context"])
    resolved = resolve_turn(body.text, context)
    if len(resolved) > 4000:
        raise HTTPException(422, "conversation context too long; start a new question")
    asset = body.input_asset_id
    if not asset and (context.get("pending_text") or "录音" in resolved):
        asset = context.get("asset_id")
    turn_key = hashlib.sha256(f"{conversation_id}:{body.idempotency_key}".encode()).hexdigest()
    response = await dispatch(
        AssistantRequest(text=resolved, input_asset_id=asset, idempotency_key=f"turn:{turn_key}"),
        user,
        session,
        commit=False,
        report_job_id=context.get("last_assessment_job_id"),
    )
    context = {
        "last_text": resolved,
        "asset_id": str(asset) if asset else None,
        "pending_text": resolved if response["status"] in {"clarify", "blocked"} else "",
        "last_assessment_job_id": response.get("job", {}).get("id")
        if response.get("agent") == "assessment"
        else context.get("last_assessment_job_id"),
        "summary": {
            "turns": body.revision + 1,
            "last_intent": response.get("agent"),
            "last_job_id": response.get("job", {}).get("id"),
        },
    }
    response = {**response, "conversation_id": str(conversation_id), "revision": body.revision + 1}
    await session.execute(
        text("""INSERT INTO conversation_turns
      (id,owner_id,conversation_id,idempotency_key,request_hash,user_text,response)
      VALUES (:turn,:owner,:id,:key,:hash,:message,CAST(:response AS jsonb))"""),
        {
            "turn": str(uuid4()),
            "owner": user["id"],
            "id": str(conversation_id),
            "key": body.idempotency_key,
            "hash": digest,
            "message": body.text,
            "response": json.dumps(response),
        },
    )
    await session.execute(
        text("""UPDATE conversations SET context=CAST(:context AS jsonb),
      revision=revision+1,updated_at=now() WHERE id=:id AND owner_id=:owner"""),
        {"context": json.dumps(context), "id": str(conversation_id), "owner": user["id"]},
    )
    await session.commit()
    return response


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(conversation_id: UUID, user: User, session: Session):
    await owned_conversation(session, user["id"], conversation_id, True)
    await session.execute(
        text("DELETE FROM conversations WHERE id=:id AND owner_id=:owner"),
        {"id": str(conversation_id), "owner": user["id"]},
    )
    await session.commit()
