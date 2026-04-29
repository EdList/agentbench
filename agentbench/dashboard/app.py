"""Dashboard FastAPI app — serves workflow health data and regression timeline.

Standalone FastAPI application that reads local ``.agentbench/workflows/``
and ``.agentbench/reports/`` directories.  No database — pure file-based.

API endpoints:
    GET /api/stats          — overview stats
    GET /api/workflows      — list all workflows with latest status
    GET /api/workflows/{name} — single workflow detail
    GET /api/reports        — list all reports (paginated)
    GET /api/timeline       — regression timeline for charts
    GET /                   — dashboard HTML
"""

from __future__ import annotations

import hmac
import json
import logging
import re
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse

from agentbench.recorder.workflow import Workflow

_DASHBOARD_DIR = Path(__file__).resolve().parent / "templates"
_WORKFLOWS_DIR = Path(".agentbench/workflows")
_REPORTS_DIR = Path(".agentbench/reports")
_LOGGER = logging.getLogger(__name__)

# Valid workflow names: alphanumeric, hyphens, underscores only
_WORKFLOW_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def create_dashboard_app(
    base_dir: Path | None = None,
    auth_token: str | None = None,
) -> FastAPI:
    """Create the dashboard FastAPI application.

    Args:
        base_dir: Root directory containing ``.agentbench/``.
            Defaults to cwd.
        auth_token: Optional bearer token for API authentication.
            If None, a warning is logged and all requests are allowed.
    """
    root = base_dir or Path.cwd()
    rp_dir = root / _REPORTS_DIR

    if auth_token is None:
        _LOGGER.warning(
            "Dashboard running WITHOUT authentication. "
            "Set --token to enable bearer token auth."
        )

    app = FastAPI(
        title="AgentBench Dashboard",
        version="0.1.0",
        description="Workflow health and regression tracking dashboard.",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # -- Bearer token auth middleware for /api/* routes ----------------------

    @app.middleware("http")
    async def auth_middleware(request: Request, call_next: Any) -> Response:
        if request.url.path.startswith("/api/"):
            if auth_token is not None:
                auth_header = request.headers.get("Authorization", "")
                expected = f"Bearer {auth_token}"
                if not hmac.compare_digest(auth_header, expected):
                    return Response(
                        content='{"detail":"Unauthorized"}',
                        status_code=401,
                        media_type="application/json",
                    )
        return await call_next(request)

    # -- Helpers -------------------------------------------------------------

    def _load_reports_for_workflow(name: str) -> list[dict[str, Any]]:
        """Load all reports for a given workflow, sorted by time."""
        if not rp_dir.exists():
            return []
        reports: list[dict[str, Any]] = []
        for path in sorted(rp_dir.glob(f"{name}-replay-*.json")):
            try:
                reports.append(json.loads(path.read_text()))
            except (json.JSONDecodeError, KeyError):
                continue
        return reports

    def _scan_all_reports(
        limit: int | None = None,
        offset: int = 0,
    ) -> tuple[list[dict[str, Any]], int]:
        """Load reports from the reports directory with filesystem-level pagination.

        Returns (page_of_reports, total_count).
        """
        if not rp_dir.exists():
            return [], 0

        # Gather all JSON files sorted by mtime (newest first) without reading contents
        all_files = sorted(
            rp_dir.glob("*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        total = len(all_files)

        # Apply filesystem-level pagination: only load the slice needed
        if limit is not None:
            page_files = all_files[offset : offset + limit]
        else:
            page_files = all_files

        reports: list[dict[str, Any]] = []
        for path in page_files:
            try:
                data = json.loads(path.read_text())
                data["_file"] = path.name
                reports.append(data)
            except (json.JSONDecodeError, KeyError):
                continue
        return reports, total

    # -- API: Stats ----------------------------------------------------------

    @app.get("/api/stats", tags=["dashboard"])
    def get_stats() -> dict[str, Any]:
        """Dashboard overview statistics."""
        workflows = Workflow.list_workflows(base_dir=root)
        all_reports, _ = _scan_all_reports()

        total_workflows = len(workflows)
        total_reports = len(all_reports)

        # Latest scores per workflow
        latest_scores: dict[str, float] = {}
        for r in all_reports:
            wf_name = r.get("replay_of", "")
            if wf_name:
                latest_scores[wf_name] = r.get("overall_score", 0.0)

        avg_score = (
            sum(latest_scores.values()) / len(latest_scores)
            if latest_scores
            else 1.0
        )

        # Regressions (reports where passed=False)
        regressions = [r for r in all_reports if not r.get("passed", True)]

        return {
            "total_workflows": total_workflows,
            "total_reports": total_reports,
            "average_score": round(avg_score, 3),
            "regression_count": len(regressions),
            "latest_scores": latest_scores,
            "healthy": len(regressions) == 0,
        }

    # -- API: Workflows ------------------------------------------------------

    @app.get("/api/workflows", tags=["dashboard"])
    def list_workflows() -> list[dict[str, Any]]:
        """List all workflows with their latest replay status."""
        listed = Workflow.list_workflows(base_dir=root)
        result: list[dict[str, Any]] = []

        for name, created_at in listed:
            try:
                wf = Workflow.load(name, base_dir=root)
                # Find latest report for this workflow
                reports = _load_reports_for_workflow(name)
                latest = reports[-1] if reports else None

                result.append({
                    "name": name,
                    "created_at": created_at,
                    "turn_count": wf.turn_count,
                    "tool_call_count": wf.total_tool_calls,
                    "latest_score": latest.get("overall_score") if latest else None,
                    "latest_passed": latest.get("passed") if latest else None,
                    "report_count": len(reports),
                })
            except Exception:  # noqa: BLE001
                result.append({
                    "name": name,
                    "created_at": created_at,
                    "error": "Failed to load",
                })

        return result

    @app.get("/api/workflows/{name}", tags=["dashboard"])
    def get_workflow(name: str) -> dict[str, Any]:
        """Get detailed info for a single workflow."""
        if not _WORKFLOW_NAME_RE.match(name):
            raise HTTPException(
                status_code=400,
                detail=(
                    "Workflow name must contain only alphanumeric "
                    "characters, hyphens, and underscores."
                ),
            )
        try:
            wf = Workflow.load(name, base_dir=root)
        except FileNotFoundError:
            raise HTTPException(status_code=404, detail="Workflow not found") from None

        reports = _load_reports_for_workflow(name)

        return {
            "name": wf.name,
            "agent_url": wf.agent_url,
            "agent_format": wf.agent_format,
            "created_at": wf.created_at,
            "turn_count": wf.turn_count,
            "total_tool_calls": wf.total_tool_calls,
            "tool_call_sequence": wf.tool_call_sequence,
            "user_messages": wf.user_messages,
            "turns": [
                {
                    "index": t.index,
                    "user_message": t.user_message,
                    "agent_response": t.agent_response,
                    "tool_calls": [
                        {"name": tc.name, "arguments": tc.arguments}
                        for tc in t.tool_calls
                    ],
                    "latency_ms": t.latency_ms,
                }
                for t in wf.turns
            ],
            "reports": reports,
        }

    # -- API: Reports --------------------------------------------------------

    @app.get("/api/reports", tags=["dashboard"])
    def list_reports(
        limit: int = 50, offset: int = 0,
    ) -> dict[str, Any]:
        """List all replay reports (paginated, newest first)."""
        page_reports, total = _scan_all_reports(limit=limit, offset=offset)

        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "reports": page_reports,
        }

    # -- API: Timeline -------------------------------------------------------

    @app.get("/api/timeline", tags=["dashboard"])
    def get_timeline() -> list[dict[str, Any]]:
        """Regression timeline — scores over time for charts."""
        all_reports, _ = _scan_all_reports()

        timeline: list[dict[str, Any]] = []
        for r in all_reports:
            timeline.append({
                "workflow": r.get("replay_of", ""),
                "score": r.get("overall_score", 0.0),
                "passed": r.get("passed", False),
                "timestamp": r.get("created_at", ""),
                "turn_count": len(r.get("turn_results", [])),
                "threshold": r.get("threshold", 0.8),
            })

        return timeline

    # -- UI ------------------------------------------------------------------

    @app.get("/", tags=["ui"])
    def serve_dashboard() -> FileResponse:
        index = _DASHBOARD_DIR / "index.html"
        if not index.exists():
            return HTMLResponse(
                "<h1>Dashboard template not found</h1>"
                "<p>Expected at agentbench/dashboard/templates/index.html</p>",
                status_code=404,
            )
        return FileResponse(index, media_type="text/html")

    return app
