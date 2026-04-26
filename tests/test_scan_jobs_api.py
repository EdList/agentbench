"""Tests for async scan-job APIs and project gate job polling."""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

os.environ["AGENTBENCH_API_KEYS"] = "test-api-key,other-api-key"
os.environ["AGENTBENCH_SECRET_KEY"] = "test-secret-key-for-agentbench-32bytes"

from agentbench.server.app import create_app
from agentbench.server.auth import settings as auth_settings
from agentbench.server.models import Base
from agentbench.server.schemas import DomainScoreResponse, ScanResponse

auth_settings.api_keys = ["test-api-key", "other-api-key"]
auth_settings.secret_key = "test-secret-key-for-agentbench-32bytes"


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    import agentbench.server.models as models_mod
    import agentbench.server.routes.scans as scans_mod

    scans_mod._scan_store.clear()
    scans_mod.store = scans_mod.ScanStore(db_path=tmp_path / "scans.db")

    engine = create_engine(
        f"sqlite:///{tmp_path / 'server.db'}",
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(models_mod, "_engine", engine, raising=False)
    monkeypatch.setattr(models_mod, "_SessionLocal", session_factory, raising=False)
    Base.metadata.create_all(bind=engine)

    application = create_app()
    return TestClient(application)


def _auth_headers() -> dict[str, str]:
    return {"X-API-Key": "test-api-key"}


def _other_auth_headers() -> dict[str, str]:
    return {"X-API-Key": "other-api-key"}


def _make_scan_response(
    overall_score: float = 85.0,
    overall_grade: str = "B",
    critical_issues: list[str] | None = None,
) -> ScanResponse:
    return ScanResponse(
        overall_score=overall_score,
        overall_grade=overall_grade,
        domain_scores=[
            DomainScoreResponse(
                name="Safety",
                score=overall_score,
                grade=overall_grade,
                findings=["Sample finding"],
                recommendations=["Sample recommendation"],
            )
        ],
        summary="Sample summary",
        behaviors_tested=10,
        behaviors_passed=8,
        behaviors_failed=2,
        critical_issues=critical_issues or [],
        timestamp="2026-01-01T12:00:00+00:00",
    )


def _wait_for_terminal_job(
    client: TestClient,
    job_id: str,
    headers: dict[str, str],
    *,
    timeout_seconds: float = 2.0,
) -> dict:
    deadline = time.time() + timeout_seconds
    last_payload: dict | None = None
    while time.time() < deadline:
        response = client.get(f"/api/v1/scans/jobs/{job_id}", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        last_payload = payload
        if payload["status"] in {"completed", "failed", "cancelled"}:
            return payload
        time.sleep(0.02)
    raise AssertionError(f"Job {job_id} did not finish in time. Last payload: {last_payload}")


class TestScanJobsApi:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_job_and_poll_until_completed(self, mock_run, client: TestClient):
        mock_run.return_value = _make_scan_response()

        response = client.post(
            "/api/v1/scans/jobs",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        assert response.status_code == 202
        created = response.json()
        assert created["job_id"]
        assert created["status"] in {"queued", "running"}
        assert created["agent_url"] == "https://example.com/agent"

        terminal = _wait_for_terminal_job(client, created["job_id"], _auth_headers())
        assert terminal["status"] == "completed"
        assert terminal["scan_id"]
        assert terminal["overall_score"] == 85.0
        assert terminal["overall_grade"] == "B"
        assert terminal["permalink"] == f"/?scan_id={terminal['scan_id']}"

        scan_response = client.get(f"/api/v1/scans/{terminal['scan_id']}", headers=_auth_headers())
        assert scan_response.status_code == 200

    @patch("agentbench.server.routes.scans._run_scan")
    def test_cancel_running_scan_job_marks_it_cancelled(self, mock_run, client: TestClient):
        started = threading.Event()
        release = threading.Event()

        def _slow_scan(agent_url: str, categories: list[str] | None):
            started.set()
            release.wait(timeout=2)
            return _make_scan_response()

        mock_run.side_effect = _slow_scan

        response = client.post(
            "/api/v1/scans/jobs",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]
        assert started.wait(timeout=1)

        cancel_response = client.post(
            f"/api/v1/scans/jobs/{job_id}/cancel",
            headers=_auth_headers(),
        )
        assert cancel_response.status_code == 200
        assert cancel_response.json()["cancel_requested"] is True

        release.set()
        terminal = _wait_for_terminal_job(client, job_id, _auth_headers())
        assert terminal["status"] == "cancelled"
        assert terminal["scan_id"] is None

        scans_response = client.get("/api/v1/scans", headers=_auth_headers())
        assert scans_response.status_code == 200
        assert scans_response.json() == []

    @patch("agentbench.server.routes.scans._run_scan")
    def test_scan_job_status_is_scoped_to_principal(self, mock_run, client: TestClient):
        mock_run.return_value = _make_scan_response()

        response = client.post(
            "/api/v1/scans/jobs",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        other = client.get(f"/api/v1/scans/jobs/{job_id}", headers=_other_auth_headers())
        assert other.status_code == 404

    def test_fail_stale_scan_jobs_marks_queued_work_as_failed(self, client: TestClient):
        import agentbench.server.models as models_mod
        import agentbench.server.routes.scans as scans_mod

        session = models_mod.get_session_factory()()
        try:
            job = models_mod.ScanJob(
                principal="test-api-key",
                status="queued",
                agent_url="https://example.com/agent",
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            job_id = job.id
        finally:
            session.close()

        scans_mod.fail_stale_scan_jobs()

        verify_session = models_mod.get_session_factory()()
        try:
            refreshed = verify_session.query(models_mod.ScanJob).filter(models_mod.ScanJob.id == job_id).first()
            assert refreshed is not None
            assert refreshed.status == "failed"
            assert refreshed.error_detail is not None
            assert "server restart" in refreshed.error_detail.lower()
        finally:
            verify_session.close()


class TestProjectGateJobsApi:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_project_gate_job_returns_machine_friendly_terminal_payload(self, mock_run, client: TestClient):
        mock_run.return_value = _make_scan_response(overall_score=72.0, overall_grade="C")

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Production Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_overall_score": 80, "minimum_domain_scores": {}},
            headers=_auth_headers(),
        ).json()

        response = client.post(
            f"/api/v1/projects/{project['id']}/gate/jobs",
            json={"agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_auth_headers(),
        )

        assert response.status_code == 202
        created = response.json()
        assert created["project_id"] == project["id"]
        assert created["agent_id"] == agent["id"]
        assert created["policy_id"] == policy["id"]
        assert created["status"] in {"queued", "running"}

        terminal = _wait_for_terminal_job(client, created["job_id"], _auth_headers())
        assert terminal["status"] == "completed"
        assert terminal["release_verdict"] == "fail"
        assert any("overall score" in reason.lower() for reason in terminal["verdict_reasons"])
        assert terminal["overall_score"] == 72.0
        assert terminal["overall_grade"] == "C"
        assert terminal["permalink"] == f"/?scan_id={terminal['scan_id']}"

    @patch("agentbench.server.routes.scans._run_scan")
    def test_project_gate_job_rejects_cross_principal_access(self, mock_run, client: TestClient):
        mock_run.return_value = _make_scan_response()

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Production Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_domain_scores": {}},
            headers=_auth_headers(),
        ).json()

        response = client.post(
            f"/api/v1/projects/{project['id']}/gate/jobs",
            json={"agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_other_auth_headers(),
        )
        assert response.status_code == 404
