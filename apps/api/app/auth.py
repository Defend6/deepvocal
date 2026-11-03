from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, Any

import httpx
import jwt
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
        headers={"WWW-Authenticate": "Bearer"},
    )


async def _jwks(settings: Settings) -> dict[str, Any]:
    if not settings.oidc_jwks_url:
        raise _unauthorized("OIDC JWKS URL is not configured")
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get(settings.oidc_jwks_url)
        response.raise_for_status()
        return response.json()


async def authenticate(
    request: Request, settings: Annotated[Settings, Depends(get_settings)]
) -> Principal:
    authorization = request.headers.get("Authorization", "")
    if settings.auth_mode == "local":
        # This header is deliberately explicit and only accepted in local mode.
        identity = request.headers.get("X-Debug-Identity")
        if identity:
            try:
                issuer, subject = identity.split("|", 1)
            except ValueError as exc:
                raise _unauthorized("X-Debug-Identity must be issuer|subject") from exc
            if not issuer or not subject:
                raise _unauthorized("debug identity is incomplete")
            return Principal(issuer=issuer, subject=subject, claims={"iss": issuer, "sub": subject})
    if not authorization.startswith("Bearer "):
        raise _unauthorized("Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise _unauthorized("Bearer token required")

    if settings.auth_mode != "oidc":
        raise _unauthorized("unsupported authentication mode")
    if not settings.oidc_issuer or not settings.oidc_audience:
        raise _unauthorized("OIDC issuer and audience are not configured")
    try:
        header = jwt.get_unverified_header(token)
        keys = await _jwks(settings)
        key_data = next(
            (item for item in keys.get("keys", []) if item.get("kid") == header.get("kid")), None
        )
        if not key_data:
            raise _unauthorized("signing key not found")
        key = jwt.algorithms.RSAAlgorithm.from_jwk(json.dumps(key_data))
        claims = jwt.decode(
            token,
            key=key,
            algorithms=["RS256", "RS384", "RS512"],
            audience=settings.oidc_audience,
            issuer=settings.oidc_issuer,
            leeway=settings.auth_clock_skew_seconds,
            options={"require": ["exp", "iat", "iss", "sub"]},
        )
    except HTTPException:
        raise
    except (jwt.PyJWTError, ValueError, httpx.HTTPError) as exc:
        raise _unauthorized("invalid or expired bearer token") from exc
    return Principal(issuer=claims["iss"], subject=claims["sub"], claims=claims)
