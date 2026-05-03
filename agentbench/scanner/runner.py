"""Scan runner — orchestrates probes, analysis, and scoring."""

from __future__ import annotations

import asyncio
import logging
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

logger = logging.getLogger(__name__)

MAX_CONCURRENCY = 5
MIN_INTERVAL = 0.0  # no throttle — adaptive backoff handles rate limits
_RATE_LIMIT_WINDOW = 5  # if this many 429s in a row, increase delay
_MAX_BASE_DELAY = 30.0  # cap base delay at 30s


async def run_scan(
    url: str,
    *,
    api_key: str | None = None,
    model: str | None = None,
    domains: list[str] | None = None,
    timeout: float = 30.0,
    headers: dict[str, str] | None = None,
    progress_callback: Any = None,
) -> ScanResult:
    """Run a complete scan against an agent endpoint."""
    start = time.monotonic()

    # Select probes — use `is not None` so domains=[] returns empty (intentional)
    if domains is not None:
        filter_domains = {Domain(d) for d in domains}
        probes = [p for p in get_all_probes() if p.domain in filter_domains]
    else:
        probes = get_all_probes()

    total = len(probes)
    completed = 0
    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    last_request = time.monotonic()
    throttle_lock = asyncio.Lock()
    adaptive_delay = MIN_INTERVAL  # grows on repeated 429s
    consecutive_429s = 0

    async def _run_one(probe):
        nonlocal completed, last_request, adaptive_delay, consecutive_429s
        async with semaphore:
            # Throttle — lock protects read-sleep-write + adaptive state
            async with throttle_lock:
                now = time.monotonic()
                wait = adaptive_delay - (now - last_request)
                if wait > 0:
                    logger.info("adaptive throttle: sleeping %.1fs", wait)
                    await asyncio.sleep(wait)
                last_request = time.monotonic()

            result = await send_probe(
                url, probe, api_key=api_key, model=model, timeout=timeout, headers=headers
            )

            # Adaptive backoff: track consecutive 429s
            async with throttle_lock:
                if result.status_code == 429:
                    consecutive_429s += 1
                    if consecutive_429s >= _RATE_LIMIT_WINDOW:
                        # Exponential backoff, capped
                        adaptive_delay = min(adaptive_delay * 2 + 1.0, _MAX_BASE_DELAY)
                        logger.warning(
                            "rate-limited %d times, increasing delay to %.1fs",
                            consecutive_429s, adaptive_delay,
                        )
                else:
                    # Success — recover gradually
                    if consecutive_429s > 0:
                        consecutive_429s = 0
                        adaptive_delay = max(adaptive_delay / 2, MIN_INTERVAL)
                        logger.info("rate limit recovered, delay now %.1fs", adaptive_delay)

            completed += 1
            if progress_callback:
                try:
                    await progress_callback(completed, total)
                except Exception as exc:
                    logger.warning("progress callback failed: %s", exc)
            return result

    tasks = [_run_one(p) for p in probes]
    results: list[ProbeResult] = await asyncio.gather(*tasks)

    # Analyze
    all_findings: list[Finding] = []
    for r in results:
        finding = analyze_result(r)
        if finding is not None:
            all_findings.append(finding)

    # Score per domain — only domains that were actually probed
    domain_scores: dict[str, DomainScore] = {}
    probed_domains = {r.probe.domain for r in results}
    for domain in probed_domains:
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
