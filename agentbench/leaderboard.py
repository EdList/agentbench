"""Leaderboard — store and compare scan results over time."""

from __future__ import annotations

import fcntl
import json
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentbench.probes.base import ScanResult

_DEFAULT_DIR = Path.home() / ".agentbench"
_LEADERBOARD_FILE = "leaderboard.json"


@contextmanager
def _leaderboard_lock() -> Iterator[None]:
    """Hold an exclusive lock while reading/writing the leaderboard file.

    A sidecar lock serializes access across atomic replacements; the
    leaderboard file itself is also flocked while the critical section runs.
    """
    _ensure_dir()
    lb_path = _DEFAULT_DIR / _LEADERBOARD_FILE
    lock_path = _DEFAULT_DIR / f"{_LEADERBOARD_FILE}.lock"
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        with open(lb_path, "a+", encoding="utf-8") as leaderboard_file:
            fcntl.flock(leaderboard_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(leaderboard_file.fileno(), fcntl.LOCK_UN)
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _read_leaderboard_unlocked() -> list[dict]:
    """Load leaderboard contents without acquiring a lock."""
    lb_path = _DEFAULT_DIR / _LEADERBOARD_FILE
    if not lb_path.exists():
        return []
    try:
        with open(lb_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list) or not all(isinstance(entry, dict) for entry in data):
            return []
        return data
    except (json.JSONDecodeError, OSError):
        logging.warning(
            "Failed to read leaderboard JSON; returning empty leaderboard",
            exc_info=True,
        )
        return []


def _ensure_dir() -> Path:
    _DEFAULT_DIR.mkdir(parents=True, exist_ok=True)
    return _DEFAULT_DIR


def add_scan_result(result: ScanResult, label: str | None = None) -> dict:
    """Add a scan result to the local leaderboard. Returns the entry."""
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
        "scan_scope": result.scan_scope,
    }
    with _leaderboard_lock():
        lb = _read_leaderboard_unlocked()
        lb.append(entry)
        lb_path = _DEFAULT_DIR / _LEADERBOARD_FILE
        # Atomic write — write to a unique temp file then rename
        tmp_path = lb_path.with_name(f"{lb_path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
        try:
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(lb, f, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, lb_path)
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
    return entry


def load_leaderboard() -> list[dict]:
    """Load the local leaderboard. Returns empty list if not found or corrupt."""
    with _leaderboard_lock():
        return _read_leaderboard_unlocked()


def get_recent(n: int = 10) -> list[dict]:
    """Get the N most recent leaderboard entries."""
    if n <= 0:
        return []
    lb = load_leaderboard()
    # Sort by timestamp descending to ensure chronological recency
    lb = sorted(lb, key=lambda e: e.get("timestamp", ""), reverse=True)
    return lb[:n]


def _normalize_url(url: str) -> str:
    """Normalize a URL for comparison: strip trailing slash and lowercase scheme/host."""
    if not url:
        return url
    normalized = url.strip().rstrip("/")
    if "://" in normalized:
        scheme, rest = normalized.split("://", 1)
        return f"{scheme.lower()}://{rest}"
    return normalized


def compare_results(url: str | None = None, label: str | None = None) -> list[dict]:
    """Get all entries matching a URL or label for comparison."""
    lb = load_leaderboard()
    results = []
    norm_url = _normalize_url(url) if url else None
    for entry in lb:
        entry_url = _normalize_url(entry.get("url", "")) if norm_url else entry.get("url")
        if (norm_url and entry_url == norm_url) or (label and entry.get("label") == label):
            results.append(entry)
    return results
