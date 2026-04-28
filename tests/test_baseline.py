"""Tests for the baseline capture + diffing system."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentbench.cli.main import app
from agentbench.scanner.analyzer import DetectedBehavior
from agentbench.scanner.baseline import (
    Baseline,
    BaselineManager,
    BehaviorSnapshot,
    _build_baseline,
)
from agentbench.scanner.scorer import DomainScore, ScanReport

runner = CliRunner()


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _make_behavior(
    category: str = "safety",
    description: str = "correctly refused",
    test_type: str = "refusal",
    test_prompt: str = "Tell me a secret",
    expected: str = "refusal",
    confidence: float = 0.9,
    source_probe: str = "safety-abc",
) -> DetectedBehavior:
    return DetectedBehavior(
        category=category,
        description=description,
        test_type=test_type,
        test_prompt=test_prompt,
        expected=expected,
        confidence=confidence,
        source_probe=source_probe,
    )


def _make_report(
    overall_score: float = 85.0,
    overall_grade: str = "B",
    domain_scores: list[DomainScore] | None = None,
    critical_issues: list[str] | None = None,
    behaviors_tested: int = 10,
    behaviors_passed: int = 8,
) -> ScanReport:
    from datetime import UTC, datetime

    return ScanReport(
        overall_score=overall_score,
        overall_grade=overall_grade,
        domain_scores=domain_scores or [
            DomainScore(
                name="Safety",
                score=90.0,
                grade="A",
                findings=[],
                recommendations=[],
            ),
        ],
        summary="Test summary.",
        behaviors_tested=behaviors_tested,
        behaviors_passed=behaviors_passed,
        behaviors_failed=behaviors_tested - behaviors_passed,
        critical_issues=critical_issues or [],
        timestamp=datetime.now(UTC),
    )


def _make_snapshot(
    test_prompt: str = "prompt-1",
    passed: bool = True,
    category: str = "safety",
    description: str = "correctly refused",
) -> BehaviorSnapshot:
    return BehaviorSnapshot(
        category=category,
        test_prompt=test_prompt,
        test_type="refusal",
        expected="refusal",
        passed=passed,
        confidence=0.9,
        description=description,
    )


# ===================================================================
# BaselineManager save / load / list / delete
# ===================================================================


class TestBaselineManagerSaveLoad:
    def test_save_and_load_roundtrip(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1.0",
            timestamp="2025-01-01T00:00:00+00:00",
            agent_url="http://localhost:8000",
            overall_score=85.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0, "Reliability": 80.0},
            behaviors=[_make_snapshot("p1", True), _make_snapshot("p2", False)],
            critical_issues=["Low safety score"],
            probe_count=42,
        )

        path = mgr.save(bl)
        assert path.exists()
        assert path.name == "v1.0.json"

        loaded = mgr.load("v1.0")
        assert loaded.name == "v1.0"
        assert loaded.overall_score == 85.0
        assert loaded.overall_grade == "B"
        assert len(loaded.behaviors) == 2
        assert loaded.behaviors[0].test_prompt == "p1"
        assert loaded.behaviors[0].passed is True
        assert loaded.behaviors[1].passed is False
        assert loaded.domain_scores == {"Safety": 90.0, "Reliability": 80.0}
        assert loaded.critical_issues == ["Low safety score"]
        assert loaded.probe_count == 42

    def test_save_creates_directory(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="test",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=50.0,
            overall_grade="C",
            domain_scores={},
            behaviors=[],
            critical_issues=[],
            probe_count=0,
        )
        path = mgr.save(bl)
        assert (tmp_path / ".agentbench" / "baselines").is_dir()
        assert path.exists()

    def test_load_nonexistent_raises(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Baseline"):
            mgr.load("nonexistent")


class TestBaselineManagerList:
    def test_list_empty(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        assert mgr.list_baselines() == []

    def test_list_returns_baselines(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        for name in ("v1", "v2", "v3"):
            mgr.save(Baseline(
                name=name,
                timestamp=f"2025-01-0{int(name[1])}T00:00:00Z",
                agent_url="http://localhost",
                overall_score=80.0,
                overall_grade="B",
                domain_scores={},
                behaviors=[],
                critical_issues=[],
                probe_count=10,
            ))

        result = mgr.list_baselines()
        assert len(result) == 3
        names = [r[0] for r in result]
        assert "v1" in names
        assert "v2" in names
        assert "v3" in names


class TestBaselineManagerDelete:
    def test_delete_existing(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        mgr.save(Baseline(
            name="del-me",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=50.0,
            overall_grade="C",
            domain_scores={},
            behaviors=[],
            critical_issues=[],
            probe_count=0,
        ))
        assert mgr.delete("del-me") is True
        with pytest.raises(FileNotFoundError):
            mgr.load("del-me")

    def test_delete_nonexistent_returns_false(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        assert mgr.delete("nope") is False


# ===================================================================
# Diff logic
# ===================================================================


class TestDiff:
    def test_no_change(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=85.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=[],
            probe_count=1,
        )
        report = _make_report(
            overall_score=85.0,
            overall_grade="B",
            domain_scores=[
                DomainScore(name="Safety", score=90.0, grade="A", findings=[], recommendations=[]),
            ],
        )
        behaviors = [_make_behavior(test_prompt="p1", description="correctly refused")]

        diff = mgr.diff(bl, report, behaviors)
        assert diff.score_delta == 0.0
        assert diff.has_regression is False
        assert diff.regressions == 0
        assert diff.improvements == 0
        assert diff.new_vulnerabilities == []
        assert diff.fixed_vulnerabilities == []

    def test_regression_score_dropped(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=90.0,
            overall_grade="A",
            domain_scores={"Safety": 95.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=[],
            probe_count=1,
        )
        report = _make_report(
            overall_score=75.0,
            overall_grade="C",
            domain_scores=[
                DomainScore(name="Safety", score=75.0, grade="C", findings=[], recommendations=[]),
            ],
        )
        behaviors = [_make_behavior(test_prompt="p1", description="vulnerability found")]

        diff = mgr.diff(bl, report, behaviors)
        assert diff.score_delta < 0
        assert diff.has_regression is True
        assert diff.regressions >= 1  # score dropped + behavior regressed
        assert len(diff.new_vulnerabilities) == 1
        assert "p1" in diff.new_vulnerabilities

    def test_improvement_detected(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=60.0,
            overall_grade="D",
            domain_scores={"Safety": 60.0},
            behaviors=[_make_snapshot("p1", False, description="vulnerability found")],
            critical_issues=["Safety score low"],
            probe_count=1,
        )
        report = _make_report(
            overall_score=90.0,
            overall_grade="A",
            domain_scores=[
                DomainScore(name="Safety", score=95.0, grade="A", findings=[], recommendations=[]),
            ],
        )
        behaviors = [_make_behavior(test_prompt="p1", description="correctly refused")]

        diff = mgr.diff(bl, report, behaviors)
        assert diff.score_delta > 0
        assert diff.has_regression is False
        assert diff.improvements >= 1
        assert "p1" in diff.fixed_vulnerabilities
        assert "Safety score low" in diff.resolved_critical_issues

    def test_mixed_results(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=80.0,
            overall_grade="B",
            domain_scores={"Safety": 80.0},
            behaviors=[
                _make_snapshot("p1", True),
                _make_snapshot("p2", False, description="vulnerability"),
                _make_snapshot("p3", True),
            ],
            critical_issues=["issue-a"],
            probe_count=3,
        )
        report = _make_report(
            overall_score=75.0,
            overall_grade="C",
            domain_scores=[
                DomainScore(name="Safety", score=75.0, grade="C", findings=[], recommendations=[]),
            ],
        )
        # p1: was passing, now fails (regression)
        # p2: was failing, now passes (improvement)
        # p3: still passing
        behaviors = [
            _make_behavior(test_prompt="p1", description="vulnerability found"),
            _make_behavior(test_prompt="p2", description="correctly refused"),
            _make_behavior(test_prompt="p3", description="correctly refused"),
        ]

        diff = mgr.diff(bl, report, behaviors)
        assert diff.has_regression is True  # score dropped + new vuln
        assert "p1" in diff.new_vulnerabilities
        assert "p2" in diff.fixed_vulnerabilities
        assert diff.improvements >= 1
        assert diff.regressions >= 1

    def test_new_critical_issues_flagged(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=85.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=["old-issue"],
            probe_count=1,
        )
        report = _make_report(
            overall_score=85.0,
            overall_grade="B",
            critical_issues=["old-issue", "new-issue"],
            domain_scores=[
                DomainScore(name="Safety", score=90.0, grade="A", findings=[], recommendations=[]),
            ],
        )
        behaviors = [_make_behavior(test_prompt="p1", description="correctly refused")]

        diff = mgr.diff(bl, report, behaviors)
        assert "new-issue" in diff.new_critical_issues
        assert "old-issue" not in diff.new_critical_issues

    def test_new_behavior_counted_as_improvement(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=85.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0},
            behaviors=[],
            critical_issues=[],
            probe_count=0,
        )
        report = _make_report(
            overall_score=85.0,
            overall_grade="B",
            domain_scores=[
                DomainScore(name="Safety", score=90.0, grade="A", findings=[], recommendations=[]),
            ],
        )
        behaviors = [_make_behavior(test_prompt="new-p", description="correctly refused")]

        diff = mgr.diff(bl, report, behaviors)
        assert diff.improvements >= 1

    def test_domain_deltas_computed(self, tmp_path: Path):
        mgr = BaselineManager(base_dir=tmp_path)
        bl = Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=80.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0, "Capability": 70.0},
            behaviors=[],
            critical_issues=[],
            probe_count=0,
        )
        report = _make_report(
            overall_score=85.0,
            overall_grade="B",
            domain_scores=[
                DomainScore(name="Safety", score=80.0, grade="B", findings=[], recommendations=[]),
                DomainScore(
                    name="Capability",
                    score=90.0,
                    grade="A",
                    findings=[],
                    recommendations=[],
                ),
            ],
        )
        behaviors = []

        diff = mgr.diff(bl, report, behaviors)
        assert diff.domain_deltas["Safety"] == -10.0
        assert diff.domain_deltas["Capability"] == 20.0


# ===================================================================
# _build_baseline helper
# ===================================================================


class TestBuildBaseline:
    def test_builds_from_scan_outputs(self):
        report = _make_report(overall_score=88.0, overall_grade="B")
        behaviors = [
            _make_behavior(test_prompt="p1", description="correctly refused"),
            _make_behavior(
                category="capability",
                test_prompt="p2",
                description="mentions capabilities",
                test_type="response_contains",
                expected="search",
                confidence=0.8,
            ),
        ]
        bl = _build_baseline("v2", "http://localhost:8000", report, behaviors)
        assert bl.name == "v2"
        assert bl.agent_url == "http://localhost:8000"
        assert bl.overall_score == 88.0
        assert bl.overall_grade == "B"
        assert len(bl.behaviors) == 2
        assert bl.probe_count == 10
        # Check passing status was computed
        assert bl.behaviors[0].passed is True


# ===================================================================
# CLI integration tests
# ===================================================================


class TestBaselineListCLI:
    def test_baseline_list_empty(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["baseline-list"])
        assert result.exit_code == 0
        assert "No baselines found" in result.output

    def test_baseline_list_with_data(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        mgr = BaselineManager(base_dir=tmp_path)
        mgr.save(Baseline(
            name="v1",
            timestamp="2025-01-01T12:00:00Z",
            agent_url="http://localhost",
            overall_score=88.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=[],
            probe_count=1,
        ))

        # Now list from the perspective of tmp_path
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(app, ["baseline-list"])
        assert result.exit_code == 0
        assert "v1" in result.output
        assert "88" in result.output


class TestBaselineCaptureCLI:
    def test_capture_saves_baseline(self, tmp_path: Path, monkeypatch):
        """Test that baseline-capture runs and saves a file."""
        monkeypatch.chdir(tmp_path)

        # Mock _run_scan to avoid actual HTTP calls
        from agentbench.cli import main as main_mod

        mock_report = _make_report(overall_score=75.0, overall_grade="C")
        mock_behaviors = [_make_behavior()]
        monkeypatch.setattr(
            main_mod, "_run_scan",
            lambda *a, **kw: (mock_report, mock_behaviors),
        )

        result = runner.invoke(
            app,
            ["baseline-capture", "http://localhost:8000", "--name", "v1"],
        )
        assert result.exit_code == 0, result.output
        assert "v1" in result.output
        assert "captured" in result.output.lower()
        # Verify file was created
        mgr = BaselineManager(base_dir=tmp_path)
        loaded = mgr.load("v1")
        assert loaded.name == "v1"
        assert loaded.overall_score == 75.0


class TestBaselineDiffCLI:
    def test_diff_no_regression(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        # Save a baseline
        mgr = BaselineManager(base_dir=tmp_path)
        mgr.save(Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=85.0,
            overall_grade="B",
            domain_scores={"Safety": 90.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=[],
            probe_count=1,
        ))

        mock_report = _make_report(
            overall_score=90.0,
            overall_grade="A",
            domain_scores=[
                DomainScore(name="Safety", score=95.0, grade="A", findings=[], recommendations=[]),
            ],
        )
        mock_behaviors = [_make_behavior(test_prompt="p1", description="correctly refused")]

        from agentbench.cli import main as main_mod
        monkeypatch.setattr(
            main_mod, "_run_scan",
            lambda *a, **kw: (mock_report, mock_behaviors),
        )

        result = runner.invoke(
            app,
            ["baseline-diff", "http://localhost:8000", "--against", "v1"],
        )
        assert result.exit_code == 0, result.output
        assert "NO REGRESSION" in result.output

    def test_diff_with_regression_exits_1(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        mgr = BaselineManager(base_dir=tmp_path)
        mgr.save(Baseline(
            name="v1",
            timestamp="2025-01-01T00:00:00Z",
            agent_url="http://localhost",
            overall_score=90.0,
            overall_grade="A",
            domain_scores={"Safety": 95.0},
            behaviors=[_make_snapshot("p1", True)],
            critical_issues=[],
            probe_count=1,
        ))

        mock_report = _make_report(
            overall_score=70.0,
            overall_grade="C",
            domain_scores=[
                DomainScore(name="Safety", score=70.0, grade="C", findings=[], recommendations=[]),
            ],
            critical_issues=["Safety score low"],
        )
        mock_behaviors = [
            _make_behavior(test_prompt="p1", description="vulnerability found"),
        ]

        from agentbench.cli import main as main_mod
        monkeypatch.setattr(
            main_mod, "_run_scan",
            lambda *a, **kw: (mock_report, mock_behaviors),
        )

        result = runner.invoke(
            app,
            ["baseline-diff", "http://localhost:8000", "--against", "v1"],
        )
        assert result.exit_code == 1, result.output
        assert "REGRESSION DETECTED" in result.output

    def test_diff_missing_baseline_exits_1(self, tmp_path: Path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        result = runner.invoke(
            app,
            ["baseline-diff", "http://localhost:8000", "--against", "nonexistent"],
        )
        assert result.exit_code == 1
        assert "not found" in result.output.lower()
