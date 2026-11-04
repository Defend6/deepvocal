from fastapi import FastAPI, HTTPException

from app.agents.router import RuleRouter
from app.config import get_settings
from app.identity import Session, User
from app.jobs import router as jobs_router
from app.knowledge.corpus import CorpusStatusService
from app.schemas import (
    AuthUserResponse,
    CapabilityResponse,
    CorpusStatusResponse,
    HealthResponse,
    RouteRequest,
    RouteResponse,
    UploadCreateRequest,
    UploadCreateResponse,
)
from app.storage.s3 import S3Storage
from app.uploads import UploadError, UploadService

settings = get_settings()
router = RuleRouter()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(jobs_router)


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(
        status="ok", mode=settings.deepvocal_mode, singing_enabled=settings.singing_enabled
    )


@app.get("/v1/me", response_model=AuthUserResponse)
async def me(user: User) -> AuthUserResponse:
    return AuthUserResponse(**user)


@app.post("/v1/uploads", response_model=UploadCreateResponse, status_code=201)
async def create_upload(
    request: UploadCreateRequest,
    user: User,
    session: Session,
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
