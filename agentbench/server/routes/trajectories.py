"""Trajectory-related API routes: upload, list, diff stored trajectories."""

from __future__ import annotations

import json
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from agentbench.server.auth import require_auth
from agentbench.server.models import Trajectory, get_db
from agentbench.server.schemas import (
    ErrorResponse,
    TrajectoryDiffEntry,
    TrajectoryDiffResponse,
    TrajectoryListResponse,
    TrajectoryResponse,
    TrajectoryUploadRequest,
)

router = APIRouter(prefix="/trajectories", tags=["trajectories"])


@router.post(
    "",
    response_model=TrajectoryResponse,
    status_code=status.HTTP_201_CREATED,
    responses={401: {"model": ErrorResponse}},
)
def upload_trajectory(
    body: TrajectoryUploadRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> TrajectoryResponse:
    """Upload a golden trajectory."""
    steps = body.data.get("steps", [])
    traj_id = str(uuid.uuid4())
    traj = Trajectory(
        id=traj_id,
        principal=principal,
        name=body.name,
        data=json.dumps(body.data),
        prompt=body.prompt or body.data.get("prompt"),
        step_count=len(steps),
        tags=",".join(body.tags) if body.tags else None,
    )
    db.add(traj)
    db.commit()
    db.refresh(traj)

    return _traj_to_response(traj)


@router.get(
    "",
    response_model=TrajectoryListResponse,
    responses={401: {"model": ErrorResponse}},
)
def list_trajectories(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> TrajectoryListResponse:
    """List stored trajectories."""
    total = db.query(Trajectory).filter(Trajectory.principal == principal).count()
    rows = (
        db.query(Trajectory)
        .filter(Trajectory.principal == principal)
        .order_by(Trajectory.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return TrajectoryListResponse(
        trajectories=[_traj_to_response(r) for r in rows],
        total=total,
    )


@router.get(
    "/{name}/diff",
    response_model=TrajectoryDiffResponse,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
def diff_trajectory(
    name: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> TrajectoryDiffResponse:
    """Diff the named trajectory against the latest uploaded trajectory.

    Uses the same ``TrajectoryDiff`` logic from ``agentbench.storage.trajectory``.
    """
    target = (
        db.query(Trajectory)
        .filter(Trajectory.name == name, Trajectory.principal == principal)
        .first()
    )
    if target is None:
        raise HTTPException(status_code=404, detail=f"Trajectory {name!r} not found.")

    latest = (
        db.query(Trajectory)
        .filter(Trajectory.id != target.id, Trajectory.principal == principal)
        .order_by(Trajectory.created_at.desc())
        .first()
    )
    if latest is None:
        raise HTTPException(
            status_code=404,
            detail="No other trajectory to diff against.",
        )

    golden_data = json.loads(target.data)
    current_data = json.loads(latest.data)

    from agentbench.storage.trajectory import TrajectoryDiff

    differ = TrajectoryDiff()
    result = differ.compare(golden_data, current_data)

    diffs = [
        TrajectoryDiffEntry(
            step_number=d.step_number,
            severity=d.severity,
            field=d.field,
            golden_value=d.golden_value,
            current_value=d.current_value,
            message=d.message,
        )
        for d in result.step_diffs
    ]

    return TrajectoryDiffResponse(
        golden_name=result.golden_name,
        current_name=result.current_name,
        diffs=diffs,
        summary=result.summary,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _traj_to_response(traj: Trajectory) -> TrajectoryResponse:
    return TrajectoryResponse(
        id=traj.id,
        name=traj.name,
        step_count=traj.step_count,
        prompt=traj.prompt,
        tags=traj.tags.split(",") if traj.tags else [],
        created_at=traj.created_at,
        updated_at=traj.updated_at,
    )
