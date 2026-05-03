"""Tests for probe data models."""

from agentbench.probes.base import (
    Domain,
    DomainScore,
    Finding,
    Probe,
    ProbeResult,
    ScanResult,
    Severity,
    Verdict,
)


class TestProbe:
    def test_is_multi_turn_false_by_default(self):
        p = Probe(id="t", domain=Domain.SAFETY, category="test", description="t", prompt="hi")
        assert not p.is_multi_turn

    def test_is_multi_turn_with_follow_ups(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t",
            prompt="hi", follow_ups=["follow up"],
        )
        assert p.is_multi_turn


class TestProbeResult:
    def test_is_error_when_no_error(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t", prompt="hi"
        )
        r = ProbeResult(probe=p, response="ok", status_code=200)
        assert not r.is_error

    def test_is_error_when_error_set(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t", prompt="hi"
        )
        r = ProbeResult(probe=p, error="Timeout")
        assert r.is_error

    def test_is_error_when_no_status_code(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t", prompt="hi"
        )
        r = ProbeResult(probe=p)
        assert r.is_error

    def test_full_conversation_single_turn(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t", prompt="hello"
        )
        r = ProbeResult(probe=p, response="world")
        conv = r.full_conversation
        assert len(conv) == 2
        assert conv[0]["role"] == "user"
        assert conv[0]["content"] == "hello"
        assert conv[1]["role"] == "assistant"
        assert conv[1]["content"] == "world"

    def test_full_conversation_multi_turn(self):
        p = Probe(
            id="t", domain=Domain.SAFETY, category="test", description="t",
            prompt="q1", follow_ups=["q2", "q3"],
        )
        r = ProbeResult(probe=p, response="a1", follow_up_responses=["a2"])
        conv = r.full_conversation
        assert len(conv) == 5  # q1, a1, q2, a2, q3


class TestDomainScore:
    def test_grade_a(self):
        ds = DomainScore(domain=Domain.SAFETY, score=95)
        assert ds.grade == "A"

    def test_grade_f(self):
        ds = DomainScore(domain=Domain.SAFETY, score=30)
        assert ds.grade == "F"

    def test_status_icon_green(self):
        ds = DomainScore(domain=Domain.SAFETY, score=85)
        assert ds.status_icon == "✅"

    def test_status_icon_red(self):
        ds = DomainScore(domain=Domain.SAFETY, score=45)
        assert ds.status_icon == "❌"


class TestScanResult:
    def test_to_dict(self):
        ds = DomainScore(domain=Domain.SAFETY, score=90)
        ds.passed = 5
        ds.failed = 1
        ds.total = 6
        sr = ScanResult(
            url="http://test.com",
            overall_score=90,
            domain_scores={"safety": ds},
            findings=[],
            duration_seconds=5.0,
            probes_run=6,
            timestamp="2026-01-01T00:00:00Z",
        )
        d = sr.to_dict()
        assert d["url"] == "http://test.com"
        assert d["overall_score"] == 90
        assert d["grade"] == "A"
        assert "safety" in d["domains"]

    def test_critical_and_warning_counts(self):
        f1 = Finding(
            probe_id="x", domain=Domain.SAFETY, category="c",
            severity=Severity.CRITICAL, verdict=Verdict.FAIL,
            title="t", detail="d", evidence="e",
        )
        f2 = Finding(
            probe_id="y", domain=Domain.SAFETY, category="c",
            severity=Severity.WARNING, verdict=Verdict.FAIL,
            title="t", detail="d", evidence="e",
        )
        sr = ScanResult(
            url="http://test.com", overall_score=70,
            domain_scores={}, findings=[f1, f2],
            duration_seconds=1.0, probes_run=2, timestamp="",
        )
        assert sr.critical_count == 1
        assert sr.warning_count == 1
