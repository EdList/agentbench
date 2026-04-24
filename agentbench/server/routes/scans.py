"""Scan-related API routes: submit a scan, retrieve results, list recent scans."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, status

from agentbench.scanner.analyzer import BehaviorAnalyzer
from agentbench.scanner.prober import ALL_CATEGORIES, AgentProber
from agentbench.scanner.scorer import ScoringEngine
from agentbench.server.schemas import (
    DomainScoreResponse,
    ScanRequest,
    ScanResponse,
    ScanSummaryResponse,
)

router = APIRouter(prefix="/scans", tags=["scans"])

# ---------------------------------------------------------------------------
# In-memory scan storage (good enough for the lean MVP)
# ---------------------------------------------------------------------------

_scan_store: dict[str, dict] = {}


def _run_scan(agent_url: str, categories: list[str] | None) -> ScanResponse:
    """Execute the full prober → analyzer → scorer pipeline synchronously."""
    import httpx

    cats = categories if categories else list(ALL_CATEGORIES)

    # Wrap the agent URL in a simple callable for the prober
    def _agent_fn(prompt: str) -> str:
        """Send *prompt* to the agent via HTTP and return the response text."""
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(
                agent_url,
                json={"prompt": prompt},
            )
            resp.raise_for_status()
            data = resp.json()
            # Support common response shapes
            if isinstance(data, dict):
                return data.get("response", data.get("output", str(data)))
            return str(data)

    # 1. Probe
    prober = AgentProber(agent_fn=_agent_fn, categories=cats)
    session = prober.probe_all()

    # 2. Analyze
    analyzer = BehaviorAnalyzer()
    behaviors = analyzer.analyze(session)

    # 3. Score
    engine = ScoringEngine()
    report = engine.score(behaviors)

    # 4. Convert to response
    return ScanResponse(
        overall_score=report.overall_score,
        overall_grade=report.overall_grade,
        domain_scores=[
            DomainScoreResponse(
                name=ds.name,
                score=ds.score,
                grade=ds.grade,
                findings=ds.findings,
                recommendations=ds.recommendations,
            )
            for ds in report.domain_scores
        ],
        summary=report.summary,
        behaviors_tested=report.behaviors_tested,
        behaviors_passed=report.behaviors_passed,
        behaviors_failed=report.behaviors_failed,
        critical_issues=report.critical_issues,
        timestamp=report.timestamp.isoformat(),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ScanResponse,
    status_code=status.HTTP_200_OK,
)
def submit_scan(body: ScanRequest) -> ScanResponse:
    """Scan an agent and return the full report immediately.

    The scan runs synchronously (38 probes, no LLM) and typically completes
    in under a minute.
    """
    scan_id = str(uuid.uuid4())
    try:
        report = _run_scan(body.agent_url, body.categories)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to scan agent at {body.agent_url}: {exc}",
        ) from exc

    # Store for later retrieval
    _scan_store[scan_id] = {
        "scan_id": scan_id,
        "agent_url": body.agent_url,
        "report": report,
        "timestamp": datetime.now(UTC).isoformat(),
    }

    return report


@router.get(
    "/{scan_id}",
    response_model=ScanResponse,
)
def get_scan(scan_id: str) -> ScanResponse:
    """Retrieve a previously-run scan report by ID."""
    entry = _scan_store.get(scan_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found.")
    return entry["report"]


@router.get(
    "",
    response_model=list[ScanSummaryResponse],
)
def list_scans(
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
) -> list[ScanSummaryResponse]:
    """List recent scans, ordered by time descending."""
    # Sort by timestamp descending
    sorted_entries = sorted(
        _scan_store.values(),
        key=lambda e: e.get("timestamp", ""),
        reverse=True,
    )
    page = sorted_entries[offset : offset + limit]
    return [
        ScanSummaryResponse(
            scan_id=e["scan_id"],
            agent_url=e["agent_url"],
            overall_score=e["report"].overall_score,
            overall_grade=e["report"].overall_grade,
            timestamp=e["timestamp"],
        )
        for e in page
    ]
