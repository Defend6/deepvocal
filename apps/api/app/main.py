from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.router import RuleRouter
from app.auth import Principal, authenticate
from app.config import get_settings
from app.database import get_session
from app.knowledge.corpus import CorpusStatusService
from app.schemas import (
    AuthUserResponse,
    CapabilityResponse,
    CorpusStatusResponse,
    HealthResponse,
    RouteRequest,
    RouteResponse,
    UploadCompleteResponse,
    UploadCreateRequest,
    UploadCreateResponse,
)
from app.storage.s3 import S3Storage
from app.uploads import UploadError, UploadService

settings = get_settings()
router = RuleRouter()
app = FastAPI(title=settings.app_name, version="0.1.0")


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", mode=settings.deepvocal_mode, singing_enabled=settings.singing_enabled
    )


async def current_user(
    principal: Annotated[Principal, Depends(authenticate)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    result = await session.execute(
        text("""
            INSERT INTO users (id, issuer, subject)
            VALUES (gen_random_uuid(), :issuer, :subject)
            ON CONFLICT (issuer, subject) DO UPDATE SET issuer = EXCLUDED.issuer
            RETURNING id, issuer, subject, status
        """),
        {"issuer": principal.issuer, "subject": principal.subject},
    )
    user = result.mappings().one()
    if user["status"] != "active":
        await session.rollback()
        raise HTTPException(status_code=403, detail="user account is not active")
    await session.commit()
    return {"id": user["id"], "issuer": user["issuer"], "subject": user["subject"]}


@app.get("/v1/me", response_model=AuthUserResponse)
async def me(user: Annotated[dict[str, str], Depends(current_user)]) -> AuthUserResponse:
    return AuthUserResponse(**user)


@app.post("/v1/uploads", response_model=UploadCreateResponse, status_code=201)
async def create_upload(
    request: UploadCreateRequest,
    user: Annotated[dict[str, str], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UploadCreateResponse:
    try:
        storage = S3Storage.from_settings(settings)
        storage.ensure_bucket()
        payload = await UploadService(session, storage, settings).create(user["id"], request)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    except Exception as exc:
        await session.rollback()
        raise HTTPException(status_code=503, detail="upload storage is unavailable") from exc
    return UploadCreateResponse(**payload)


@app.post("/v1/uploads/{asset_id}/complete", response_model=UploadCompleteResponse)
async def complete_upload(
    asset_id: str,
    user: Annotated[dict[str, str], Depends(current_user)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> UploadCompleteResponse:
    try:
        payload = await UploadService(
            session, S3Storage.from_settings(settings), settings
        ).complete(user["id"], asset_id)
    except UploadError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
    return UploadCompleteResponse(**payload)


@app.post("/v1/router/decide", response_model=RouteResponse)
def decide_route(request: RouteRequest) -> RouteResponse:
    decision = router.route(request.text, request.explicit_action)
    return RouteResponse(
        agent=decision.agent,
        reason=decision.reason,
        confidence=decision.confidence,
        tool_allowlist=list(decision.tool_allowlist),
        requires_confirmation=decision.requires_confirmation,
    )


@app.get("/v1/agents/{agent}/capabilities", response_model=CapabilityResponse)
def capabilities(agent: str) -> CapabilityResponse:
    if agent == "singing":
        return CapabilityResponse(
            agent=agent,
            enabled=settings.singing_enabled,
            status="disabled" if not settings.singing_enabled else "available",
            reason="external singing service is not configured"
            if not settings.singing_enabled
            else None,
        )
    return CapabilityResponse(
        agent=agent,
        enabled=True,
        status="mock" if settings.deepvocal_mode == "mock" else "available",
    )


@app.get("/v1/knowledge/corpus/status", response_model=CorpusStatusResponse)
def corpus_status() -> CorpusStatusResponse:
    return CorpusStatusResponse(**CorpusStatusService(settings.corpus_root).status())
