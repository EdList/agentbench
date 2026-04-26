"""Saved-agent API routes."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Project, SavedAgent, get_db
from agentbench.server.schemas import (
    ErrorResponse,
    SavedAgentCreateRequest,
    SavedAgentListResponse,
    SavedAgentResponse,
)

router = APIRouter(prefix="/projects/{project_id}/agents", tags=["agents"])


def _get_project_or_404(db: Session, project_id: str, principal: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.principal == principal)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    return project


@router.post(
    "",
    response_model=SavedAgentResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def create_saved_agent(
    project_id: str,
    body: SavedAgentCreateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> SavedAgentResponse:
    _get_project_or_404(db, project_id, principal)
    agent = SavedAgent(
        project_id=project_id,
        principal=principal,
        name=body.name.strip(),
        agent_url=body.agent_url.strip(),
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)
    return SavedAgentResponse.model_validate(agent, from_attributes=True)


@router.get(
    "",
    response_model=SavedAgentListResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def list_saved_agents(
    project_id: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> SavedAgentListResponse:
    _get_project_or_404(db, project_id, principal)
    rows = (
        db.query(SavedAgent)
        .filter(SavedAgent.project_id == project_id, SavedAgent.principal == principal)
        .order_by(SavedAgent.created_at.desc())
        .all()
    )
    return SavedAgentListResponse(
        agents=[SavedAgentResponse.model_validate(agent, from_attributes=True) for agent in rows],
        total=len(rows),
    )
