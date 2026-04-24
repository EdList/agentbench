"""Pydantic request/response schemas for the AgentBench API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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

    name: str | None = Field(None, description="Human-readable name for this run")
    test_suite_code: str | None = Field(
        None, description="Inline Python code for the test suite"
    )
    test_suite_path: str | None = Field(
        None, description="Path to test suite on the server filesystem"
    )
    config: dict[str, Any] | None = Field(
        default_factory=dict, description="Run configuration overrides"
    )


class RunResultEntry(BaseModel):
    test_name: str
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    error: str | None = None
    assertions: list[dict[str, Any]] | None = None


class RunResponse(BaseModel):
    id: str
    status: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    duration_ms: float = 0.0
    created_at: datetime | None = None
    completed_at: datetime | None = None
    results: list[RunResultEntry] | None = None

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
    prompt: str | None = Field(None, description="Original prompt")
    tags: list[str] | None = Field(default_factory=list, description="Tags")


class TrajectoryResponse(BaseModel):
    id: str
    name: str
    step_count: int = 0
    prompt: str | None = None
    tags: list[str] | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

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


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------

class ScanRequest(BaseModel):
    """Submit a scan request against an agent endpoint."""

    agent_url: str = Field(..., description="HTTP endpoint URL of the agent to scan")
    categories: list[str] | None = Field(
        default=None,
        description="Probe categories to run (default: all)",
    )


class DomainScoreResponse(BaseModel):
    name: str
    score: float
    grade: str
    findings: list[str]
    recommendations: list[str]


class ScanResponse(BaseModel):
    """Full scan report returned after scanning an agent."""

    overall_score: float
    overall_grade: str
    domain_scores: list[DomainScoreResponse]
    summary: str
    behaviors_tested: int
    behaviors_passed: int
    behaviors_failed: int
    critical_issues: list[str]
    timestamp: str


class ScanSummaryResponse(BaseModel):
    """Lightweight summary for listing scans."""

    scan_id: str
    agent_url: str
    overall_score: float
    overall_grade: str
    timestamp: str
