"""Run-related API routes: submit, list, retrieve test runs."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Run, get_db
from agentbench.server.schemas import (
    ErrorResponse,
    RunCreateRequest,
    RunListResponse,
    RunResponse,
    RunResultEntry,
)

router = APIRouter(prefix="/runs", tags=["runs"])


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}},
)
def submit_run(
    body: RunCreateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Submit a new test run.

    Accepts either inline ``test_suite_code`` or a ``test_suite_path`` on the
    server.  The run is created in ``pending`` status — a background worker
    (not yet implemented) would pick it up and execute the suite.
    """
    if not body.test_suite_code and not body.test_suite_path:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Provide either test_suite_code or test_suite_path.",
        )

    run_id = str(uuid.uuid4())
    run = Run(
        id=run_id,
        status="pending",
        total_tests=0,
        passed=0,
        failed=0,
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    return RunResponse(
        id=run.id,
        status=run.status,
        total_tests=run.total_tests,
        passed=run.passed,
        failed=run.failed,
        duration_ms=run.duration_ms,
        created_at=run.created_at,
        completed_at=run.completed_at,
        results=[],
    )


@router.get(
    "",
    response_model=RunListResponse,
    responses={401: {"model": ErrorResponse}},
)
def list_runs(
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> RunListResponse:
    """List recent runs, ordered by creation time descending."""
    total = db.query(Run).count()
    rows = (
        db.query(Run)
        .order_by(Run.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    def _to_response(r: Run) -> RunResponse:
        results = None
        if r.results:
            results = [
                RunResultEntry(
                    test_name=rr.test_name,
                    passed=rr.passed,
                    failed=rr.failed,
                    duration_ms=rr.duration_ms,
                    error=rr.error,
                    assertions=json.loads(rr.assertions_json) if rr.assertions_json else None,
                )
                for rr in r.results
            ]
        return RunResponse(
            id=r.id,
            status=r.status,
            total_tests=r.total_tests,
            passed=r.passed,
            failed=r.failed,
            duration_ms=r.duration_ms,
            created_at=r.created_at,
            completed_at=r.completed_at,
            results=results,
        )

    return RunListResponse(runs=[_to_response(r) for r in rows], total=total)


@router.get(
    "/{run_id}",
    response_model=RunResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def get_run(
    run_id: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> RunResponse:
    """Retrieve a single run by ID, including its results."""
    run = db.query(Run).filter(Run.id == run_id).first()
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")

    results: list[RunResultEntry] | None = None
    if run.results:
        results = [
            RunResultEntry(
                test_name=rr.test_name,
                passed=rr.passed,
                failed=rr.failed,
                duration_ms=rr.duration_ms,
                error=rr.error,
                assertions=json.loads(rr.assertions_json) if rr.assertions_json else None,
            )
            for rr in run.results
        ]

    return RunResponse(
        id=run.id,
        status=run.status,
        total_tests=run.total_tests,
        passed=run.passed,
        failed=run.failed,
        duration_ms=run.duration_ms,
        created_at=run.created_at,
        completed_at=run.completed_at,
        results=results,
    )
