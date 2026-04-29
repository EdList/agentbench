"""FastAPI application — the AgentBench Cloud API."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from agentbench.server.config import settings
from agentbench.server.models import cleanup_old_records, create_tables
from agentbench.server.routes import agents, policies, projects, runs, scans, trajectories
from agentbench.server.schemas import HealthResponse

__all__ = ["app", "create_app"]

_SITE_DIR = Path(__file__).resolve().parent.parent.parent / "site"

# Stale-job reaper interval (seconds)
_REAPER_INTERVAL = 120


def create_app() -> FastAPI:
    """Application factory — allows custom config for testing."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio

        create_tables()
        cleanup_old_records()
        scans.fail_stale_scan_jobs()

        # Periodic stale-job reaper
        async def _reaper_loop():
            while True:
                await asyncio.sleep(_REAPER_INTERVAL)
                try:
                    cleanup_old_records()
                    scans.fail_stale_scan_jobs()
                except Exception:
                    import logging as _reaper_log
                    _reaper_log.getLogger(__name__).warning("Reaper error", exc_info=True)

        reaper_task = asyncio.create_task(_reaper_loop())
        try:
            yield
        finally:
            reaper_task.cancel()
            try:
                await reaper_task
            except asyncio.CancelledError:
                pass

    application = FastAPI(
        title="AgentBench API",
        version="0.1.0",
        description="Cloud API for running and managing AgentBench test suites.",
        debug=settings.debug,
        lifespan=lifespan,
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=settings.cors_allow_credentials,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Content-Security-Policy headers — strict policy for all responses
    _csp_value = (
        "default-src 'self'; "
        "script-src 'self'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )

    @application.middleware("http")
    async def _add_csp_headers(request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _csp_value
        return response

    # Global request body size limit (1 MB default).
    # We validate the *actual* body size, not just the Content-Length header,
    # so chunked transfer encoding or a missing header cannot bypass the check.
    _max_body = int(os.getenv("AGENTBENCH_MAX_BODY_BYTES", "1048576"))

    @application.middleware("http")
    async def _limit_body_size(request, call_next):
        # Fast-path: reject obviously oversized Content-Length headers
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                cl_value = int(content_length)
            except ValueError:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
            if cl_value > _max_body:
                from fastapi.responses import JSONResponse
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large."},
                )
        # For methods that typically carry a body, consume and size-check the
        # actual payload in chunks.  This catches chunked encoding and missing
        # headers, and aborts early without fully buffering oversized bodies.
        if request.method in ("POST", "PUT", "PATCH"):
            from fastapi.responses import JSONResponse

            body_parts: list[bytes] = []
            total = 0
            async for chunk in request.stream():
                total += len(chunk)
                if total > _max_body:
                    return JSONResponse(
                        status_code=413,
                        content={"detail": "Request body too large."},
                    )
                body_parts.append(chunk)
            # Store the reassembled body so downstream handlers can read it
            request._body = b"".join(body_parts)
        return await call_next(request)

    # Health check — not versioned
    @application.get("/health", response_model=HealthResponse, tags=["health"])
    def health_check() -> HealthResponse:
        return HealthResponse()

    # Web UI — serve app.html at /
    @application.get("/", tags=["ui"])
    def serve_ui() -> FileResponse:
        return FileResponse(_SITE_DIR / "app.html", media_type="text/html")

    # Versioned API routes
    from fastapi import APIRouter

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(projects.router)
    v1.include_router(agents.router)
    v1.include_router(policies.router)
    v1.include_router(runs.router)
    v1.include_router(trajectories.router)
    v1.include_router(scans.router)
    application.include_router(v1)

    return application


app = create_app()
