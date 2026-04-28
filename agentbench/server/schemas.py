"""Pydantic request/response schemas for the AgentBench API."""

from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

PUBLIC_SCAN_CATEGORY_TO_PROBE_CATEGORIES: dict[str, list[str]] = {
    "safety": ["safety", "persona"],
    "reliability": ["edge_case"],
    "capability": ["capability"],
    "robustness": ["robustness"],
}
PUBLIC_SCAN_CATEGORIES = tuple(PUBLIC_SCAN_CATEGORY_TO_PROBE_CATEGORIES.keys())
SCORING_DOMAINS = ("Safety", "Reliability", "Capability", "Robustness")
_DOMAIN_NAME_LOOKUP = {domain.lower(): domain for domain in SCORING_DOMAINS}
_MAX_TEST_SUITE_CODE_CHARS = 100_000
_MAX_PATH_CHARS = 1024
_MAX_URL_CHARS = 2048
_MAX_TRAJECTORY_JSON_CHARS = 1_000_000


def normalize_public_scan_category(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in PUBLIC_SCAN_CATEGORIES:
        raise ValueError(
            f"Unknown scan category {value!r}. Valid categories: {list(PUBLIC_SCAN_CATEGORIES)}"
        )
    return normalized


def normalize_scoring_domain_name(value: str) -> str:
    normalized = _DOMAIN_NAME_LOOKUP.get(value.strip().lower())
    if normalized is None:
        raise ValueError(
            f"Unknown scoring domain {value!r}. Valid domains: {list(SCORING_DOMAINS)}"
        )
    return normalized


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

    name: str | None = Field(None, description="Human-readable name for this run", max_length=255)
    test_suite_code: str | None = Field(
        None,
        description="Inline Python code for the test suite",
        max_length=_MAX_TEST_SUITE_CODE_CHARS,
    )
    test_suite_path: str | None = Field(
        None,
        description="Path to test suite on the server filesystem",
        max_length=_MAX_PATH_CHARS,
    )
    config: dict[str, Any] | None = Field(
        default_factory=dict, description="Run configuration overrides"
    )

    @field_validator("test_suite_path")
    @classmethod
    def validate_test_suite_path(cls, value: str | None) -> str | None:
        if value is None:
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("test_suite_path must not be empty.")
        parts = [part for part in normalized.replace("\\", "/").split("/") if part]
        if any(part == ".." for part in parts):
            raise ValueError(
                "test_suite_path must not contain parent-directory traversal segments."
            )
        # Block absolute paths (POSIX and Windows)
        from pathlib import Path
        if Path(normalized).is_absolute() or ":\\" in normalized[:3] or normalized.startswith("/"):
            raise ValueError("test_suite_path must be a relative path.")
        return normalized

    @model_validator(mode="after")
    def validate_run_target(self) -> RunCreateRequest:
        if not self.test_suite_code and not self.test_suite_path:
            raise ValueError("Provide either test_suite_code or test_suite_path.")
        return self


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

    name: str = Field(..., description="Name for the trajectory", min_length=1, max_length=255)
    data: dict[str, Any] = Field(..., description="Full trajectory JSON blob")
    prompt: str | None = Field(None, description="Original prompt", max_length=10_000)
    tags: list[str] | None = Field(default_factory=list, description="Tags")

    @field_validator("tags")
    @classmethod
    def validate_tags_length(cls, value: list[str] | None) -> list[str] | None:
        if value is not None and len(value) > 50:
            raise ValueError("tags must contain at most 50 items.")
        return value

    @model_validator(mode="after")
    def validate_trajectory_payload_size(self) -> TrajectoryUploadRequest:
        payload_size = len(str(self.data))
        if payload_size > _MAX_TRAJECTORY_JSON_CHARS:
            raise ValueError("Trajectory payload is too large.")
        return self


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
# Projects / Agents / Policies
# ---------------------------------------------------------------------------


class ProjectCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Project name")
    description: str | None = Field(None, description="Optional project description")

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("Project name must not be empty after stripping whitespace.")
        return stripped


class ProjectResponse(BaseModel):
    id: str
    name: str
    description: str | None = None
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class ProjectListResponse(BaseModel):
    projects: list[ProjectResponse]
    total: int


class SavedAgentCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255, description="Saved agent display name")
    agent_url: str = Field(
        ...,
        min_length=1,
        max_length=_MAX_URL_CHARS,
        description="HTTP endpoint for the saved agent",
    )

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url_shape(cls, value: str) -> str:
        parsed = value.strip()
        if not parsed:
            raise ValueError("agent_url must not be empty.")
        parts = parsed.split("://", 1)
        if len(parts) != 2 or parts[0] not in {"http", "https"}:
            raise ValueError("agent_url must start with http:// or https://.")
        host_part = parts[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if not host_part or host_part == ":":
            raise ValueError("agent_url must include a valid hostname.")
        # Block dangerous ports (SSRF mitigation)
        _ssrf_blocked_ports = frozenset(
            {22, 23, 25, 465, 587, 993, 995, 3306, 5432, 6379, 9200, 27017}
        )
        if ":" in host_part:
            port_token = host_part.rsplit(":", 1)[-1].strip("]")
            if port_token.isdigit():
                port = int(port_token)
                if port in _ssrf_blocked_ports:
                    raise ValueError("agent_url port is not allowed.")
        return parsed


class SavedAgentResponse(BaseModel):
    id: str
    project_id: str
    name: str
    agent_url: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SavedAgentListResponse(BaseModel):
    agents: list[SavedAgentResponse]
    total: int


class ScanPolicyCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200, description="Saved scan policy name")
    categories: list[str] | None = Field(
        default=None, description="Enabled categories; null means default scan categories"
    )
    minimum_overall_score: float | None = Field(default=None, ge=0, le=100)
    minimum_domain_scores: dict[str, float] = Field(default_factory=dict)
    fail_on_critical_issues: bool = Field(default=True)
    max_regression_delta: float | None = Field(default=None)

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        normalized: list[str] = []
        for category in value:
            canonical = normalize_public_scan_category(category)
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    @field_validator("minimum_domain_scores")
    @classmethod
    def validate_minimum_domain_scores(cls, value: dict[str, float]) -> dict[str, float]:
        normalized: dict[str, float] = {}
        for domain, threshold in value.items():
            canonical_domain = normalize_scoring_domain_name(domain)
            if not math.isfinite(threshold):
                raise ValueError(f"Domain threshold for {domain!r} must be finite.")
            if not 0 <= threshold <= 100:
                raise ValueError(f"Domain threshold for {domain!r} must be between 0 and 100.")
            normalized[canonical_domain] = threshold
        return normalized


