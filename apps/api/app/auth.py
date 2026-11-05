from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
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
    if settings.auth_mode == "oidc":
        import jwt

        header = request.headers.get("Authorization", "")
        if not header.startswith("Bearer ") or len(header) > 16384:
            raise _unauthorized("valid access token required")
        token = header[7:]
        try:
            key = jwks_client(settings.oidc_jwks_url).get_signing_key_from_jwt(token).key
            claims = jwt.decode(
                token,
                key,
                algorithms=["RS256", "ES256"],
                audience=settings.oidc_audience,
                issuer=settings.oidc_issuer,
                leeway=settings.auth_clock_skew_seconds,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
            if not isinstance(claims["sub"], str) or not claims["sub"].strip():
                raise ValueError("invalid subject")
            if claims.get("typ") in {"ID", "Refresh"}:
                raise ValueError("access token required")
            return Principal(settings.oidc_issuer, claims["sub"], claims)
        except (jwt.PyJWTError, ValueError) as exc:
            raise _unauthorized("access token invalid or expired") from exc
    if settings.auth_mode != "local" or settings.deepvocal_mode.lower() not in {
        "mock",
        "development",
        "test",
    }:
        raise _unauthorized("only local test identity is implemented")
    identity = request.headers.get("X-Debug-Identity")
    if not identity or len(identity) > 512:
        raise _unauthorized("X-Debug-Identity required in local test mode")
    try:
        issuer, subject = identity.split("|", 1)
    except ValueError as exc:
        raise _unauthorized("X-Debug-Identity must be issuer|subject") from exc
    if not issuer.strip() or not subject.strip():
        raise _unauthorized("debug identity is incomplete")
    issuer, subject = issuer.strip(), subject.strip()
    return Principal(issuer=issuer, subject=subject, claims={"iss": issuer, "sub": subject})


@lru_cache(maxsize=8)
def jwks_client(url):
    from jwt import PyJWKClient

    return PyJWKClient(url, cache_keys=False, cache_jwk_set=True, lifespan=300, timeout=5)
