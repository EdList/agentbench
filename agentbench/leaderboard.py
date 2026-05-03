"""Leaderboard — store and compare scan results over time."""

from __future__ import annotations

import json
import os
from pathlib import Path

from agentbench.probes.base import ScanResult

_DEFAULT_DIR = Path.home() / ".agentbench"
_LEADERBOARD_FILE = "leaderboard.json"


def _ensure_dir() -> Path:
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_DIR


def add_scan_result(result: ScanResult, label: str | None = None) -> dict:
    """Add a scan result to the local leaderboard. Returns the entry."""
    _ensure_dir()
    lb = load_leaderboard()
    entry = {
        "timestamp": result.timestamp,
        "url": result.url,
        "label": label or result.url,
        "overall_score": result.overall_score,
        "grade": result.grade,
        "probes_run": result.probes_run,
        "critical_count": result.critical_count,
        "warning_count": result.warning_count,
        "domains": {
            name: {"score": ds.score, "grade": ds.grade}
            for name, ds in result.domain_scores.items()
        },
    }
    lb.append(entry)
    lb_path = _DEFAULT_DIR / _LEADERBOARD_FILE
    # Atomic write — write to temp file then rename
    tmp_path = lb_path.with_suffix(".tmp")
    with open(tmp_path, "w") as f:
        json.dump(lb, f, indent=2)
    os.replace(tmp_path, lb_path)
    return entry


def load_leaderboard() -> list[dict]:
    """Load the local leaderboard. Returns empty list if not found or corrupt."""
    lb_path = _DEFAULT_DIR / _LEADERBOARD_FILE
    if not lb_path.exists():
        return []
    try:
        with open(lb_path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return []


def get_recent(n: int = 10) -> list[dict]:
    """Get the N most recent leaderboard entries."""
    lb = load_leaderboard()
    return lb[-n:]


def compare_results(url: str | None = None, label: str | None = None) -> list[dict]:
    """Get all entries matching a URL or label for comparison."""
    lb = load_leaderboard()
    results = []
    for entry in lb:
        if (url and entry.get("url") == url) or (label and entry.get("label") == label):
            results.append(entry)
    return results
