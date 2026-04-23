"""Pydantic request/response schemas for the AgentBench API."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status: str = "ok"
    version: str = "0.1.0"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class RunCreateRequest(BaseModel):
    """Submit a new test run."""

    name: Optional[str] = Field(None, description="Human-readable name for this run")
    test_suite_code: Optional[str] = Field(
        None, description="Inline Python code for the test suite"
    )
    test_suite_path: Optional[str] = Field(
        None, description="Path to test suite on the server filesystem"
    )
    config: Optional[dict[str, Any]] = Field(
        default_factory=dict, description="Run configuration overrides"
    )


class RunResultEntry(BaseModel):
    test_name: str
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    error: Optional[str] = None
    assertions: Optional[list[dict[str, Any]]] = None


class RunResponse(BaseModel):
    id: str
    status: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    created_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    results: Optional[list[RunResultEntry]] = None

    model_config = {"from_attributes": True}


class RunListResponse(BaseModel):
    runs: list[RunResponse]
    total: int


# ---------------------------------------------------------------------------
# Trajectories
# ---------------------------------------------------------------------------

class TrajectoryUploadRequest(BaseModel):
    """Upload a golden trajectory."""

    name: str = Field(..., description="Name for the trajectory")
    data: dict[str, Any] = Field(..., description="Full trajectory JSON blob")
    prompt: Optional[str] = Field(None, description="Original prompt")
    tags: Optional[list[str]] = Field(default_factory=list, description="Tags")


class TrajectoryResponse(BaseModel):
    id: str
    name: str
    step_count: int = 0
    prompt: Optional[str] = None
    tags: Optional[list[str]] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


class TrajectoryListResponse(BaseModel):
    trajectories: list[TrajectoryResponse]
    total: int


class TrajectoryDiffEntry(BaseModel):
    step_number: int
    severity: str
    field: str
    golden_value: Any
    current_value: Any
    message: str


class TrajectoryDiffResponse(BaseModel):
    golden_name: str
    current_name: str
    diffs: list[TrajectoryDiffEntry]
    summary: dict[str, int]


# ---------------------------------------------------------------------------
# Generic
# ---------------------------------------------------------------------------

class ErrorResponse(BaseModel):
    detail: str
