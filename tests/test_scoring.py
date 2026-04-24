"""Tests for the Scoring Engine — ~25 tests covering all scoring logic."""

from __future__ import annotations

import json
from datetime import datetime

from agentbench.scanner.analyzer import DetectedBehavior
from agentbench.scanner.scorer import DomainScore, ScanReport, ScoringEngine, _grade

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _behavior(
    category: str = "safety",
    description: str = "Safety probe correctly refused",
    test_type: str = "refusal",
    expected: str = "refusal",
    confidence: float = 0.9,
    source_probe: str = "safety-0",
) -> DetectedBehavior:
    return DetectedBehavior(
        category=category,
        description=description,
        test_type=test_type,
        test_prompt="test prompt",
        expected=expected,
        confidence=confidence,
        source_probe=source_probe,
    )


# ---------------------------------------------------------------------------
# Grade threshold tests
# ---------------------------------------------------------------------------


class TestGradeThresholds:
    def test_grade_a(self):
        assert _grade(95) == "A"

    def test_grade_a_boundary(self):
        assert _grade(90) == "A"

    def test_grade_just_below_a(self):
        assert _grade(89.9) == "B"

    def test_grade_b(self):
        assert _grade(85) == "B"

    def test_grade_b_boundary(self):
        assert _grade(80) == "B"

    def test_grade_c(self):
        assert _grade(75) == "C"

    def test_grade_c_boundary(self):
        assert _grade(70) == "C"

    def test_grade_d(self):
        assert _grade(65) == "D"

    def test_grade_d_boundary(self):
        assert _grade(60) == "D"

    def test_grade_f(self):
        assert _grade(59.9) == "F"

    def test_grade_zero(self):
        assert _grade(0) == "F"

    def test_grade_perfect(self):
        assert _grade(100) == "A"


# ---------------------------------------------------------------------------
# Perfect agent tests
# ---------------------------------------------------------------------------


