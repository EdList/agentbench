"""Core data models for AgentBench probes and results."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Domain(StrEnum):
    SAFETY = "safety"
    RELIABILITY = "reliability"
    CAPABILITY = "capability"
    CONSISTENCY = "consistency"


class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"


class Verdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


@dataclass
class Probe:
    """A single behavioral test probe sent to an agent."""

    id: str
    domain: Domain
    category: str
    description: str
    prompt: str
    system_prompt: str | None = None
    follow_ups: list[str] = field(default_factory=list)
    severity: Severity = Severity.WARNING
    tags: list[str] = field(default_factory=list)
    # What to check in the response
    check: str = ""  # hint for analyzer: "injection_success", "pii_leak", "compliance", etc.
    # Expected behavior description
    expected: str = ""

    @property
    def is_multi_turn(self) -> bool:
        return len(self.follow_ups) > 0


@dataclass
class ProbeResult:
    """Result of running a single probe against an agent."""

    probe: Probe
    response: str | None = None
    status_code: int | None = None
    latency_ms: float = 0.0
    error: str | None = None
    follow_up_responses: list[str] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.error is not None or self.status_code is None

    @property
    def full_conversation(self) -> list[dict[str, str]]:
        """Return the full multi-turn conversation as message dicts."""
        messages = [{"role": "user", "content": self.probe.prompt}]
        if self.response:
            messages.append({"role": "assistant", "content": self.response})
        for i, follow_up in enumerate(self.probe.follow_ups):
            messages.append({"role": "user", "content": follow_up})
            if i < len(self.follow_up_responses):
                messages.append(
                    {"role": "assistant", "content": self.follow_up_responses[i]}
                )
        return messages


@dataclass
class Finding:
    """A specific behavioral finding from analyzing a probe result."""

    probe_id: str
    domain: Domain
    category: str
    severity: Severity
    verdict: Verdict
    title: str
    detail: str
    evidence: str

    def __str__(self) -> str:
        icon = {"critical": "❌", "warning": "⚠️", "info": "ℹ️"}.get(
            self.severity.value, "•"
        )
        return f"{icon} {self.title}"


@dataclass
class DomainScore:
    """Score for a single behavioral domain."""

    domain: Domain
    score: int = 100  # 0-100, starts perfect, deductions for findings
    findings: list[Finding] = field(default_factory=list)
    passed: int = 0
    failed: int = 0
    errored: int = 0
    total: int = 0

    @property
    def grade(self) -> str:
        if self.score >= 90:
            return "A"
        if self.score >= 80:
            return "B"
        if self.score >= 70:
            return "C"
        if self.score >= 60:
            return "D"
        return "F"

    @property
    def status_icon(self) -> str:
        if self.score >= 80:
            return "✅"
        if self.score >= 60:
            return "⚠️"
        return "❌"


@dataclass
class ScanResult:
    """Complete result of scanning an agent endpoint."""

    url: str
    overall_score: int
    domain_scores: dict[str, DomainScore]
    findings: list[Finding]
    duration_seconds: float
    probes_run: int
    timestamp: str
    agent_info: dict[str, Any] = field(default_factory=dict)

    @property
    def grade(self) -> str:
        if self.overall_score >= 90:
            return "A"
        if self.overall_score >= 80:
            return "B"
        if self.overall_score >= 70:
            return "C"
        if self.overall_score >= 60:
            return "D"
        return "F"

    @property
    def critical_count(self) -> int:
        return sum(
            1 for f in self.findings if f.severity == Severity.CRITICAL
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1 for f in self.findings if f.severity == Severity.WARNING
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "overall_score": self.overall_score,
            "grade": self.grade,
            "probes_run": self.probes_run,
            "duration_seconds": round(self.duration_seconds, 1),
            "timestamp": self.timestamp,
            "findings": [
                {
                    "probe_id": f.probe_id,
                    "domain": f.domain.value,
                    "category": f.category,
                    "severity": f.severity.value,
                    "title": f.title,
                    "detail": f.detail,
                    "evidence": f.evidence[:200],
                }
                for f in self.findings
            ],
            "domains": {
                name: {
                    "score": ds.score,
                    "grade": ds.grade,
                    "passed": ds.passed,
                    "failed": ds.failed,
                    "total": ds.total,
                }
                for name, ds in self.domain_scores.items()
            },
        }
