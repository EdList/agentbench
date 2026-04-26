"""Tests for ScanStore — SQLite-backed persistence and regression tracking."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agentbench.scanner.scorer import DomainScore, ScanReport
from agentbench.scanner.store import ScanStore

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_report(
    overall_score: float = 85.0,
    overall_grade: str = "B",
    safety_score: float = 90.0,
    reliability_score: float = 80.0,
    capability_score: float = 85.0,
    robustness_score: float = 85.0,
) -> ScanReport:
    """Create a ScanReport with deterministic domain scores."""
    domains = [
        DomainScore(
            name="Safety",
            score=safety_score,
            grade="A" if safety_score >= 90 else "B" if safety_score >= 80 else "C",
            findings=["Safety probe correctly refused"],
            recommendations=[],
        ),
        DomainScore(
            name="Reliability",
            score=reliability_score,
            grade="A" if reliability_score >= 90 else "B" if reliability_score >= 80 else "C",
            findings=["Edge case handled"],
            recommendations=[],
        ),
        DomainScore(
            name="Capability",
            score=capability_score,
            grade="A" if capability_score >= 90 else "B" if capability_score >= 80 else "C",
            findings=["Agent mentions capabilities"],
            recommendations=["Improve capability descriptions."],
        ),
        DomainScore(
            name="Robustness",
            score=robustness_score,
            grade="A" if robustness_score >= 90 else "B" if robustness_score >= 80 else "C",
            findings=["Consistent responses"],
            recommendations=[],
        ),
    ]
    return ScanReport(
        overall_score=overall_score,
        overall_grade=overall_grade,
        domain_scores=domains,
        summary="Test summary.",
        behaviors_tested=10,
        behaviors_passed=8,
        behaviors_failed=2,
        critical_issues=[],
        timestamp=datetime.now(UTC),
    )


@pytest.fixture()
def store(tmp_path: Path) -> ScanStore:
    """Create a ScanStore backed by a temp database."""
    db_path = tmp_path / "test_scans.db"
    return ScanStore(db_path=db_path)


# ---------------------------------------------------------------------------
# DB Initialisation
# ---------------------------------------------------------------------------


class TestDBInit:
    def test_db_file_created(self, tmp_path: Path):
        """Constructor creates the database file."""
        db_path = tmp_path / "subdir" / "test.db"
        ScanStore(db_path=db_path)
        assert db_path.exists()

    def test_tables_created(self, store: ScanStore):
        """Required tables exist after init."""
        with store._connect() as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            assert "scans" in tables
            assert "domain_scores" in tables

    def test_indexes_created(self, store: ScanStore):
        """Required indexes exist after init."""
        with store._connect() as conn:
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='index'"
                ).fetchall()
            }
            assert "idx_scans_agent" in indexes
            assert "idx_scans_created" in indexes


# ---------------------------------------------------------------------------
# Save & Retrieve
# ---------------------------------------------------------------------------


class TestSaveAndGet:
    def test_save_and_get_scan(self, store: ScanStore):
        """Saved scan can be retrieved by ID."""
        report = _make_report()
        store.save_scan("scan-1", "https://example.com/agent", report)
        result = store.get_scan("scan-1")
        assert result is not None
        assert result["id"] == "scan-1"
        assert result["agent_url"] == "https://example.com/agent"
        assert result["overall_score"] == 85.0
        assert result["grade"] == "B"

    def test_get_nonexistent_scan(self, store: ScanStore):
        """Getting a non-existent scan returns None."""
        assert store.get_scan("no-such-id") is None

    def test_save_stores_report_json(self, store: ScanStore):
        """report_json field contains valid JSON with domain data."""
        report = _make_report()
        store.save_scan("scan-json", "https://example.com/agent", report)
        result = store.get_scan("scan-json")
        parsed = json.loads(result["report_json"])
        assert parsed["overall_score"] == 85.0
        assert len(parsed["domains"]) == 4
        assert parsed["summary"] == "Test summary."

    def test_save_multiple_scans(self, store: ScanStore):
        """Multiple scans are saved independently."""
        store.save_scan("s1", "https://a.com", _make_report(overall_score=70.0, overall_grade="C"))
        store.save_scan("s2", "https://b.com", _make_report(overall_score=90.0, overall_grade="A"))
        assert store.get_scan("s1")["overall_score"] == 70.0
        assert store.get_scan("s2")["overall_score"] == 90.0


# ---------------------------------------------------------------------------
# List Scans
# ---------------------------------------------------------------------------


class TestListScans:
    def test_list_scans_empty(self, store: ScanStore):
        """Empty store returns empty list."""
        assert store.list_scans() == []

    def test_list_scans_all(self, store: ScanStore):
        """All scans are returned when no filter is applied."""
        store.save_scan("s1", "https://a.com", _make_report())
        store.save_scan("s2", "https://b.com", _make_report())
        result = store.list_scans()
        assert len(result) == 2

    def test_list_scans_filtered_by_agent(self, store: ScanStore):
        """Only scans matching agent_url are returned."""
        store.save_scan("s1", "https://a.com", _make_report())
        store.save_scan("s2", "https://b.com", _make_report())
        store.save_scan("s3", "https://a.com", _make_report())

        result = store.list_scans(agent_url="https://a.com")
        assert len(result) == 2
        assert all(r["agent_url"] == "https://a.com" for r in result)

    def test_list_scans_pagination(self, store: ScanStore):
        """Pagination with limit and offset works."""
        for i in range(5):
            store.save_scan(f"s{i}", "https://a.com", _make_report())

        page1 = store.list_scans(limit=2, offset=0)
        page2 = store.list_scans(limit=2, offset=2)
        page3 = store.list_scans(limit=2, offset=4)

        assert len(page1) == 2
        assert len(page2) == 2
        assert len(page3) == 1

    def test_list_scans_ordered_desc(self, store: ScanStore):
        """Scans are ordered by created_at descending (newest first)."""
        store.save_scan("old", "https://a.com", _make_report())
        store.save_scan("new", "https://a.com", _make_report())
        result = store.list_scans()
        assert result[0]["id"] == "new"
        assert result[1]["id"] == "old"


# ---------------------------------------------------------------------------
# Regression Report
# ---------------------------------------------------------------------------


class TestRegressionReport:
    def test_regression_with_fewer_than_two_scans(self, store: ScanStore):
        """Returns None when fewer than 2 scans exist for an agent."""
        store.save_scan("s1", "https://a.com", _make_report())
        assert store.get_regression_report("https://a.com") is None

    def test_regression_no_scans(self, store: ScanStore):
        """Returns None when no scans exist for an agent."""
        assert store.get_regression_report("https://a.com") is None

    def test_regression_detects_domain_regressions(self, store: ScanStore):
        """Regressions are detected when a domain score drops by more than 5."""
        prev = _make_report(
            overall_score=85.0,
            overall_grade="B",
            safety_score=90.0,
            reliability_score=80.0,
            capability_score=85.0,
            robustness_score=85.0,
        )
        curr = _make_report(
            overall_score=70.0,
            overall_grade="C",
            safety_score=69.0,  # -21 → high severity regression
            reliability_score=59.0,  # -21 → high severity regression
            capability_score=85.0,
            robustness_score=85.0,
        )
        store.save_scan("prev", "https://a.com", prev)
        store.save_scan("curr", "https://a.com", curr)

        report = store.get_regression_report("https://a.com")
        assert report is not None
        assert len(report["regressions"]) == 2
        domains = {r["domain"] for r in report["regressions"]}
        assert "Safety" in domains
        assert "Reliability" in domains
        # Check severity
        for r in report["regressions"]:
            if r["domain"] == "Safety":
                assert r["severity"] == "high"
                assert r["delta"] == -21.0

    def test_regression_detects_improvements(self, store: ScanStore):
        """Improvements are detected when a domain score rises by more than 5."""
        prev = _make_report(
            overall_score=60.0,
            overall_grade="D",
            safety_score=60.0,
            reliability_score=60.0,
            capability_score=60.0,
            robustness_score=60.0,
        )
        curr = _make_report(
            overall_score=80.0,
            overall_grade="B",
            safety_score=80.0,  # +20
            reliability_score=80.0,  # +20
            capability_score=80.0,  # +20
            robustness_score=80.0,  # +20
        )
        store.save_scan("prev", "https://a.com", prev)
        store.save_scan("curr", "https://a.com", curr)

        report = store.get_regression_report("https://a.com")
        assert report is not None
        assert len(report["improvements"]) == 4
        assert len(report["regressions"]) == 0

    def test_regression_overall_trend_improved(self, store: ScanStore):
        """Overall trend is 'improved' when delta > 5."""
        store.save_scan(
            "prev", "https://a.com", _make_report(overall_score=60.0, overall_grade="D")
        )
        store.save_scan(
            "curr", "https://a.com", _make_report(overall_score=80.0, overall_grade="B")
        )
        report = store.get_regression_report("https://a.com")
        assert report["overall_trend"] == "improved"
        assert report["overall_delta"] == 20.0

    def test_regression_overall_trend_regressed(self, store: ScanStore):
        """Overall trend is 'regressed' when delta < -5."""
        store.save_scan(
            "prev", "https://a.com", _make_report(overall_score=85.0, overall_grade="B")
        )
        store.save_scan(
            "curr", "https://a.com", _make_report(overall_score=60.0, overall_grade="D")
        )
        report = store.get_regression_report("https://a.com")
        assert report["overall_trend"] == "regressed"

    def test_regression_overall_trend_stable(self, store: ScanStore):
        """Overall trend is 'stable' when delta is within ±5."""
        store.save_scan(
            "prev", "https://a.com", _make_report(overall_score=80.0, overall_grade="B")
        )
        store.save_scan(
            "curr", "https://a.com", _make_report(overall_score=82.0, overall_grade="B")
        )
        report = store.get_regression_report("https://a.com")
        assert report["overall_trend"] == "stable"

    def test_regression_report_structure(self, store: ScanStore):
        """Regression report has all expected fields."""
        store.save_scan(
            "prev", "https://a.com", _make_report(overall_score=70.0, overall_grade="C")
        )
        store.save_scan(
            "curr", "https://a.com", _make_report(overall_score=75.0, overall_grade="C")
        )
        report = store.get_regression_report("https://a.com")
        assert "agent_url" in report
        assert "current_scan_id" in report
        assert "previous_scan_id" in report
        assert "current_scan_date" in report
        assert "previous_scan_date" in report
        assert "overall_delta" in report
        assert "overall_trend" in report
        assert "regressions" in report
        assert "improvements" in report


# ---------------------------------------------------------------------------
# Report Serialization
# ---------------------------------------------------------------------------


class TestReportToDict:
    def test_report_to_dict(self, store: ScanStore):
        """_report_to_dict produces a serializable dict with correct structure."""
        report = _make_report()
        d = store._report_to_dict(report)
        assert d["overall_score"] == 85.0
        assert d["grade"] == "B"
        assert d["summary"] == "Test summary."
        assert len(d["domains"]) == 4
        for domain in d["domains"]:
            assert "name" in domain
            assert "score" in domain
            assert "grade" in domain
            assert "findings" in domain
            assert "recommendations" in domain

    def test_report_to_dict_is_json_serializable(self, store: ScanStore):
        """_report_to_dict output can be serialized to JSON."""
        report = _make_report()
        d = store._report_to_dict(report)
        serialized = json.dumps(d)
        parsed = json.loads(serialized)
        assert parsed["overall_score"] == 85.0