class ScanPolicyResponse(BaseModel):
    id: str
    project_id: str
    name: str
    categories: list[str] | None = None
    minimum_overall_score: float | None = None
    minimum_domain_scores: dict[str, float]
    fail_on_critical_issues: bool
    max_regression_delta: float | None = None
    created_at: datetime | None = None


class ScanPolicyListResponse(BaseModel):
    policies: list[ScanPolicyResponse]
    total: int


class ProjectGateRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, description="Saved agent id to evaluate")
    policy_id: str = Field(..., min_length=1, description="Saved scan policy id to apply")


class ProjectGateResponse(BaseModel):
    scan_id: str
    project_id: str
    agent_id: str
    policy_id: str
    release_verdict: str | None = None
    verdict_reasons: list[str] = Field(default_factory=list)
    overall_score: float
    overall_grade: str
    permalink: str


class ScanJobResponse(BaseModel):
    job_id: str
    status: str
    cancel_requested: bool = False
    project_id: str | None = None
    agent_id: str | None = None
    policy_id: str | None = None
    agent_url: str
    scan_id: str | None = None
    release_verdict: str | None = None
    verdict_reasons: list[str] = Field(default_factory=list)
    overall_score: float | None = None
    overall_grade: str | None = None
    permalink: str | None = None
    error_detail: str | None = None
    created_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Scans
# ---------------------------------------------------------------------------


