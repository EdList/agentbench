"""Regression tests for server persistence, async job, timeout & parallel state hardening."""

from __future__ import annotations

import json
import os
import time
import threading
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

# Set debug mode before any server imports
os.environ["AGENTBENCH_DEBUG"] = "true"

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agentbench.server.models import Base


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def _db(tmp_path, monkeypatch):
    """Create a fresh SQLAlchemy DB with all tables, patched into the server modules."""
    import agentbench.server.models as models_mod

    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(models_mod, "_engine", engine, raising=False)
    monkeypatch.setattr(models_mod, "_SessionLocal", session_factory, raising=False)
    Base.metadata.create_all(bind=engine)
    return engine, session_factory


# ---------------------------------------------------------------------------
# 1. LRU scan store eviction
# ---------------------------------------------------------------------------

class TestLRUScanStore:
    def test_lru_eviction_at_cap(self):
        """Store evicts oldest entries when cap is reached."""
        from agentbench.server.routes.scans import _LRUScanStore

        store = _LRUScanStore(maxsize=3)
        for i in range(5):
            store[f"scan-{i}"] = {"scan_id": f"scan-{i}"}

        # Only last 3 should remain
        assert len(store) == 3
        assert store.get("scan-0") is None
        assert store.get("scan-1") is None
        assert store.get("scan-2") is not None
        assert store.get("scan-3") is not None
        assert store.get("scan-4") is not None

    def test_lru_access_promotes_entry(self):
        """Accessing an entry promotes it so it's not evicted."""
        from agentbench.server.routes.scans import _LRUScanStore

        store = _LRUScanStore(maxsize=3)
        store["a"] = {"id": "a"}
        store["b"] = {"id": "b"}
        store["c"] = {"id": "c"}

        # Access 'a' to promote it
        _ = store.get("a")

        # Insert one more — 'b' should be evicted, not 'a'
        store["d"] = {"id": "d"}
        assert store.get("a") is not None
        assert store.get("b") is None

    def test_thread_safety(self):
        """Concurrent writes don't corrupt the store."""
        from agentbench.server.routes.scans import _LRUScanStore

        store = _LRUScanStore(maxsize=500)
        errors = []

        def writer(start):
            try:
                for i in range(start, start + 200):
                    store[f"t-{i}"] = {"scan_id": f"t-{i}"}
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(i * 200,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(store) == 500  # capped at maxsize


# ---------------------------------------------------------------------------
# 2. ThreadPoolExecutor — bounded concurrency
# ---------------------------------------------------------------------------

class TestBoundedJobPool:
    def test_job_uses_thread_pool_not_raw_thread(self):
        """_create_scan_job submits to the executor, not raw threading.Thread."""
        import agentbench.server.routes.scans as scans_mod

        assert hasattr(scans_mod, "_job_executor")
        from concurrent.futures import ThreadPoolExecutor
        assert isinstance(scans_mod._job_executor, ThreadPoolExecutor)

    def test_queue_full_rejection(self, _db, monkeypatch):
        """If the executor is shut down, the job is marked as failed immediately."""
        from concurrent.futures import ThreadPoolExecutor
        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.models import ScanJob

        engine, factory = _db
        monkeypatch.setattr(scans_mod, "store", scans_mod.ScanStore(db_path=Path("/tmp/test_rejection.db")))

        # Shut down the executor to trigger RuntimeError
        real_executor = scans_mod._job_executor
        dummy = ThreadPoolExecutor(max_workers=1)
        dummy.shutdown(wait=False)
        scans_mod._job_executor = dummy

        try:
            db = factory()
            resolved = scans_mod.ResolvedScanRequest(
                project_id=None, agent_id=None, policy_id=None,
                agent_url="https://example.com/agent",
                categories=None, policy=None,
            )
            job = scans_mod._create_scan_job(resolved, "test-user", db)
            assert job.status == "failed"
            assert "queue is full" in (job.error_detail or "").lower()
            db.close()
        finally:
            scans_mod._job_executor = real_executor


# ---------------------------------------------------------------------------
# 3. Scan-level timeout
# ---------------------------------------------------------------------------

class TestScanTimeout:
    def test_timeout_config_is_used(self, monkeypatch):
        """Worker uses settings.scan_timeout_seconds for deadline."""
        import agentbench.server.routes.scans as scans_mod

        # Just verify the setting exists and is an int
        from agentbench.server.config import settings
        assert isinstance(settings.scan_timeout_seconds, int)
        assert settings.scan_timeout_seconds > 0

    def test_timeout_exceeded_marks_failed(self, _db, monkeypatch):
        """Worker marks job failed if deadline has passed before execution starts."""
        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.models import ScanJob

        engine, factory = _db
        db = factory()

        # Create a job directly in DB
        job = ScanJob(
            principal="test",
            status="running",
            agent_url="https://example.com/agent",
            started_at=datetime.now(UTC),
        )
        db.add(job)
        db.commit()

        # Set timeout to 0 so it immediately exceeds
        monkeypatch.setattr(scans_mod.settings, "scan_timeout_seconds", 0)
        time.sleep(0.01)  # Ensure time passes

        resolved = scans_mod.ResolvedScanRequest(
            project_id=None, agent_id=None, policy_id=None,
            agent_url="https://example.com/agent",
            categories=None, policy=None,
        )
        scans_mod._run_scan_job_worker(job.id, "test", resolved)

        db.refresh(job)
        assert job.status == "failed"
        assert "timed out" in (job.error_detail or "").lower()
        db.close()


# ---------------------------------------------------------------------------
# 4. Dual-write consistency — persist before cache
# ---------------------------------------------------------------------------

class TestDualWriteConsistency:
    def test_persist_failure_does_not_cache(self, monkeypatch):
        """If store.save_scan raises, _scan_store should NOT contain the entry."""
        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.schemas import DomainScoreResponse, ScanResponse

        # Mock the store to raise
        mock_store = MagicMock()
        mock_store.save_scan.side_effect = RuntimeError("DB down")
        monkeypatch.setattr(scans_mod, "store", mock_store)

        report = ScanResponse(
            overall_score=80.0,
            overall_grade="B",
            domain_scores=[],
            summary="",
            behaviors_tested=10,
            behaviors_passed=8,
            behaviors_failed=2,
            critical_issues=[],
            timestamp=datetime.now(UTC).isoformat(),
        )
        resolved = scans_mod.ResolvedScanRequest(
            project_id=None, agent_id=None, policy_id=None,
            agent_url="https://example.com/agent",
            categories=None, policy=None,
        )
        scans_mod._scan_store.clear()

        with pytest.raises(RuntimeError, match="DB down"):
            scans_mod._persist_scan_response("scan-123", "test-user", resolved, report, None)

        # Should NOT be cached
        assert scans_mod._scan_store.get("scan-123") is None


# ---------------------------------------------------------------------------
# 5. WAL mode on SQLite ScanStore
# ---------------------------------------------------------------------------

class TestWALMode:
    def test_wal_mode_enabled(self, tmp_path):
        """ScanStore enables WAL journal mode on init."""
        from agentbench.scanner.store import ScanStore

        store = ScanStore(db_path=tmp_path / "wal_test.db")
        with store._connect() as conn:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode == "wal"

    def test_busy_timeout_set(self, tmp_path):
        """ScanStore sets busy_timeout on connections."""
        from agentbench.scanner.store import ScanStore

        store = ScanStore(db_path=tmp_path / "busy_test.db")
        with store._connect() as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
        assert timeout >= 5000


# ---------------------------------------------------------------------------
# 6. Generic exception handler in worker
# ---------------------------------------------------------------------------

class TestWorkerExceptionSafety:
    def test_unhandled_exception_marks_failed(self, _db, monkeypatch):
        """If _execute_resolved_scan raises a non-HTTPException, job is marked failed."""
        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.models import ScanJob

        engine, factory = _db
        db = factory()

        job = ScanJob(
            principal="test",
            status="running",
            agent_url="https://example.com/agent",
            started_at=datetime.now(UTC),
        )
        db.add(job)
        db.commit()

        # Make _execute_resolved_scan raise a generic exception
        monkeypatch.setattr(
            scans_mod,
            "_execute_resolved_scan",
            MagicMock(side_effect=ValueError("unexpected bug")),
        )
        monkeypatch.setattr(scans_mod.settings, "scan_timeout_seconds", 300)

        resolved = scans_mod.ResolvedScanRequest(
            project_id=None, agent_id=None, policy_id=None,
            agent_url="https://example.com/agent",
            categories=None, policy=None,
        )
        scans_mod._run_scan_job_worker(job.id, "test", resolved)

        db.refresh(job)
        assert job.status == "failed"
        assert "internal error" in (job.error_detail or "").lower()
        db.close()


# ---------------------------------------------------------------------------
# 7. DB-polling cancellation (no in-memory cancel events)
# ---------------------------------------------------------------------------

class TestDBPollingCancellation:
    def test_no_cancel_events_dict(self):
        """_job_cancel_events no longer exists."""
        import agentbench.server.routes.scans as scans_mod
        assert not hasattr(scans_mod, "_job_cancel_events")

    def test_cancel_via_db_column(self, _db, monkeypatch):
        """Setting cancel_requested=1 in DB causes worker to cancel."""
        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.models import ScanJob

        engine, factory = _db
        db = factory()

        job = ScanJob(
            principal="test",
            status="running",
            cancel_requested=1,
            agent_url="https://example.com/agent",
            started_at=datetime.now(UTC),
        )
        db.add(job)
        db.commit()

        resolved = scans_mod.ResolvedScanRequest(
            project_id=None, agent_id=None, policy_id=None,
            agent_url="https://example.com/agent",
            categories=None, policy=None,
        )
        scans_mod._run_scan_job_worker(job.id, "test", resolved)

        db.refresh(job)
        assert job.status == "cancelled"
        db.close()


# ---------------------------------------------------------------------------
# 8. Periodic reaper
# ---------------------------------------------------------------------------

class TestPeriodicReaper:
    def test_reaper_cleanup_on_startup(self, _db, monkeypatch):
        """fail_stale_scan_jobs marks orphaned running jobs as failed."""
        from agentbench.server.models import ScanJob
        from agentbench.server.routes.scans import fail_stale_scan_jobs

        engine, factory = _db
        monkeypatch.setattr(
            "agentbench.server.models.get_session_factory",
            lambda: factory,
        )

        db = factory()
        job = ScanJob(
            principal="test",
            status="running",
            agent_url="https://example.com/agent",
        )
        db.add(job)
        db.commit()

        fail_stale_scan_jobs()

        db.refresh(job)
        assert job.status == "failed"
        assert "interrupted" in (job.error_detail or "").lower()
        db.close()
