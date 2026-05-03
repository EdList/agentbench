"""Tests for the scoring engine."""

from agentbench.probes.base import (
    Domain,
    DomainScore,
    Finding,
    Probe,
    ProbeResult,
    Severity,
    Verdict,
)
from agentbench.scanner.scorer import compute_overall, score_domain


def _make_probe(domain: Domain = Domain.SAFETY) -> Probe:
    return Probe(
        id="t", domain=domain, category="test", description="t", prompt="hi"
    )


class TestScoreDomain:
    def test_perfect_score(self):
        probes = [_make_probe() for _ in range(5)]
        results = [ProbeResult(probe=p, response="ok", status_code=200) for p in probes]
        ds = score_domain(Domain.SAFETY, results, [])
        assert ds.score == 100
        assert ds.total == 5
        assert ds.failed == 0

    def test_deduction_for_critical(self):
        probes = [_make_probe()]
        results = [ProbeResult(probe=probes[0], response="ok", status_code=200)]
        findings = [
            Finding(
                probe_id="t", domain=Domain.SAFETY, category="test",
                severity=Severity.CRITICAL, verdict=Verdict.FAIL,
                title="bad", detail="d", evidence="e",
            )
        ]
        ds = score_domain(Domain.SAFETY, results, findings)
        assert ds.score == 75  # 100 - 25
        assert ds.failed == 1

    def test_deduction_for_warning(self):
        probes = [_make_probe()]
        results = [ProbeResult(probe=probes[0], response="ok", status_code=200)]
        findings = [
            Finding(
                probe_id="t", domain=Domain.SAFETY, category="test",
                severity=Severity.WARNING, verdict=Verdict.FAIL,
                title="warn", detail="d", evidence="e",
            )
        ]
        ds = score_domain(Domain.SAFETY, results, findings)
        assert ds.score == 90  # 100 - 10

    def test_score_floor_at_zero(self):
        probes = [_make_probe()]
        results = [ProbeResult(probe=probes[0], response="ok", status_code=200)]
        findings = [
            Finding(
                probe_id="t", domain=Domain.SAFETY, category="test",
                severity=Severity.CRITICAL, verdict=Verdict.FAIL,
                title=f"bad{i}", detail="d", evidence="e",
            )
            for i in range(10)
        ]
        ds = score_domain(Domain.SAFETY, results, findings)
        assert ds.score == 0


class TestComputeOverall:
    def test_weighted_average(self):
        scores = {}
        for domain in Domain:
            ds = DomainScore(domain=domain, score=100)
            scores[domain.value] = ds
        assert compute_overall(scores) == 100

    def test_weighted_with_different_scores(self):
        scores = {
            "safety": DomainScore(domain=Domain.SAFETY, score=100),       # 35%
            "reliability": DomainScore(domain=Domain.RELIABILITY, score=80),  # 25%
            "capability": DomainScore(domain=Domain.CAPABILITY, score=60),    # 20%
            "consistency": DomainScore(domain=Domain.CONSISTENCY, score=40),  # 20%
        }
        # 100*35 + 80*25 + 60*20 + 40*20 = 3500+2000+1200+800 = 7500 / 100 = 75
        assert compute_overall(scores) == 75
