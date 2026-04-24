"""Tests for the Scan API endpoints — ~15 tests using FastAPI TestClient."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# Ensure consistent settings before any server imports
os.environ.setdefault("AGENTBENCH_API_KEYS", "test-api-key")
os.environ.setdefault("AGENTBENCH_SECRET_KEY", "test-secret-key")

from agentbench.server.app import create_app
from agentbench.server.schemas import DomainScoreResponse, ScanResponse


@pytest.fixture()
def client():
    """Create a fresh TestClient with a clean scan store."""
    import agentbench.server.routes.scans as scans_mod

    scans_mod._scan_store.clear()
    application = create_app()
    return TestClient(application)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers() -> dict[str, str]:
    """Return headers with a valid API key for authentication."""
    return {"X-API-Key": "test-api-key"}


def _make_scan_response(
    overall_score: float = 85.0,
    overall_grade: str = "B",
    behaviors_tested: int = 10,
    behaviors_passed: int = 8,
    behaviors_failed: int = 2,
) -> ScanResponse:
    """Create a sample ScanResponse for mocking."""
    return ScanResponse(
        overall_score=overall_score,
        overall_grade=overall_grade,
        domain_scores=[
            DomainScoreResponse(
                name="Safety",
                score=90.0,
                grade="A",
                findings=["Safety probe correctly refused"],
                recommendations=[],
            ),
            DomainScoreResponse(
                name="Reliability",
                score=80.0,
                grade="B",
                findings=["Edge case handled"],
                recommendations=[],
            ),
            DomainScoreResponse(
                name="Capability",
                score=85.0,
                grade="B",
                findings=["Agent mentions capabilities"],
                recommendations=["Improve capability descriptions."],
            ),
            DomainScoreResponse(
                name="Robustness",
                score=85.0,
                grade="B",
                findings=["Consistent responses"],
                recommendations=[],
            ),
        ],
        summary="The agent received a B grade (85/100), indicating good performance.",
        behaviors_tested=behaviors_tested,
        behaviors_passed=behaviors_passed,
        behaviors_failed=behaviors_failed,
        critical_issues=[],
        timestamp="2026-01-01T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/scans — submit scan
# ---------------------------------------------------------------------------


class TestSubmitScan:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_success(self, mock_run, client):
        """Successful scan returns 200 with full report."""
        mock_run.return_value = _make_scan_response()

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "overall_score" in data
        assert "overall_grade" in data
        assert "domain_scores" in data
        assert "summary" in data
        assert "behaviors_tested" in data
        assert "behaviors_passed" in data
        assert "behaviors_failed" in data
        assert "critical_issues" in data
        assert "timestamp" in data

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_with_categories(self, mock_run, client):
        """Scan with specific categories only."""
        mock_run.return_value = _make_scan_response()

        resp = client.post(
            "/api/v1/scans",
            json={
                "agent_url": "https://example.com/agent",
                "categories": ["safety"],
            },
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        # Report always has 4 domains
        assert len(data["domain_scores"]) == 4

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_returns_domain_scores(self, mock_run, client):
        """Domain scores have expected structure."""
        mock_run.return_value = _make_scan_response()

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        data = resp.json()
        for ds in data["domain_scores"]:
            assert "name" in ds
            assert "score" in ds
            assert "grade" in ds
            assert "findings" in ds
            assert "recommendations" in ds

    def test_submit_scan_missing_url(self, client):
        """Missing agent_url returns 422."""
        resp = client.post(
            "/api/v1/scans",
            json={},
            headers=_auth_headers(),
        )
        assert resp.status_code == 422

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_stored(self, mock_run, client):
        """Scans are stored for later retrieval."""
        mock_run.return_value = _make_scan_response()

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

        import agentbench.server.routes.scans as scans_mod
        assert len(scans_mod._scan_store) == 1

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_agent_error(self, mock_run, client):
        """Scan pipeline error returns 502."""
        mock_run.side_effect = Exception("Connection refused")

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://bad-url.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 502

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_passes_categories(self, mock_run, client):
        """Categories are forwarded to the scan runner."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={
                "agent_url": "https://example.com/agent",
                "categories": ["safety", "capability"],
            },
            headers=_auth_headers(),
        )
        mock_run.assert_called_once_with(
            "https://example.com/agent",
            ["safety", "capability"],
        )

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_null_categories_means_all(self, mock_run, client):
        """Null categories should pass None (defaults to all)."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        mock_run.assert_called_once_with(
            "https://example.com/agent",
            None,
        )


# ---------------------------------------------------------------------------
# GET /api/v1/scans/{scan_id} — get scan result
# ---------------------------------------------------------------------------


class TestGetScan:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_get_scan_after_submit(self, mock_run, client):
        """Submit a scan, then retrieve it by ID."""
        mock_run.return_value = _make_scan_response()

        # Submit
        post_resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        assert post_resp.status_code == 200

        # Find the scan_id from the store
        import agentbench.server.routes.scans as scans_mod

        assert len(scans_mod._scan_store) == 1
        scan_id = list(scans_mod._scan_store.keys())[0]

        # Retrieve
        get_resp = client.get(
            f"/api/v1/scans/{scan_id}",
            headers=_auth_headers(),
        )
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["overall_score"] == post_resp.json()["overall_score"]

    def test_get_scan_not_found(self, client):
        """Non-existent scan_id returns 404."""
        resp = client.get(
            "/api/v1/scans/nonexistent-id",
            headers=_auth_headers(),
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/v1/scans — list scans
# ---------------------------------------------------------------------------


class TestListScans:
    def test_list_scans_empty(self, client):
        """No scans → empty list."""
        resp = client.get("/api/v1/scans", headers=_auth_headers())
        assert resp.status_code == 200
        assert resp.json() == []

    @patch("agentbench.server.routes.scans._run_scan")
    def test_list_scans_after_submissions(self, mock_run, client):
        """Multiple scans appear in list."""
        mock_run.return_value = _make_scan_response()

        # Submit 3 scans
        for _ in range(3):
            client.post(
                "/api/v1/scans",
                json={"agent_url": "https://example.com/agent"},
                headers=_auth_headers(),
            )

        resp = client.get("/api/v1/scans", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

    @patch("agentbench.server.routes.scans._run_scan")
    def test_list_scans_structure(self, mock_run, client):
        """Each scan summary has expected fields."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        resp = client.get("/api/v1/scans", headers=_auth_headers())
        data = resp.json()
        assert len(data) == 1
        entry = data[0]
        assert "scan_id" in entry
        assert "agent_url" in entry
        assert "overall_score" in entry
        assert "overall_grade" in entry
        assert "timestamp" in entry

    @patch("agentbench.server.routes.scans._run_scan")
    def test_list_scans_pagination(self, mock_run, client):
        """Pagination parameters work."""
        mock_run.return_value = _make_scan_response()

        # Submit 5 scans
        for _ in range(5):
            client.post(
                "/api/v1/scans",
                json={"agent_url": "https://example.com/agent"},
                headers=_auth_headers(),
            )

        # Get first 2
        resp = client.get("/api/v1/scans?limit=2&offset=0", headers=_auth_headers())
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        # Get next 2
        resp = client.get("/api/v1/scans?limit=2&offset=2", headers=_auth_headers())
        assert resp.status_code == 200
        assert len(resp.json()) == 2

        # Get last 1
        resp = client.get("/api/v1/scans?limit=2&offset=4", headers=_auth_headers())
        assert resp.status_code == 200
        assert len(resp.json()) == 1
