"""Project-related API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Project, get_db
from agentbench.server.routes.scans import _create_scan_job, _resolve_scan_request, _scan_job_to_response, submit_scan
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
    project = Project(
        name=body.name.strip(),
        description=body.description,
        principal=principal,
    )
    db.add(project)
    db.commit()
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
        projects=[ProjectResponse.model_validate(project, from_attributes=True) for project in rows],
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
        overall_score=scan.overall_score,
        overall_grade=scan.overall_grade,
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
    job = _create_scan_job(resolved, principal, db)
    return _scan_job_to_response(job)
