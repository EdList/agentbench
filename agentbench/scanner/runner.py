"""Scan runner — orchestrates probes, analysis, and scoring."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import Any

from agentbench.http.client import send_probe
from agentbench.probes.base import (
    Domain,
    DomainScore,
    Finding,
    ProbeResult,
    ScanResult,
)
from agentbench.probes.registry import get_all_probes
from agentbench.scanner.analyzer import analyze_result
from agentbench.scanner.scorer import compute_overall, score_domain

MAX_CONCURRENCY = 5


async def run_scan(
    url: str,
    *,
    api_key: str | None = None,
    domains: list[str] | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    progress_callback: Any = None,
) -> ScanResult:
    """Run a complete scan against an agent endpoint."""
    start = time.monotonic()

    # Select probes
    if domains:
        filter_domains = {Domain(d) for d in domains}
        probes = [p for p in get_all_probes() if p.domain in filter_domains]
    else:
        probes = get_all_probes()

    total = len(probes)
    completed = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _run_one(probe):
        nonlocal completed
        async with semaphore:
            result = await send_probe(
                url, probe, api_key=api_key, timeout=timeout, headers=headers
            )
            completed += 1
            if progress_callback:
                try:
                    await progress_callback(completed, total)
                except Exception:
                    pass
            return result

    tasks = [_run_one(p) for p in probes]
    results: list[ProbeResult] = await asyncio.gather(*tasks)

    # Analyze
    all_findings: list[Finding] = []
    for r in results:
        finding = analyze_result(r)
        if finding is not None:
            all_findings.append(finding)

    # Score per domain
    domain_scores: dict[str, DomainScore] = {}
    for domain in Domain:
        d_results = [r for r in results if r.probe.domain == domain]
        d_findings = [f for f in all_findings if f.domain == domain]
        domain_scores[domain.value] = score_domain(domain, d_results, d_findings)

    overall = compute_overall(domain_scores)
    duration = time.monotonic() - start

    return ScanResult(
        url=url,
        overall_score=overall,
        domain_scores=domain_scores,
        findings=all_findings,
        duration_seconds=duration,
        probes_run=total,
        timestamp=datetime.now(UTC).isoformat(),
    )
