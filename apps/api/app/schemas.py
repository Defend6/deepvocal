from typing import Literal

from pydantic import BaseModel, Field


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
