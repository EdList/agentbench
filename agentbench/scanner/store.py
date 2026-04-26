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
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute("""CREATE TABLE IF NOT EXISTS scans (
                id TEXT PRIMARY KEY,
                principal TEXT NOT NULL DEFAULT '',
                agent_url TEXT NOT NULL,
                created_at TEXT NOT NULL,
                overall_score REAL NOT NULL,
                grade TEXT NOT NULL,
                report_json TEXT NOT NULL,
                duration_ms INTEGER
            )""")
            columns = {row[1] for row in conn.execute("PRAGMA table_info(scans)").fetchall()}
            if "principal" not in columns:
                conn.execute("ALTER TABLE scans ADD COLUMN principal TEXT NOT NULL DEFAULT ''")
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
            conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                "idx_scans_principal_agent ON scans(principal, agent_url)"
            )

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
        *,
        principal: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Save a scan result."""
        with self._connect() as conn:
            conn.execute(
                (
                    "INSERT INTO scans "
                    "(id, principal, agent_url, created_at, "
                    "overall_score, grade, report_json, duration_ms) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                ),
                (
                    scan_id,
                    principal,
                    agent_url,
                    datetime.now(UTC).isoformat(),
                    report.overall_score,
                    report.overall_grade,
                    json.dumps(self._report_to_dict(report, metadata=metadata)),
                    duration_ms,
                ),
            )
            for domain in report.domain_scores:
                weight = _DOMAIN_WEIGHTS.get(domain.name.lower(), 0.0)
                conn.execute(
                    (
                        "INSERT INTO domain_scores "
                        "(scan_id, domain, score, grade, weight, "
                        "behaviors_total, behaviors_passed) "
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

    def get_scan(
        self,
        scan_id: str,
        principal: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a scan by ID."""
        with self._connect() as conn:
            if principal is None:
                row = conn.execute("SELECT * FROM scans WHERE id = ?", (scan_id,)).fetchone()
            else:
                row = conn.execute(
                    "SELECT * FROM scans WHERE id = ? AND principal = ?",
                    (scan_id, principal),
                ).fetchone()
            if not row:
                return None
            return dict(row)

    def list_scans(
        self,
        agent_url: str | None = None,
        principal: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List scans, optionally filtered by agent_url and principal."""
        with self._connect() as conn:
            conditions: list[str] = []
            params: list[Any] = []
            if principal is not None:
                conditions.append("principal = ?")
                params.append(principal)
            if agent_url:
                conditions.append("agent_url = ?")
                params.append(agent_url)
            where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
            rows = conn.execute(
                "SELECT id, agent_url, created_at, overall_score, grade, duration_ms "
                f"FROM scans{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (*params, limit, offset),
            ).fetchall()
            return [dict(r) for r in rows]

    def get_regression_report(
        self,
        agent_url: str,
        principal: str | None = None,
        latest_n: int = 2,
    ) -> dict[str, Any] | None:
        """Compare the latest scans for an agent and detect regressions."""
        with self._connect() as conn:
            if principal is None:
                rows = conn.execute(
                    "SELECT id, created_at, overall_score, grade, report_json "
                    "FROM scans WHERE agent_url = ? ORDER BY created_at DESC LIMIT ?",
                    (agent_url, latest_n),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, created_at, overall_score, grade, report_json "
                    "FROM scans WHERE agent_url = ? AND principal = ? "
                    "ORDER BY created_at DESC LIMIT ?",
                    (agent_url, principal, latest_n),
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
                        regressions.append(
                            {
                                "domain": curr_domain["name"],
                                "previous_score": prev_domain["score"],
                                "current_score": curr_domain["score"],
                                "delta": round(delta, 1),
                                "severity": "high" if delta < -20 else "medium",
                            }
                        )
                    elif delta > 5:
                        improvements.append(
                            {
                                "domain": curr_domain["name"],
                                "previous_score": prev_domain["score"],
                                "current_score": curr_domain["score"],
                                "delta": round(delta, 1),
                            }
                        )

        overall_delta = current["overall_score"] - previous["overall_score"]

        return {
            "agent_url": agent_url,
            "current_scan_id": current["id"],
            "current_scan_date": current["created_at"],
            "previous_scan_id": previous["id"],
            "previous_scan_date": previous["created_at"],
            "overall_delta": round(overall_delta, 1),
            "overall_trend": (
                "improved" if overall_delta > 5 else "regressed" if overall_delta < -5 else "stable"
            ),
            "regressions": regressions,
            "improvements": improvements,
        }

    def _report_to_dict(
        self,
        report: ScanReport,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert ScanReport to JSON-serializable dict."""
        payload = {
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
            "behaviors_tested": report.behaviors_tested,
            "behaviors_passed": report.behaviors_passed,
            "behaviors_failed": report.behaviors_failed,
            "critical_issues": report.critical_issues,
            "timestamp": report.timestamp.isoformat(),
        }
        if metadata:
            payload.update(metadata)
        return payload


# ---------------------------------------------------------------------------
# Server-backed scan store (reads/writes from SQLAlchemy DB)
# ---------------------------------------------------------------------------

from sqlalchemy import Engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402


class ServerScanStore:
    """Server-backed scan persistence using the SQLAlchemy database.

    Shares scan history across replicas instead of using a node-local
    SQLite file.  Has the same public interface as :class:`ScanStore`.
    """

    def __init__(self, engine: Engine | None = None, session: Session | None = None):
        if session is not None:
            self._session = session
            self._factory = None
            self._owns_session = False
        elif engine is not None:
            self._factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            self._session = None
            self._owns_session = True
        else:
            raise ValueError("ServerScanStore requires either an engine or a session")

    def _get_session(self) -> Session:
        if self._session is not None:
            return self._session
        return self._factory()  # type: ignore[misc]

    def _close_session(self, session: Session) -> None:
        """Close the session only if we own it (i.e. it was created from the factory)."""
        if self._owns_session:
            session.close()

    # ---- public interface (same as ScanStore) ----

    def save_scan(
        self,
        scan_id: str,
        agent_url: str,
        report: ScanReport,
        duration_ms: int = 0,
        *,
        principal: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Persist the scan report into the ScanJob row matched by scan_id."""
        from agentbench.server.models import ScanJob

        session = self._get_session()
        try:
            job = session.query(ScanJob).filter(ScanJob.scan_id == scan_id).first()
            if job is None:
                # Fallback: create a new ScanJob row if there's no match
                job = ScanJob(
                    id=scan_id,
                    principal=principal,
                    status="completed",
                    agent_url=agent_url,
                    scan_id=scan_id,
                    overall_score=report.overall_score,
                    overall_grade=report.overall_grade,
                )
                session.add(job)

            report_dict = self._report_to_dict(report, metadata=metadata)
            job.report_json = json.dumps(report_dict)
            job.domain_scores_json = json.dumps(
                [
                    {
                        "name": d.name,
                        "score": d.score,
                        "grade": d.grade,
                    }
                    for d in report.domain_scores
                ]
            )
            job.overall_score = report.overall_score
            job.overall_grade = report.overall_grade
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            self._close_session(session)

    def get_scan(
        self,
        scan_id: str,
        principal: str | None = None,
    ) -> dict[str, Any] | None:
        """Retrieve a scan by ID (and optionally principal)."""
        from agentbench.server.models import ScanJob

        session = self._get_session()
        try:
            query = session.query(ScanJob).filter(ScanJob.scan_id == scan_id)
            if principal is not None:
                query = query.filter(ScanJob.principal == principal)
            job = query.first()
            if job is None:
                return None
            return self._job_to_row_dict(job)
        finally:
            self._close_session(session)

    def list_scans(
        self,
        agent_url: str | None = None,
        principal: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List scans, optionally filtered by agent_url and principal."""
        from agentbench.server.models import ScanJob

        session = self._get_session()
        try:
            query = session.query(ScanJob).filter(ScanJob.scan_id.isnot(None))
            if principal is not None:
                query = query.filter(ScanJob.principal == principal)
            if agent_url:
                query = query.filter(ScanJob.agent_url == agent_url)
            query = query.order_by(ScanJob.created_at.desc())
            jobs = query.offset(offset).limit(limit).all()
            return [self._job_to_summary_dict(j) for j in jobs]
        finally:
            self._close_session(session)

    def get_regression_report(
        self,
        agent_url: str,
        principal: str | None = None,
        latest_n: int = 2,
    ) -> dict[str, Any] | None:
        """Compare the latest two scans for an agent_url / principal pair."""
        from agentbench.server.models import ScanJob

        session = self._get_session()
        try:
            query = (
                session.query(ScanJob)
                .filter(ScanJob.agent_url == agent_url)
                .filter(ScanJob.scan_id.isnot(None))
                .filter(ScanJob.report_json.isnot(None))
            )
            if principal is not None:
                query = query.filter(ScanJob.principal == principal)
            query = query.order_by(ScanJob.created_at.desc())
            jobs = query.limit(latest_n).all()
        finally:
            self._close_session(session)

        if len(jobs) < 2:
            return None

        current = jobs[0]
        previous = jobs[1]

        current_report = json.loads(current.report_json)
        previous_report = json.loads(previous.report_json)

        regressions = []
        improvements = []
        for curr_domain in current_report.get("domains", []):
            for prev_domain in previous_report.get("domains", []):
                if curr_domain["name"] == prev_domain["name"]:
                    delta = curr_domain["score"] - prev_domain["score"]
                    if delta < -5:
                        regressions.append(
                            {
                                "domain": curr_domain["name"],
                                "previous_score": prev_domain["score"],
                                "current_score": curr_domain["score"],
                                "delta": round(delta, 1),
                                "severity": "high" if delta < -20 else "medium",
                            }
                        )
                    elif delta > 5:
                        improvements.append(
                            {
                                "domain": curr_domain["name"],
                                "previous_score": prev_domain["score"],
                                "current_score": curr_domain["score"],
                                "delta": round(delta, 1),
                            }
                        )

        overall_delta = (current.overall_score or 0) - (previous.overall_score or 0)

        return {
            "agent_url": agent_url,
            "current_scan_id": current.scan_id,
            "current_scan_date": current.created_at.isoformat() if current.created_at else "",
            "previous_scan_id": previous.scan_id,
            "previous_scan_date": previous.created_at.isoformat() if previous.created_at else "",
            "overall_delta": round(overall_delta, 1),
            "overall_trend": (
                "improved" if overall_delta > 5 else "regressed" if overall_delta < -5 else "stable"
            ),
            "regressions": regressions,
            "improvements": improvements,
        }

    # ---- helpers ----

    @staticmethod
    def _report_to_dict(
        report: ScanReport,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Convert ScanReport to JSON-serializable dict."""
        payload = {
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
            "behaviors_tested": report.behaviors_tested,
            "behaviors_passed": report.behaviors_passed,
            "behaviors_failed": report.behaviors_failed,
            "critical_issues": report.critical_issues,
            "timestamp": report.timestamp.isoformat(),
        }
        if metadata:
            payload.update(metadata)
        return payload

    @staticmethod
    def _job_to_row_dict(job: ScanJob) -> dict[str, Any]:  # type: ignore[name-defined  # noqa: F821
        """Convert a ScanJob ORM object to the dict format expected by consumers."""
        return {
            "id": job.scan_id or job.id,
            "principal": job.principal,
            "agent_url": job.agent_url,
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "overall_score": job.overall_score,
            "grade": job.overall_grade,
            "report_json": job.report_json or "{}",
            "duration_ms": 0,
        }

    @staticmethod
    def _job_to_summary_dict(job: ScanJob) -> dict[str, Any]:  # type: ignore[name-defined  # noqa: F821
        """Convert a ScanJob to the summary dict format (for list_scans)."""
        return {
            "id": job.scan_id or job.id,
            "agent_url": job.agent_url,
            "created_at": job.created_at.isoformat() if job.created_at else "",
            "overall_score": job.overall_score,
            "grade": job.overall_grade,
            "duration_ms": 0,
        }
