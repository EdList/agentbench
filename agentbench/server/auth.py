"""Authentication — API key + JWT token support for FastAPI routes."""

from __future__ import annotations

import datetime as _dt
import hashlib
import hmac
import threading
import time

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer

from agentbench.server.config import settings

# ---------------------------------------------------------------------------
# Auth failure rate limiting — in-memory per-principal/IP counter
# ---------------------------------------------------------------------------

_auth_fail_lock = threading.Lock()
_auth_fail_counts: dict[str, list[float]] = {}  # key → list of failure timestamps
_AUTH_FAIL_WINDOW = 300  # 5 minutes
_AUTH_FAIL_MAX = 10  # block after 10 failures in window


def _check_auth_failure_limit(key: str) -> None:
    """Raise 429 if *key* has exceeded the auth-failure limit."""
    now = time.monotonic()
    with _auth_fail_lock:
        timestamps = _auth_fail_counts.get(key)
        if timestamps is None:
            return
        # Prune expired entries
        timestamps[:] = [ts for ts in timestamps if now - ts < _AUTH_FAIL_WINDOW]
        if len(timestamps) >= _AUTH_FAIL_MAX:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many failed authentication attempts. Please try again later.",
            )


def _record_auth_failure(key: str) -> None:
    """Record a failed auth attempt for *key*."""
    now = time.monotonic()
    with _auth_fail_lock:
        timestamps = _auth_fail_counts.setdefault(key, [])
        timestamps[:] = [ts for ts in timestamps if now - ts < _AUTH_FAIL_WINDOW]
        timestamps.append(now)

# ---------------------------------------------------------------------------
# API-key authentication
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _api_key_is_valid(api_key: str | None) -> bool:
    """Return True when the provided API key matches a configured key."""
    if api_key is None:
        return False
    return any(hmac.compare_digest(api_key, configured) for configured in settings.api_keys)


def verify_api_key(api_key: str | None = Security(_api_key_header)) -> str:
    """FastAPI dependency that validates the ``X-API-Key`` header.

    Returns the validated key string on success.  Raises 401 otherwise.
    """
    if api_key and _api_key_is_valid(api_key):
        # Use hashed API key as the subject (identity) — never leak the raw key
        # Prefix to avoid namespace collision with JWT sub claims
        return f"apikey:{hashlib.sha256(api_key.encode()).hexdigest()}"
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
            detail="Invalid or expired token.",
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
    Includes in-memory rate limiting for failed auth attempts.
    """
    # Build a client key from available credentials for rate limiting
    _rate_key: str = "anonymous"
    if api_key:
        _rate_key = f"key:{hashlib.sha256(api_key.encode()).hexdigest()[:16]}"
    elif credentials:
        _rate_key = f"jwt:{hashlib.sha256(credentials.credentials.encode()).hexdigest()[:16]}"
    _check_auth_failure_limit(_rate_key)

    # Try API key first
    if _api_key_is_valid(api_key):
        return _principal_for_api_key(api_key)

    # Try JWT bearer
    if credentials is not None:
        try:
            payload = decode_access_token(credentials.credentials)
            sub = payload.get("sub")
            if sub is None:
                _record_auth_failure(_rate_key)
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Token is missing required 'sub' claim.",
                )
            return str(sub)
        except jwt.PyJWTError:
            pass

    _record_auth_failure(_rate_key)
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Provide a valid X-API-Key header or Bearer token.",
    )
