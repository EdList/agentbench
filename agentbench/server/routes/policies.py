"""Scan-policy API routes."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Project, ScanPolicy, get_db
from agentbench.server.schemas import (
    ErrorResponse,
    ScanPolicyCreateRequest,
    ScanPolicyListResponse,
    ScanPolicyResponse,
)

router = APIRouter(prefix="/projects/{project_id}/policies", tags=["policies"])


def _get_project_or_404(db: Session, project_id: str, principal: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.principal == principal)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    return project


def _policy_to_response(policy: ScanPolicy) -> ScanPolicyResponse:
    return ScanPolicyResponse(
        id=policy.id,
        project_id=policy.project_id,
        name=policy.name,
        categories=json.loads(policy.categories_json) if policy.categories_json else None,
        minimum_overall_score=policy.minimum_overall_score,
        minimum_domain_scores=json.loads(policy.minimum_domain_scores_json),
        fail_on_critical_issues=bool(policy.fail_on_critical_issues),
        max_regression_delta=policy.max_regression_delta,
        created_at=policy.created_at,
    )


@router.post(
    "",
    response_model=ScanPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def create_scan_policy(
    project_id: str,
    body: ScanPolicyCreateRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanPolicyResponse:
    _get_project_or_404(db, project_id, principal)
    policy = ScanPolicy(
        project_id=project_id,
        principal=principal,
        name=body.name.strip(),
        categories_json=json.dumps(body.categories) if body.categories is not None else None,
        minimum_overall_score=body.minimum_overall_score,
        minimum_domain_scores_json=json.dumps(body.minimum_domain_scores),
        fail_on_critical_issues=1 if body.fail_on_critical_issues else 0,
        max_regression_delta=body.max_regression_delta,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return _policy_to_response(policy)


@router.get(
    "",
    response_model=ScanPolicyListResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def list_scan_policies(
    project_id: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanPolicyListResponse:
    _get_project_or_404(db, project_id, principal)
    rows = (
        db.query(ScanPolicy)
        .filter(ScanPolicy.project_id == project_id, ScanPolicy.principal == principal)
        .order_by(ScanPolicy.created_at.desc())
        .all()
    )
    return ScanPolicyListResponse(
        policies=[_policy_to_response(policy) for policy in rows],
        total=len(rows),
    )
