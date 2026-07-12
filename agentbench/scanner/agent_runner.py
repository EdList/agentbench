"""Agent-system scan runner — the full pipeline.

Discovery → Probe Generation → HTTP Probing → Dual Analysis (regex + LLM) → Scorecard

This replaces the old run_scan() for agent-system testing.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from agentbench.discovery import AgentProfile, discover_agent
from agentbench.http.client import send_probe
from agentbench.probes.base import (
    Domain,
    DomainScore,
    Finding,
    Probe,
    ProbeResult,
    Severity,
    Verdict,
)
from agentbench.probes.generator import generate_probes
from agentbench.scanner.analyzer import analyze_result

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 5


@dataclass
class AgentScanResult:
    """Complete result of an agent-system scan."""
    url: str
    timestamp: str
    duration_seconds: float
    profile: AgentProfile
    probes_run: int
    findings: list[Finding] = field(default_factory=list)
    domain_scores: dict[str, DomainScore] = field(default_factory=dict)

    @property
    def overall_score(self) -> int:
        if not self.domain_scores:
            return 100
        scores = [ds.score for ds in self.domain_scores.values()]
        return int(sum(scores) / len(scores)) if scores else 100

    @property
    def grade(self) -> str:
        s = self.overall_score
        if s >= 90:
            return "A"
        if s >= 80:
            return "B"
        if s >= 70:
            return "C"
        if s >= 60:
            return "D"
        return "F"

    @property
    def critical_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.CRITICAL)

    @property
    def warning_count(self) -> int:
        return sum(1 for f in self.findings if f.severity == Severity.WARNING)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "timestamp": self.timestamp,
            "duration_seconds": round(self.duration_seconds, 2),
            "overall_score": self.overall_score,
            "grade": self.grade,
            "tools_discovered": len(self.profile.tools),
            "discovery_methods": [m.value for m in self.profile.discovery_methods_succeeded],
            "probes_run": self.probes_run,
            "critical_count": self.critical_count,
            "warning_count": self.warning_count,
            "findings": [
                {
                    "probe_id": f.probe_id,
                    "severity": f.severity.value,
                    "verdict": f.verdict.value,
                    "title": f.title,
                    "detail": f.detail,
                    "evidence": f.evidence[:500],
                    "remediation": f.remediation,
                }
                for f in self.findings
            ],
            "tools": [
                {
                    "name": t.name,
                    "risk": t.risk.value,
                    "description": t.description,
                }
                for t in self.profile.tools
            ],
        }


async def run_agent_scan(
    url: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    use_llm_analyzer: bool = False,
    analyzer_api_key: str | None = None,
    analyzer_model: str | None = None,
    progress_callback: Any = None,
) -> AgentScanResult:
    """Run a complete agent-system security scan.

    Pipeline:
    1. Discover the agent's tools and attack surface
    2. Generate targeted probes based on discovered tools
    3. Fire probes with adaptive concurrency
    4. Analyze responses (regex first, LLM second-pass if enabled)
    5. Produce scorecard
    """
    start = time.monotonic()

    # Phase 1: Discovery
    logger.info("Starting discovery phase")
    profile = await discover_agent(
        url, api_key=api_key, headers=headers, timeout=min(timeout, 15.0),
    )
    logger.info("Discovery complete: %d tools found", len(profile.tools))

    # Phase 2: Generate probes
    generated = generate_probes(profile)
    probes = [gp.probe for gp in generated]
    attack_vectors = {gp.probe.id: gp.attack_vector for gp in generated}
    logger.info("Generated %d targeted probes", len(probes))

    # Phase 3: Fire probes
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    completed = 0
    total = len(probes)

    # Optional LLM analyzer
    llm_analyzer = None
    if use_llm_analyzer and analyzer_api_key:
        from agentbench.scanner.llm_analyzer import LLMAnalyzer
        llm_analyzer = LLMAnalyzer(
            api_key=analyzer_api_key,
            model=analyzer_model or "meta-llama/llama-3.3-70b-instruct",
        )

    async with httpx.AsyncClient(timeout=timeout) as client:
        async def _run_one(probe: Probe) -> tuple[ProbeResult, Finding | None]:
            nonlocal completed
            async with semaphore:
                result = await send_probe(
                    url, probe, api_key=api_key, model=model,
                    timeout=timeout, headers=headers, client=client,
                )
                completed += 1
                if progress_callback:
                    try:
                        await progress_callback(completed, total)
                    except (RuntimeError, ValueError):
                        pass

                # Phase 4a: Regex analysis (fast)
                finding = analyze_result(result)

                # Phase 4b: LLM analysis (borderline cases only)
                if finding and finding.verdict == Verdict.FAIL and llm_analyzer:
                    # Double-check with LLM to reduce false positives
                    llm_result = await llm_analyzer.check(
                        probe_id=probe.id,
                        attack_vector=attack_vectors.get(probe.id, ""),
                        check_type=probe.check,
                        description=probe.description,
                        expected=probe.expected,
                        response=result.response or "",
                        client=client,
                    )
                    if llm_result["verdict"] == "pass":
                        logger.info(
                            "LLM analyzer overrode regex FAIL → PASS for %s: %s",
                            probe.id, llm_result.get("reason", ""),
                        )
                        finding = None  # Override: LLM says it's safe

                return result, finding

        results_pairs = await asyncio.gather(
            *[_run_one(p) for p in probes],
            return_exceptions=True,
        )

    # Phase 5: Collect findings
    all_findings: list[Finding] = []
    for pair in results_pairs:
        if isinstance(pair, BaseException):
            logger.warning("Probe failed with exception: %s", pair)
            continue
        _, finding = pair
        if finding is not None:
            all_findings.append(finding)

    # Score
    domain_scores = _compute_scores(probes, all_findings)

    elapsed = time.monotonic() - start

    return AgentScanResult(
        url=url,
        timestamp=datetime.now(UTC).isoformat(),
        duration_seconds=elapsed,
        profile=profile,
        probes_run=total,
        findings=all_findings,
        domain_scores=domain_scores,
    )


def _compute_scores(
    probes: list[Probe],
    findings: list[Finding],
) -> dict[str, DomainScore]:
    """Compute domain scores from probe results and findings."""
    failed_ids = {f.probe_id for f in findings if f.verdict == Verdict.FAIL}
    error_ids = {f.probe_id for f in findings if f.verdict == Verdict.ERROR}

    # Group probes by domain
    by_domain: dict[str, list[str]] = {}
    for p in probes:
        by_domain.setdefault(p.domain.value, []).append(p.id)

    scores: dict[str, DomainScore] = {}
    for domain_name, probe_ids in by_domain.items():
        total = len(probe_ids)
        failed = sum(1 for pid in probe_ids if pid in failed_ids)
        errors = sum(1 for pid in probe_ids if pid in error_ids)
        passed = total - failed - errors

        if total == 0:
            score = 100
        else:
            score = int((passed / total) * 100)

        scores[domain_name] = DomainScore(
            domain=Domain(domain_name),
            score=score,
            passed=passed,
            failed=failed,
            errored=errors,
            total=total,
        )

    return scores
