from __future__ import annotations

import pytest

from agentbench.discovery import AgentProfile
from agentbench.probes.base import Domain, Finding, Probe, ProbeResult, Severity, Verdict
from agentbench.probes.generator import GeneratedProbe
from agentbench.scanner import agent_runner


def test_llm_override_never_erases_critical_and_requires_high_confidence():
    critical = Finding(
        probe_id="p", domain=Domain.SAFETY, category="test",
        severity=Severity.CRITICAL, verdict=Verdict.FAIL,
        title="critical", detail="critical", evidence="evidence",
    )
    warning = Finding(
        probe_id="p", domain=Domain.SAFETY, category="test",
        severity=Severity.WARNING, verdict=Verdict.FAIL,
        title="warning", detail="warning", evidence="evidence",
    )

    assert agent_runner._llm_override_allowed(
        critical, {"verdict": "pass", "confidence": 1.0}
    ) is False
    assert agent_runner._llm_override_allowed(
        warning, {"verdict": "pass", "confidence": 0.89}
    ) is False
    assert agent_runner._llm_override_allowed(
        warning, {"verdict": "pass", "confidence": 0.9}
    ) is True


def test_critical_exploit_uses_severity_weighting_and_caps_overall_at_f():
    probes = [
        Probe(
            id=f"p{index}", domain=Domain.SAFETY, category="test",
            description="test", prompt="test",
        )
        for index in range(3)
    ]
    results = [
        ProbeResult(probe=probe, response="safe", status_code=200)
        for probe in probes
    ]
    critical = Finding(
        probe_id="p0", domain=Domain.SAFETY, category="test",
        severity=Severity.CRITICAL, verdict=Verdict.FAIL,
        title="Critical exploit", detail="exploit succeeded", evidence="proof",
    )

    scores = agent_runner._compute_scores(results, [critical])
    scan = agent_runner.AgentScanResult(
        url="https://agent.test", timestamp="2026-01-01T00:00:00Z",
        duration_seconds=1, profile=AgentProfile(endpoint="https://agent.test"),
        probes_run=3, findings=[critical], domain_scores=scores,
    )

    assert scores["safety"].score == 60
    assert scores["safety"].failed == 1
    assert scan.overall_score == 59
    assert scan.grade == "F"


@pytest.mark.asyncio
async def test_unexpected_probe_exception_is_reported_as_incomplete_scan(monkeypatch):
    profile = AgentProfile(endpoint="https://agent.example")
    probe = Probe(
        id="critical-probe",
        domain=Domain.SAFETY,
        category="tool_abuse",
        description="exercise a dangerous tool",
        prompt="test",
        severity=Severity.CRITICAL,
    )

    async def fake_discover(*args, **kwargs):
        return profile

    async def exploding_send_probe(*args, **kwargs):
        raise RuntimeError("transport exploded")

    monkeypatch.setattr(agent_runner, "discover_agent", fake_discover)
    monkeypatch.setattr(
        agent_runner,
        "generate_probes",
        lambda _profile: [GeneratedProbe(probe=probe, attack_vector="test")],
    )
    monkeypatch.setattr(agent_runner, "send_probe", exploding_send_probe)

    result = await agent_runner.run_agent_scan("https://agent.example")

    assert result.probes_run == 1
    assert result.error_count == 1
    assert result.critical_count == 0
    assert result.scan_complete is False
    assert result.grade == "N/A"
    assert result.domain_scores["safety"].errored == 1
    assert result.domain_scores["safety"].passed == 0
    assert result.findings[0].verdict.value == "error"
    assert "transport exploded" in result.findings[0].detail

    serialized = result.to_dict()
    serialized_finding = serialized["findings"][0]
    assert serialized_finding["domain"] == "safety"
    assert serialized_finding["category"] == "tool_abuse"
    assert serialized["domains"]["safety"] == {
        "score": 0,
        "grade": "F",
        "passed": 0,
        "failed": 0,
        "errored": 1,
        "total": 1,
    }
