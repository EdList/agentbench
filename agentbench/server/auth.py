"""Authentication — API key + JWT token support for FastAPI routes."""

from __future__ import annotations

import datetime as _dt
import hashlib

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from agentbench.server.config import settings

# ---------------------------------------------------------------------------
# API-key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency that validates the ``X-API-Key`` header.

    Returns the validated key string on success.  Raises 401 otherwise.
    """
    if api_key is not None and api_key in settings.api_keys:
        return api_key
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing API key. Provide X-API-Key header.",
    )


# ---------------------------------------------------------------------------
# JWT session tokens
# ---------------------------------------------------------------------------

_bearer_scheme = HTTPBearer(auto_error=False)


def create_access_token(
    subject: str,
    expires_delta: _dt.timedelta | None = None,
) -> str:
    """Create a signed JWT for *subject* (typically a user id or API key)."""
    now = _dt.datetime.now(_dt.UTC)
    expire = now + (expires_delta or _dt.timedelta(hours=24))
    payload = {"sub": subject, "iat": now, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_access_token(token: str) -> dict:
    """Decode and validate a JWT.  Raises ``jwt.PyJWTError`` on failure."""
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def require_token(
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> dict:
    """FastAPI dependency that requires a valid Bearer JWT.

    Returns the decoded token payload on success.
    """
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authentication token.",
        )
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid token: {exc}",
        ) from exc
    return payload


# ---------------------------------------------------------------------------
# Combined: accept *either* API key *or* JWT bearer token
# ---------------------------------------------------------------------------


def _principal_for_api_key(api_key: str) -> str:
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    return f"apikey:{digest}"


def require_auth(
    api_key: str | None = Security(_api_key_header),
    credentials: HTTPAuthorizationCredentials | None = Security(_bearer_scheme),
) -> str:
    """FastAPI dependency that accepts either an API key or a Bearer JWT.

    Returns a string identifying the authenticated principal.
    """
    # Try API key first
    if api_key is not None and api_key in settings.api_keys:
        return _principal_for_api_key(api_key)

    # Try JWT bearer
    if credentials is not None:
        try:
            payload = decode_access_token(credentials.credentials)
            return payload.get("sub", "unknown")
        except jwt.PyJWTError:
            pass

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide a valid X-API-Key header or Bearer token.",
    )
