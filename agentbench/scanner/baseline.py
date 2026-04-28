"""Baseline capture and diffing for regression testing.

Provides ``BaselineManager`` for saving / loading / comparing scan results
against a previously captured baseline.  The CLI commands
``baseline-capture``, ``baseline-diff``, and ``baseline-list`` delegate to
this module.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbench.scanner.analyzer import DetectedBehavior
from agentbench.scanner.scorer import ScanReport, ScoringEngine

# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

_BASELINES_DIR = Path(".agentbench/baselines")


@dataclass
class BehaviorSnapshot:
    """A single behavior result captured in a baseline."""

    category: str
    test_prompt: str  # used as the matching key
    test_type: str
    expected: str
    passed: bool
    confidence: float
    description: str


@dataclass
class Baseline:
    """A complete baseline snapshot."""

    name: str
    timestamp: str  # ISO format
    agent_url: str
    overall_score: float
    overall_grade: str
    domain_scores: dict[str, float]  # domain_name -> score
    behaviors: list[BehaviorSnapshot]
    critical_issues: list[str]
    probe_count: int


@dataclass
class BaselineDiff:
    """Result of comparing a new scan against a baseline."""

    baseline_name: str
    score_delta: float  # positive = improved, negative = regressed
    grade_changed: bool
    new_grade: str
    old_grade: str
    new_vulnerabilities: list[str]  # behaviors that passed before but fail now
    fixed_vulnerabilities: list[str]  # behaviors that failed before but pass now
    new_critical_issues: list[str]
    resolved_critical_issues: list[str]
    domain_deltas: dict[str, float]  # domain -> score change
    regressions: int  # total count of regressions
    improvements: int
    has_regression: bool  # True if any score dropped or new vulns appeared


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _behavior_to_snapshot(b: DetectedBehavior) -> BehaviorSnapshot:
    """Convert a ``DetectedBehavior`` into a ``BehaviorSnapshot``."""
    return BehaviorSnapshot(
        category=b.category,
        test_prompt=b.test_prompt,
        test_type=b.test_type,
        expected=b.expected,
        passed=ScoringEngine._is_passing(b),
        confidence=b.confidence,
        description=b.description,
    )


def _snapshot_from_dict(data: dict[str, Any]) -> BehaviorSnapshot:
    return BehaviorSnapshot(
        category=data["category"],
        test_prompt=data["test_prompt"],
        test_type=data["test_type"],
        expected=data["expected"],
        passed=data["passed"],
        confidence=data["confidence"],
        description=data["description"],
    )


def _snapshot_to_dict(snap: BehaviorSnapshot) -> dict[str, Any]:
    return asdict(snap)


def _baseline_from_dict(data: dict[str, Any]) -> Baseline:
    return Baseline(
        name=data["name"],
        timestamp=data["timestamp"],
        agent_url=data["agent_url"],
        overall_score=data["overall_score"],
        overall_grade=data["overall_grade"],
        domain_scores=data["domain_scores"],
        behaviors=[_snapshot_from_dict(b) for b in data["behaviors"]],
        critical_issues=data["critical_issues"],
        probe_count=data["probe_count"],
    )


def _baseline_to_dict(bl: Baseline) -> dict[str, Any]:
    d = asdict(bl)
    d["behaviors"] = [_snapshot_to_dict(b) for b in bl.behaviors]
    return d


def _build_baseline(
    name: str,
    agent_url: str,
    report: ScanReport,
    behaviors: list[DetectedBehavior],
) -> Baseline:
    """Create a ``Baseline`` from scan outputs."""
    domain_map: dict[str, float] = {ds.name: ds.score for ds in report.domain_scores}
    return Baseline(
        name=name,
        timestamp=datetime.now(UTC).isoformat(),
        agent_url=agent_url,
        overall_score=report.overall_score,
        overall_grade=report.overall_grade,
        domain_scores=domain_map,
        behaviors=[_behavior_to_snapshot(b) for b in behaviors],
        critical_issues=list(report.critical_issues),
        probe_count=report.behaviors_tested,
    )


# ---------------------------------------------------------------------------
# BaselineManager
# ---------------------------------------------------------------------------


class BaselineManager:
    """Save / load / compare scan result baselines.

    Baselines are stored as JSON files under ``.agentbench/baselines/``
    relative to *base_dir* (defaults to CWD).
    """

    def __init__(self, base_dir: Path | None = None) -> None:
        self._base = (base_dir or Path.cwd()).resolve()
        self._dir = self._base / _BASELINES_DIR

    # -- public API ----------------------------------------------------------

    def save(self, baseline: Baseline) -> Path:
        """Persist *baseline* to disk, creating the directory if needed."""
        self._dir.mkdir(parents=True, exist_ok=True)
        path = self._dir / f"{baseline.name}.json"
        path.write_text(json.dumps(_baseline_to_dict(baseline), indent=2))
        return path

    def load(self, name: str) -> Baseline:
        """Load a baseline by name.

        Raises ``FileNotFoundError`` if the baseline does not exist.
        """
        path = self._dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Baseline '{name}' not found at {path}")
        data = json.loads(path.read_text())
        return _baseline_from_dict(data)

    def list_baselines(self) -> list[tuple[str, str]]:
        """Return ``(name, timestamp)`` pairs for all saved baselines."""
        if not self._dir.exists():
            return []
        results: list[tuple[str, str]] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                results.append((data["name"], data["timestamp"]))
            except (json.JSONDecodeError, KeyError):
                continue
        return results

    def delete(self, name: str) -> bool:
        """Delete a baseline by name.  Returns ``True`` if deleted."""
        path = self._dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    def diff(
        self,
        baseline: Baseline,
        current_report: ScanReport,
        current_behaviors: list[DetectedBehavior],
    ) -> BaselineDiff:
        """Compare current scan results against a saved baseline."""
        # Index baseline behaviors by test_prompt
        baseline_by_prompt: dict[str, BehaviorSnapshot] = {
            b.test_prompt: b for b in baseline.behaviors
        }

        current_snaps: list[BehaviorSnapshot] = [
            _behavior_to_snapshot(b) for b in current_behaviors
        ]
        current_by_prompt: dict[str, BehaviorSnapshot] = {
            b.test_prompt: b for b in current_snaps
        }

        new_vulns: list[str] = []
        fixed_vulns: list[str] = []
        regressions = 0
        improvements = 0

        # Compare behaviors present in both baseline and current
        all_prompts = set(baseline_by_prompt.keys()) | set(current_by_prompt.keys())

        for prompt in all_prompts:
            bl_snap = baseline_by_prompt.get(prompt)
            cur_snap = current_by_prompt.get(prompt)

            if bl_snap is not None and cur_snap is not None:
                if bl_snap.passed and not cur_snap.passed:
                    new_vulns.append(prompt)
                    regressions += 1
                elif not bl_snap.passed and cur_snap.passed:
                    fixed_vulns.append(prompt)
                    improvements += 1
            elif bl_snap is not None and cur_snap is None:
                # Behavior was in baseline but missing now — treat as regression
                if bl_snap.passed:
                    new_vulns.append(prompt)
                    regressions += 1
            elif bl_snap is None and cur_snap is not None:
                # New behavior — regression if it fails
                if not cur_snap.passed:
                    new_vulns.append(prompt)
                    regressions += 1
                else:
                    improvements += 1

        # Score delta
        score_delta = round(current_report.overall_score - baseline.overall_score, 2)
        if score_delta < 0:
            regressions += 1  # overall score drop counts as regression

        # Grade change
        old_grade = baseline.overall_grade
        new_grade = current_report.overall_grade
        grade_changed = old_grade != new_grade

        # Critical issues
        bl_issues = set(baseline.critical_issues)
        cur_issues = set(current_report.critical_issues)
        new_critical = sorted(cur_issues - bl_issues)
        resolved_critical = sorted(bl_issues - cur_issues)

        # Domain deltas
        domain_deltas: dict[str, float] = {}
        for ds in current_report.domain_scores:
            old_score = baseline.domain_scores.get(ds.name, 0.0)
            domain_deltas[ds.name] = round(ds.score - old_score, 2)

        # Overall regression flag
        has_regression = (
            score_delta < 0
            or len(new_vulns) > 0
            or len(new_critical) > 0
        )

        return BaselineDiff(
            baseline_name=baseline.name,
            score_delta=score_delta,
            grade_changed=grade_changed,
            new_grade=new_grade,
            old_grade=old_grade,
            new_vulnerabilities=new_vulns,
            fixed_vulnerabilities=fixed_vulns,
            new_critical_issues=new_critical,
            resolved_critical_issues=resolved_critical,
            domain_deltas=domain_deltas,
            regressions=regressions,
            improvements=improvements,
            has_regression=has_regression,
        )
