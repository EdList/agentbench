"""FastAPI application — the AgentBench Cloud API."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from agentbench.server.config import settings
from agentbench.server.routes import runs, scans, trajectories
from agentbench.server.schemas import HealthResponse

__all__ = ["app", "create_app"]

_SITE_DIR = Path(__file__).resolve().parent.parent.parent / "site"


def create_app() -> FastAPI:
    """Application factory — allows custom config for testing."""
    application = FastAPI(
        title="AgentBench API",
        version="0.1.0",
        description="Cloud API for running and managing AgentBench test suites.",
        debug=settings.debug,
    )

    # CORS
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Health check — not versioned
    @application.get(
        "/health", response_model=HealthResponse, tags=["health"]
    )
    def health_check() -> HealthResponse:
        return HealthResponse()

    # Web UI — serve app.html at /
    @application.get("/", tags=["ui"])
    def serve_ui() -> FileResponse:
        return FileResponse(
            _SITE_DIR / "app.html", media_type="text/html"
        )

    # Versioned API routes
    from fastapi import APIRouter

    v1 = APIRouter(prefix="/api/v1")
    v1.include_router(runs.router)
    v1.include_router(trajectories.router)
    v1.include_router(scans.router)
    application.include_router(v1)

    return application


app = create_app()
