"""Project-related API routes."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import text
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Project, get_db
from agentbench.server.routes.scans import (
    _create_scan_job,
    _enforce_scan_queue_limits,
    _enforce_scan_rate_limit,
    _resolve_scan_request,
    _scan_job_to_response,
    submit_scan,
)
from agentbench.server.schemas import (
    ErrorResponse,
    ProjectCreateRequest,
    ProjectGateRequest,
    ProjectGateResponse,
    ProjectListResponse,
    ProjectResponse,
    ScanJobResponse,
    ScanRequest,
)

router = APIRouter(prefix="/projects", tags=["projects"])


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}},
)
def create_project(
    body: ProjectCreateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ProjectResponse:
    # Per-principal project creation limit — wrapped in an exclusive DB
    # transaction to prevent TOCTOU races on the count-then-insert pattern.
    from agentbench.server.config import settings as _settings  # noqa: F401
    _max_projects = int(os.getenv("AGENTBENCH_MAX_PROJECTS_PER_PRINCIPAL", "100"))
    try:
        db.execute(text("BEGIN EXCLUSIVE"))
    except Exception:
        # PostgreSQL or unsupported — rely on row-level locking instead
        pass
    try:
        existing = db.query(Project).filter(Project.principal == principal).count()
        if existing >= _max_projects:
            raise HTTPException(
                status_code=429,
                detail=f"Project limit reached ({_max_projects}). Delete existing projects first.",
            )
        project = Project(
            name=body.name.strip(),
            description=body.description,
            principal=principal,
        )
        db.add(project)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    db.refresh(project)
    return ProjectResponse.model_validate(project, from_attributes=True)


@router.get(
    "",
    response_model=ProjectListResponse,
    responses={401: {"model": ErrorResponse}},
)
def list_projects(
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ProjectListResponse:
    rows = (
        db.query(Project)
        .filter(Project.principal == principal)
        .order_by(Project.created_at.desc())
        .all()
    )
    return ProjectListResponse(
        projects=[
            ProjectResponse.model_validate(project, from_attributes=True) for project in rows
        ],
        total=len(rows),
    )


@router.post(
    "/{project_id}/gate",
    response_model=ProjectGateResponse,
    status_code=status.HTTP_200_OK,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def run_project_gate(
    project_id: str,
    body: ProjectGateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ProjectGateResponse:
    # Verify that the project exists and belongs to the principal
    project = db.query(Project).filter(
        Project.id == project_id, Project.principal == principal
    ).first()
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")

    # Verify that agent_id belongs to the given project
    from agentbench.server.models import SavedAgent
    agent = db.query(SavedAgent).filter(
        SavedAgent.id == body.agent_id, SavedAgent.principal == principal
    ).first()
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Saved agent {body.agent_id!r} not found.")
    if agent.project_id != project_id:
        raise HTTPException(
            status_code=400, detail="Saved agent does not belong to this project."
        )

    # Verify that policy_id belongs to the given project
    from agentbench.server.models import ScanPolicy
    policy = db.query(ScanPolicy).filter(
        ScanPolicy.id == body.policy_id, ScanPolicy.principal == principal
    ).first()
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Scan policy {body.policy_id!r} not found.")
    if policy.project_id != project_id:
        raise HTTPException(
            status_code=400, detail="Scan policy does not belong to this project."
        )

    scan = submit_scan(
        ScanRequest(
            project_id=project_id,
            agent_id=body.agent_id,
            policy_id=body.policy_id,
        ),
        principal=principal,
        db=db,
    )
    return ProjectGateResponse(
        scan_id=scan.scan_id or "",
        project_id=scan.project_id or project_id,
        agent_id=scan.agent_id or body.agent_id,
        policy_id=scan.policy_id or body.policy_id,
        release_verdict=scan.release_verdict,
        verdict_reasons=list(scan.verdict_reasons),
        overall_score=scan.overall_score or 0.0,
        overall_grade=scan.overall_grade or "N/A",
        permalink=f"/?scan_id={scan.scan_id}",
    )


@router.post(
    "/{project_id}/gate/jobs",
    response_model=ScanJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def run_project_gate_job(
    project_id: str,
    body: ProjectGateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanJobResponse:
    resolved = _resolve_scan_request(
        ScanRequest(
            project_id=project_id,
            agent_id=body.agent_id,
            policy_id=body.policy_id,
        ),
        principal,
        db,
    )
    _enforce_scan_rate_limit(principal)
    job = _enforce_scan_queue_limits(db, principal, resolved)
    job = _create_scan_job(job, resolved, principal, db)
    return _scan_job_to_response(job)
