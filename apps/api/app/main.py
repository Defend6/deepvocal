import asyncio
import logging
import time
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from sqlalchemy import text

from app.agents.router import RuleRouter
from app.artifacts import router as artifacts_router
from app.assistant import router as assistant_router
from app.config import get_settings
from app.identity import Session, User
from app.jobs import router as jobs_router
from app.knowledge.corpus import CorpusNotFoundError, CorpusStatusService
from app.memory import router as memory_router
from app.operations.diagnostics import router as operations_router
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
from app.skills import router as skills_router
from app.storage.s3 import S3Storage
from app.uploads import UploadError, UploadService

settings = get_settings()
router = RuleRouter()
app = FastAPI(title=settings.app_name, version="0.1.0")
app.include_router(jobs_router)
app.include_router(assistant_router)
app.include_router(artifacts_router)
app.include_router(memory_router)
app.include_router(skills_router)
app.include_router(operations_router)
logger = logging.getLogger("deepvocal.requests")


@app.middleware("http")
async def trace_request(request: Request, call_next):
    # Generate locally, never log queries, request bodies, identity headers or presigned URLs.
    request_id = str(uuid4())
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info(
        "request id=%s method=%s status=%s duration_ms=%d",
        request_id,
        request.method,
        response.status_code,
        round((time.monotonic() - started) * 1000),
    )
    return response


@app.get("/ready")
async def ready():
    from redis.asyncio import Redis

    from app.database import _session_factory

    async def check():
        async with _session_factory()() as session:
            version = await session.scalar(
                text("SELECT 1 FROM alembic_version WHERE version_num='0007_worker_presence'")
            )
            if version != 1:
                raise RuntimeError("migration required")
        client = Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=2)
        try:
            await client.ping()
        finally:
            await client.aclose()

    try:
        await asyncio.wait_for(check(), 5)
    except Exception as exc:
        raise HTTPException(503, "database or queue not ready") from exc
    return {
        "status": "ready",
        "scope": "database and Redis; model and audio-upload verification separate",
    }


@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    return HealthResponse(status="ok", mode=settings.deepvocal_mode, singing_enabled=False)


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
    if agent not in {"knowledge", "assessment", "planning", "singing"}:
        raise HTTPException(404, "unknown agent")
    if agent == "singing":
        return CapabilityResponse(
            agent=agent,
            enabled=False,
            status="disabled",
            reason="external singing service protocol is not configured",
        )
    if agent == "knowledge":
        from app.knowledge.budget import BudgetBlocked, price_quote

        reason = "MODEL_API_KEY_REQUIRED"
        enabled = False
        if settings.model_api_key:
            try:
                for model in (
                    settings.chat_model,
                    settings.embedding_model,
                    settings.reranker_model,
                ):
                    price_quote(settings, {"model": model})
            except BudgetBlocked as exc:
                reason = str(exc)
            else:
                enabled = True
                reason = "research corpus and provider account must pass validation"
        return CapabilityResponse(
            agent=agent,
            enabled=enabled,
            status="available" if enabled else "disabled",
            reason=reason,
        )
    if agent == "assessment":
        return CapabilityResponse(
            agent=agent,
            enabled=False,
            status="disabled",
            reason="offline measurement available; presign-only uploads remain pending",
        )
    return CapabilityResponse(
        agent=agent,
        enabled=True,
        status="available",
        reason="engineering practice templates; not expert-reviewed personalized teaching",
    )


@app.get("/v1/knowledge/corpus/status", response_model=CorpusStatusResponse)
def corpus_status() -> CorpusStatusResponse:
    try:
        return CorpusStatusResponse(**CorpusStatusService(settings.corpus_root).status())
    except CorpusNotFoundError as exc:
        raise HTTPException(503, "corpus metadata unavailable") from exc


@app.get("/v1/model/usage", tags=["models"])
async def model_usage(user: User):
    from app.knowledge.budget import ModelBudget

    return await ModelBudget(settings).own_usage(user["id"])
