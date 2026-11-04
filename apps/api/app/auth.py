from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status

from app.config import Settings, get_settings


@dataclass(frozen=True)
class Principal:
    issuer: str
    subject: str
    claims: dict[str, Any]


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
    )


def authenticate(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    if settings.auth_mode != "local" or settings.deepvocal_mode.lower() not in {
        "mock",
        "development",
        "test",
    }:
        raise _unauthorized("only local test identity is implemented")
    identity = request.headers.get("X-Debug-Identity")
    if not identity:
        raise _unauthorized("X-Debug-Identity required in local test mode")
    try:
        issuer, subject = identity.split("|", 1)
    except ValueError as exc:
        raise _unauthorized("X-Debug-Identity must be issuer|subject") from exc
    if not issuer.strip() or not subject.strip():
        raise _unauthorized("debug identity is incomplete")
    issuer, subject = issuer.strip(), subject.strip()
    return Principal(issuer=issuer, subject=subject, claims={"iss": issuer, "sub": subject})
