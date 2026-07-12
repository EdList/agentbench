"""Tests for scan runner edge cases."""

from __future__ import annotations

import asyncio

import pytest

from agentbench.probes.base import (
    Domain,
    Probe,
    ProbeResult,
    Severity,
)
from agentbench.scanner import runner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_probe(
    probe_id: str = "test-probe-01",
    domain: Domain = Domain.RELIABILITY,
    category: str = "edge_case",
    prompt: str = "What is 2+2?",
) -> Probe:
    return Probe(
        id=probe_id,
        domain=domain,
        category=category,
        description="A test probe",
        prompt=prompt,
        severity=Severity.INFO,
    )


def _make_result(
    probe: Probe,
    response: str = "The answer is 4.",
    status_code: int = 200,
    error: str | None = None,
) -> ProbeResult:
    return ProbeResult(
        probe=probe,
        response=response,
        status_code=status_code,
        error=error,
        latency_ms=42.0,
    )


def _fake_probes() -> list[Probe]:
    return [
        _make_probe("test-rel-01", Domain.RELIABILITY, "edge_case"),
        _make_probe("test-rel-02", Domain.RELIABILITY, "edge_case"),
        _make_probe("test-cap-01", Domain.CAPABILITY, "reasoning"),
    ]


# ---------------------------------------------------------------------------
# Existing test — empty domains
# ---------------------------------------------------------------------------

def test_run_scan_with_empty_domains_runs_no_probes(monkeypatch):
    async def fail_send_probe(*args, **kwargs):
        raise AssertionError("send_probe should not be called when domains=[]")

    monkeypatch.setattr(runner, "send_probe", fail_send_probe)

    result = asyncio.run(runner.run_scan("http://example.test", domains=[]))

    assert result.domain_scores == {}
    assert result.overall_score == 0
    assert result.probes_run == 0


# ---------------------------------------------------------------------------
# MEDIUM bonus (R4-C): Scan execution with mocked probes
# ---------------------------------------------------------------------------

def test_run_scan_collects_results_from_all_probes(monkeypatch):
    """Scan with mocked send_probe should run all probes and collect results."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    call_log: list[str] = []

    async def mock_send_probe(*args, **kwargs):
        probe = args[1] if len(args) > 1 else kwargs.get("probe")
        # args[1] is the probe positional arg from the runner
        if probe is None:
            # The runner calls send_probe(url, probe, ...) so probe is args[1]
            raise RuntimeError("probe arg missing")
        call_log.append(probe.id)
        return _make_result(probe)

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    result = asyncio.run(
        runner.run_scan("http://example.test", timeout=5.0),
    )

    assert result.probes_run == 3
    assert len(call_log) == 3
    assert set(call_log) == {"test-rel-01", "test-rel-02", "test-cap-01"}


def test_run_scan_produces_domain_scores(monkeypatch):
    """Scan should produce domain scores for each probed domain."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    async def mock_send_probe(url, probe, **kwargs):
        return _make_result(probe, response="OK", status_code=200)

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    result = asyncio.run(
        runner.run_scan("http://example.test", timeout=5.0),
    )

    # Two domains were probed: reliability + capability
    assert "reliability" in result.domain_scores
    assert "capability" in result.domain_scores
    ds = result.domain_scores["reliability"]
    assert ds.total == 2  # two reliability probes


def test_run_scan_calls_progress_callback(monkeypatch):
    """Progress callback should be invoked for each completed probe."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    async def mock_send_probe(url, probe, **kwargs):
        return _make_result(probe)

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    progress_calls: list[tuple[int, int]] = []

    async def on_progress(completed: int, total: int):
        progress_calls.append((completed, total))

    asyncio.run(
        runner.run_scan(
            "http://example.test",
            timeout=5.0,
            progress_callback=on_progress,
        ),
    )

    assert len(progress_calls) == 3
    assert progress_calls[-1] == (3, 3)


# ---------------------------------------------------------------------------
# Error handling during scan
# ---------------------------------------------------------------------------

def test_run_scan_handles_probe_exception(monkeypatch):
    """When send_probe raises, the scan should propagate the error
    (asyncio.gather without return_exceptions will raise)."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    async def mock_send_probe(url, probe, **kwargs):
        if probe.id == "test-rel-02":
            raise ConnectionError("connection reset")
        return _make_result(probe)

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    with pytest.raises(ConnectionError, match="connection reset"):
        asyncio.run(runner.run_scan("http://example.test", timeout=5.0))


def test_run_scan_all_probes_error_still_returns_result(monkeypatch):
    """Even if every probe errors, the exception propagates (not silently
    swallowed).  This verifies error handling is not silently caught."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    async def mock_send_probe(url, probe, **kwargs):
        raise RuntimeError("agent unreachable")

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    with pytest.raises(RuntimeError, match="agent unreachable"):
        asyncio.run(runner.run_scan("http://example.test", timeout=5.0))


# ---------------------------------------------------------------------------
# Domain filtering
# ---------------------------------------------------------------------------

def test_run_scan_domain_filter_only_runs_matching_probes(monkeypatch):
    """When domains=['reliability'], only reliability probes run."""
    fake_probes = _fake_probes()
    monkeypatch.setattr(runner, "get_all_probes", lambda: fake_probes)

    call_log: list[Domain] = []

    async def mock_send_probe(url, probe, **kwargs):
        call_log.append(probe.domain)
        return _make_result(probe)

    monkeypatch.setattr(runner, "send_probe", mock_send_probe)

    result = asyncio.run(
        runner.run_scan(
            "http://example.test", domains=["reliability"], timeout=5.0,
        ),
    )

    assert result.probes_run == 2
    assert all(d == Domain.RELIABILITY for d in call_log)
    assert "reliability" in result.domain_scores
    assert "capability" not in result.domain_scores
