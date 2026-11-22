"""Optional JWT bearer auth. Disabled unless COI_JWT_ENABLED=true."""

from typing import Annotated, Any

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from kodex.config import Settings, get_settings

_bearer = HTTPBearer(
    auto_error=False,
    bearerFormat="JWT",
    description="Enforced only when the server runs with COI_JWT_ENABLED=true.",
)


def require_auth(
    settings: Annotated[Settings, Depends(get_settings)],
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> dict[str, Any] | None:
    """Return the token claims, or None when auth is disabled."""
    if not settings.jwt_enabled:
        return None
    if creds is None:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Missing bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    assert settings.jwt_secret is not None  # enforced by Settings validator
    try:
        return jwt.decode(
            creds.credentials,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
        )
    except jwt.InvalidTokenError as exc:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            f"Invalid token: {exc}",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
