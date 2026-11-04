from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

AgentName = Literal["knowledge", "assessment", "planning", "singing", "clarify"]


class HealthResponse(BaseModel):
    status: Literal["ok"]
    mode: str
    singing_enabled: bool


class RouteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=4000)
    explicit_action: str | None = Field(default=None, max_length=80)


class RouteResponse(BaseModel):
    agent: AgentName
    reason: str
    confidence: float = Field(ge=0, le=1)
    tool_allowlist: list[str]
    requires_confirmation: bool = False


class CapabilityResponse(BaseModel):
    agent: str
    enabled: bool
    status: Literal["available", "disabled", "mock"]
    reason: str | None = None


class CorpusStatusResponse(BaseModel):
    corpus_version: str
    status: str
    document_count: int
    chunk_count: int
    entity_count: int
    relation_candidate_count: int
    source_count: int
    review_status: str
    production_enabled: bool
    production_blockers: list[str]
    embeddings_computed: bool
    neo4j_loaded: bool
    milvus_loaded: bool
    expert_review_completed: bool
    user_practice_record_count: int


class UploadCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(pattern=r"^audio/[A-Za-z0-9.+-]+$")
    size_bytes: int = Field(gt=0, le=104857600)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    purpose: Literal["assessment", "voice_sample", "melody", "reference", "generated_reference"]


class UploadCreateResponse(BaseModel):
    asset_id: str
    object_key: str
    upload_url: str
    required_headers: dict[str, str]
    expires_in: int


class AuthUserResponse(BaseModel):
    id: str
    issuer: str
    subject: str