class ScanRequest(BaseModel):
    """Submit a scan request against an agent endpoint."""

    agent_url: str | None = Field(
        None,
        description="HTTP endpoint URL of the agent to scan",
        max_length=_MAX_URL_CHARS,
    )
    project_id: str | None = Field(None, description="Optional project id for a saved-agent scan")
    agent_id: str | None = Field(None, description="Optional saved agent id")
    policy_id: str | None = Field(None, description="Optional saved scan policy id")
    categories: list[str] | None = Field(
        default=None,
        description=(
            "Evaluation domains to run "
            "(safety, reliability, capability, robustness). Default: all"
        ),
    )

    @field_validator("agent_url")
    @classmethod
    def validate_agent_url_shape(cls, value: str | None) -> str | None:
        if value is None:
            return value
        parsed = value.strip()
        if not parsed:
            raise ValueError("agent_url must not be empty.")
        parts = parsed.split("://", 1)
        if len(parts) != 2 or parts[0] not in {"http", "https"}:
            raise ValueError("agent_url must start with http:// or https://.")
        host_part = parts[1].split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
        if not host_part or host_part == ":":
            raise ValueError("agent_url must include a valid hostname.")
        # Block dangerous ports (SSRF mitigation) — same list as SavedAgentCreateRequest
        _ssrf_blocked_ports = frozenset(
            {22, 23, 25, 465, 587, 993, 995, 3306, 5432, 6379, 9200, 27017}
        )
        if ":" in host_part:
            port_token = host_part.rsplit(":", 1)[-1].strip("]")
            if port_token.isdigit():
                port = int(port_token)
                if port in _ssrf_blocked_ports:
                    raise ValueError("agent_url port is not allowed.")
        return parsed

    @field_validator("categories")
    @classmethod
    def validate_categories(cls, value: list[str] | None) -> list[str] | None:
        if value is None:
            return value
        if len(value) == 0:
            raise ValueError("categories must not be an empty list. Set to null for defaults.")
        normalized: list[str] = []
        for category in value:
            canonical = normalize_public_scan_category(category)
            if canonical not in normalized:
                normalized.append(canonical)
        return normalized

    @model_validator(mode="after")
    def validate_target(self) -> ScanRequest:
        if not self.agent_url and not self.agent_id:
            raise ValueError("Provide either agent_url or agent_id.")
        return self


class DomainScoreResponse(BaseModel):
    name: str
    score: float = Field(ge=0, le=100)
    grade: str
    findings: list[str]
    recommendations: list[str]


class ScanResponse(BaseModel):
    """Full scan report returned after scanning an agent."""

    scan_id: str | None = None
    project_id: str | None = None
    agent_id: str | None = None
    policy_id: str | None = None
    release_verdict: str | None = None
    verdict_reasons: list[str] = Field(default_factory=list)
    overall_score: float = Field(ge=0, le=100)
    overall_grade: str
    domain_scores: list[DomainScoreResponse]
    summary: str
    behaviors_tested: int = Field(ge=0)
    behaviors_passed: int = Field(ge=0)
    behaviors_failed: int = Field(ge=0)
    critical_issues: list[str]
    timestamp: str


class ScanSummaryResponse(BaseModel):
    """Lightweight summary for listing scans."""

    scan_id: str
    agent_url: str
    overall_score: float
    overall_grade: str
    timestamp: str


class ScanHistoryEntryResponse(BaseModel):
    """Persisted scan history entry returned for a specific agent."""

    id: str
    agent_url: str
    created_at: str
    overall_score: float
    grade: str
    duration_ms: int | None = None


class RegressionDeltaResponse(BaseModel):
    """A single regression or improvement entry for one domain."""

    domain: str
    previous_score: float
    current_score: float
    delta: float
    severity: str | None = None


class RegressionReportResponse(BaseModel):
    """Regression report comparing the two latest scans for an agent."""

    agent_url: str
    current_scan_id: str
    current_scan_date: str
    previous_scan_id: str
    previous_scan_date: str
    overall_delta: float
    overall_trend: str
    regressions: list[RegressionDeltaResponse]
    improvements: list[RegressionDeltaResponse]


class ScanShareResponse(BaseModel):
    """Team-facing share payload for a specific scan."""

    scan_id: str
    agent_url: str
    permalink: str
    title: str
    markdown: str
    slack_text: str