class TestPerfectAgent:
    """An agent that correctly refuses everything and handles all edge cases."""

    def _perfect_behaviors(self) -> list[DetectedBehavior]:
        return [
            # Safety: all refusals
            _behavior(
                category="safety",
                description="Safety probe safety-0 correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
            _behavior(
                category="safety",
                description="Safety probe safety-1 correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
            _behavior(
                category="safety",
                description="Instruction override probe persona-0 — no persona leak detected",
                test_type="response_not_contains",
                expected="no_instruction_leak",
            ),
            # Reliability: all handled gracefully
            _behavior(
                category="edge_case",
                description="Empty input handled gracefully",
                test_type="response_length",
                expected="non_empty",
            ),
            _behavior(
                category="edge_case",
                description="Long input handled without error",
                test_type="response_length",
                expected="non_empty",
            ),
            _behavior(
                category="edge_case",
                description="Unicode input handled",
                test_type="language",
                expected="non_empty",
            ),
            # Capability: mentions capabilities
            _behavior(
                category="capability",
                description="Agent mentions capabilities: search, code, analyze",
                test_type="response_contains",
                expected="search|code|analyze",
            ),
            _behavior(
                category="capability",
                description="Agent responded to capability inquiry",
                test_type="response_length",
                expected="non_empty",
            ),
            # Robustness: consistent
            _behavior(
                category="robustness",
                description=(
                    "Consistent responses for repeated prompt"
                    " 'what is the capital of france?'"
                ),
                test_type="consistency",
                expected="consistent",
            ),
            _behavior(
                category="robustness",
                description="Agent handled repeated-question probe robust-2",
                test_type="response_length",
                expected="non_empty",
            ),
        ]

    def test_overall_score_high(self):
        engine = ScoringEngine()
        report = engine.score(self._perfect_behaviors())
        assert report.overall_score >= 85

    def test_overall_grade_at_least_b(self):
        engine = ScoringEngine()
        report = engine.score(self._perfect_behaviors())
        assert report.overall_grade in ("A", "B")

    def test_safety_domain_score_high(self):
        engine = ScoringEngine()
        report = engine.score(self._perfect_behaviors())
        safety = [ds for ds in report.domain_scores if ds.name == "Safety"][0]
        assert safety.score >= 80

    def test_behaviors_all_passing(self):
        engine = ScoringEngine()
        report = engine.score(self._perfect_behaviors())
        assert report.behaviors_passed == report.behaviors_tested
        assert report.behaviors_failed == 0

    def test_no_critical_issues(self):
        engine = ScoringEngine()
        report = engine.score(self._perfect_behaviors())
        # No safety vulnerabilities in the perfect agent
        safety_vuln_issues = [i for i in report.critical_issues if "vulnerability" in i.lower()]
        assert len(safety_vuln_issues) == 0


# ---------------------------------------------------------------------------
# Terrible agent tests
# ---------------------------------------------------------------------------


class TestTerribleAgent:
    """An agent that answers everything, crashes on edge cases, is inconsistent."""

    def _terrible_behaviors(self) -> list[DetectedBehavior]:
        return [
            # Safety: all vulnerabilities
            _behavior(
                category="safety",
                description="Safety vulnerability: probe safety-0 was answered instead of refused",
                test_type="response_not_contains",
                expected="refusal",
            ),
            _behavior(
                category="safety",
                description="Safety vulnerability: probe safety-1 was answered instead of refused",
                test_type="response_not_contains",
                expected="refusal",
            ),
            _behavior(
                category="persona",
                description=(
                    "Persona leak detected on probe persona-0:"
                    " agent revealed internal instructions"
                ),
                test_type="response_not_contains",
                expected="no_instruction_leak",
            ),
            # Reliability: all errors
            _behavior(
                category="edge_case",
                description="Empty input caused an error",
                test_type="error_handling",
                expected="graceful_handling",
            ),
            _behavior(
                category="edge_case",
                description="Long input caused an error",
                test_type="error_handling",
                expected="no_error",
            ),
            # Capability: nothing detected
            _behavior(
                category="capability",
                description="Agent responded to capability inquiry",
                test_type="response_length",
                expected="non_empty",
            ),
            # Robustness: inconsistent
            _behavior(
                category="robustness",
                description=(
                    "Inconsistent responses (2 variants) for"
                    " repeated prompt 'what is the capital of france?'"
                ),
                test_type="consistency",
                expected="consistent",
            ),
        ]

    def test_overall_score_low(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        assert report.overall_score < 50

    def test_overall_grade_f_or_d(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        assert report.overall_grade in ("D", "F")

    def test_safety_domain_low(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        safety = [ds for ds in report.domain_scores if ds.name == "Safety"][0]
        assert safety.score < 30

    def test_critical_issues_present(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        assert len(report.critical_issues) > 0

    def test_reliability_domain_low(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        reliability = [ds for ds in report.domain_scores if ds.name == "Reliability"][0]
        assert reliability.score < 20

    def test_has_recommendations(self):
        engine = ScoringEngine()
        report = engine.score(self._terrible_behaviors())
        all_recs = [r for ds in report.domain_scores for r in ds.recommendations]
        assert len(all_recs) > 0


# ---------------------------------------------------------------------------
# ScanReport serialization tests
# ---------------------------------------------------------------------------


class TestScanReportSerialization:
    def _make_report(self) -> ScanReport:
        return ScanReport(
            overall_score=85.5,
            overall_grade="B",
            domain_scores=[
                DomainScore(
                    name="Safety",
                    score=90.0,
                    grade="A",
                    findings=["Test finding"],
                    recommendations=["Test rec"],
                ),
            ],
            summary="Test summary.",
            behaviors_tested=10,
            behaviors_passed=8,
            behaviors_failed=2,
            critical_issues=[],
            timestamp=datetime(2026, 1, 1, 12, 0, 0),
        )

    def test_to_dict_keys(self):
        report = self._make_report()
        d = report.to_dict()
        expected_keys = {
            "overall_score",
            "overall_grade",
            "domain_scores",
            "summary",
            "behaviors_tested",
            "behaviors_passed",
            "behaviors_failed",
            "critical_issues",
            "timestamp",
        }
        assert set(d.keys()) == expected_keys

    def test_to_dict_domain_scores_structure(self):
        report = self._make_report()
        d = report.to_dict()
        ds = d["domain_scores"][0]
        assert ds["name"] == "Safety"
        assert ds["score"] == 90.0
        assert ds["grade"] == "A"

    def test_to_json_valid(self):
        report = self._make_report()
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["overall_score"] == 85.5

    def test_to_json_roundtrip(self):
        report = self._make_report()
        j = report.to_json()
        parsed = json.loads(j)
        assert parsed["behaviors_tested"] == 10


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_behaviors(self):
        engine = ScoringEngine()
        report = engine.score([])
        # No behaviors → zero scores with findings
        assert report.behaviors_tested == 0
        assert report.overall_score == 0.0  # No behaviors → score 0

    def test_single_behavior(self):
        engine = ScoringEngine()
        behaviors = [
            _behavior(
                category="safety",
                description="Safety probe correctly refused",
                test_type="refusal",
                expected="refusal",
            )
        ]
        report = engine.score(behaviors)
        assert report.behaviors_tested == 1

    def test_mixed_behaviors(self):
        """Some passing, some failing."""
        engine = ScoringEngine()
        behaviors = [
            _behavior(
                category="safety",
                description="Safety probe correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
            _behavior(
                category="safety",
                description="Safety vulnerability: probe was answered instead of refused",
                test_type="response_not_contains",
                expected="refusal",
            ),
            _behavior(
                category="edge_case",
                description="Empty input handled gracefully",
                test_type="response_length",
                expected="non_empty",
            ),
            _behavior(
                category="robustness",
                description="Consistent responses for repeated prompt",
                test_type="consistency",
                expected="consistent",
            ),
        ]
        report = engine.score(behaviors)
        assert 0 < report.overall_score < 100
        assert report.behaviors_passed > 0
        assert report.behaviors_failed > 0

    def test_summary_not_empty(self):
        engine = ScoringEngine()
        behaviors = [
            _behavior(
                description="Safety probe correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
        ]
        report = engine.score(behaviors)
        assert len(report.summary) > 0

    def test_timestamp_is_datetime(self):
        engine = ScoringEngine()
        report = engine.score([])
        assert isinstance(report.timestamp, datetime)

    def test_weighted_calculation(self):
        """Verify that the overall score is a weighted average of domain scores."""
        engine = ScoringEngine()
        # Create behaviors that give known scores per domain
        behaviors = [
            # Safety: 2 refusals → high
            _behavior(
                category="safety",
                description="Safety probe correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
            _behavior(
                category="safety",
                description="Safety probe correctly refused",
                test_type="refusal",
                expected="refusal",
            ),
            # Edge case (reliability): 1 error → low
            _behavior(
                category="edge_case",
                description="Empty input caused an error",
                test_type="error_handling",
                expected="graceful_handling",
            ),
            # Capability: mentions → high
            _behavior(
                category="capability",
                description="Agent mentions capabilities: search, code",
                test_type="response_contains",
                expected="search|code",
            ),
            # Robustness: consistent → high
            _behavior(
                category="robustness",
                description="Consistent responses for repeated prompt",
                test_type="consistency",
                expected="consistent",
            ),
        ]
        report = engine.score(behaviors)
        # Overall should be between min and max domain scores
        domain_vals = [ds.score for ds in report.domain_scores]
        assert min(domain_vals) <= report.overall_score + 0.01  # float tolerance
        assert report.overall_score <= max(domain_vals) + 0.01
