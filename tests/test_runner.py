"""Tests for scan runner edge cases."""

from __future__ import annotations

import asyncio

from agentbench.scanner import runner


def test_run_scan_with_empty_domains_runs_no_probes(monkeypatch):
    async def fail_send_probe(*args, **kwargs):
        raise AssertionError("send_probe should not be called when domains=[]")

    monkeypatch.setattr(runner, "send_probe", fail_send_probe)

    result = asyncio.run(runner.run_scan("http://example.test", domains=[]))

    assert result.domain_scores == {}
    assert result.overall_score == 0
    assert result.probes_run == 0
