"""SQLite-backed scan persistence — stores scan history and enables regression tracking."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbench.scanner.scorer import _DOMAIN_WEIGHTS, ScanReport

DEFAULT_DB_PATH = Path.home() / ".agentbench" / "scans.db"


class ScanStore:
    """Persistent storage for scan results."""

    def __init__(self, db_path: Path | str | None = None):
        self._db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                agent_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                overall_score REAL NOT NULL,
                grade TEXT NOT NULL,
                report_json TEXT NOT NULL,
                duration_ms INTEGER
            )""")
            conn.execute("""CREATE TABLE IF NOT EXISTS domain_scores (
                scan_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                score REAL NOT NULL,
                grade TEXT NOT NULL,
                weight REAL NOT NULL,
                behaviors_total INTEGER,
                behaviors_passed INTEGER,
                FOREIGN KEY (scan_id) REFERENCES scans(id)
            )""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_scans_agent ON scans(agent_url)""")
            conn.execute("""CREATE INDEX IF NOT EXISTS idx_scans_created ON scans(created_at)""")

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def save_scan(
        self,
        scan_id: str,
        agent_url: str,
        report: ScanReport,
        duration_ms: int = 0,
    ) -> None:
        """Save a scan result."""
        with self._connect() as conn:
            conn.execute(
                (
                    "INSERT INTO scans "
                    "(id, agent_url, created_at, overall_score, grade, report_json, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    scan_id,
                    agent_url,
                    datetime.now(UTC).isoformat(),
                    report.overall_score,
                    report.overall_grade,
                    json.dumps(self._report_to_dict(report)),
                    duration_ms,
                ),
            )
            for domain in report.domain_scores:
                weight = _DOMAIN_WEIGHTS.get(domain.name.lower(), 0.0)
                conn.execute(
                (
                    "INSERT INTO domain_scores "
                    "(scan_id, domain, score, grade, weight, behaviors_total, behaviors_passed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)"
                ),
                    (
                        scan_id,
                        domain.name,
                        domain.score,
                        domain.grade,
                        weight,
                        0,
                        0,
                    ),
                )

    def get_scan(self, scan_id: str) -> dict[str, Any] | None:
        """Retrieve a scan by ID."""
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
            if not row:
                return None
            return dict(row)

    def list_scans(
        self,
        agent_url: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List scans, optionally filtered by agent_url."""
        with self._connect() as conn:
            if agent_url:
                rows = conn.execute(
                    "SELECT id, agent_url, created_at, overall_score, grade, duration_ms "
                    "FROM scans WHERE agent_url = ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (agent_url, limit, offset),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, agent_url, created_at, overall_score, grade, duration_ms "
                    "FROM scans ORDER BY created_at DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            return [dict(r) for r in rows]

    def get_regression_report(
        self,
        agent_url: str,
        latest_n: int = 2,
    ) -> dict[str, Any] | None:
        """Compare the latest scans for an agent and detect regressions."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, created_at, overall_score, grade, report_json "
                "FROM scans WHERE agent_url = ? ORDER BY created_at DESC LIMIT ?",
                (agent_url, latest_n),
            ).fetchall()

        if len(rows) < 2:
            return None

        current = dict(rows[0])
        previous = dict(rows[1])

        current_report = json.loads(current["report_json"])
        previous_report = json.loads(previous["report_json"])

        # Compare domain scores
        regressions = []
        improvements = []
        for curr_domain in current_report.get("domains", []):
            for prev_domain in previous_report.get("domains", []):
                if curr_domain["name"] == prev_domain["name"]:
                    delta = curr_domain["score"] - prev_domain["score"]
                    if delta < -5:
                        regressions.append({
                            "domain": curr_domain["name"],
                            "previous_score": prev_domain["score"],
                            "current_score": curr_domain["score"],
                            "delta": round(delta, 1),
                            "severity": "high" if delta < -20 else "medium",
                        })
                    elif delta > 5:
                        improvements.append({
                            "domain": curr_domain["name"],
                            "previous_score": prev_domain["score"],
                            "current_score": curr_domain["score"],
                            "delta": round(delta, 1),
                        })

        overall_delta = current["overall_score"] - previous["overall_score"]

        return {
            "agent_url": agent_url,
            "current_scan_id": current["id"],
            "current_scan_date": current["created_at"],
            "previous_scan_id": previous["id"],
            "previous_scan_date": previous["created_at"],
            "overall_delta": round(overall_delta, 1),
            "overall_trend": (
                "improved" if overall_delta > 5
                else "regressed" if overall_delta < -5
                else "stable"
            ),
            "regressions": regressions,
            "improvements": improvements,
        }

    def _report_to_dict(self, report: ScanReport) -> dict[str, Any]:
        """Convert ScanReport to JSON-serializable dict."""
        return {
            "overall_score": report.overall_score,
            "grade": report.overall_grade,
            "domains": [
                {
                    "name": d.name,
                    "score": d.score,
                    "grade": d.grade,
                    "findings": d.findings,
                    "recommendations": d.recommendations,
                }
                for d in report.domain_scores
            ],
            "summary": report.summary,
        }
