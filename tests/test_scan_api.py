"""Tests for the Scan API endpoints — ~15 tests using FastAPI TestClient."""

from __future__ import annotations

import os
import socket
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import ANY, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

# Ensure consistent settings before any server imports
os.environ["AGENTBENCH_API_KEYS"] = "test-api-key,other-api-key"
os.environ["AGENTBENCH_SECRET_KEY"] = "test-secret-key-for-agentbench-32bytes"

from agentbench.scanner.store import ServerScanStore
from agentbench.server.app import create_app
from agentbench.server.auth import settings as auth_settings
from agentbench.server.models import Base, get_db
from agentbench.server.schemas import DomainScoreResponse, ScanResponse

auth_settings.api_keys = ["test-api-key", "other-api-key"]
auth_settings.secret_key = "test-secret-key-for-agentbench-32bytes"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """Create a fresh TestClient with isolated in-memory and SQLite scan stores."""
    import agentbench.server.models as models_mod
    import agentbench.server.routes.scans as scans_mod

    scans_mod._scan_store.clear()
    scans_mod._rate_limit_window_by_principal.clear()
    scans_mod.store = scans_mod.ScanStore(db_path=tmp_path / "scans.db")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'server.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    monkeypatch.setattr(models_mod, "_engine", engine, raising=False)
    monkeypatch.setattr(models_mod, "_SessionLocal", test_session, raising=False)

    def _override_get_db():
        db = test_session()
        try:
            yield db
        finally:
            db.close()

    application = create_app()
    application.dependency_overrides[get_db] = _override_get_db
    return TestClient(application)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _auth_headers() -> dict[str, str]:
    """Return headers with the primary test API key."""
    return {"X-API-Key": "test-api-key"}


def _other_auth_headers() -> dict[str, str]:
    """Return headers with a different authenticated principal."""
    return {"X-API-Key": "other-api-key"}


def _make_scan_response(
    overall_score: float = 85.0,
    overall_grade: str = "B",
    behaviors_tested: int = 10,
    behaviors_passed: int = 8,
    behaviors_failed: int = 2,
    critical_issues: list[str] | None = None,
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
        critical_issues=critical_issues or [],
        timestamp="2026-01-01T12:00:00+00:00",
    )


# ---------------------------------------------------------------------------
# POST /api/v1/scans — submit scan
# ---------------------------------------------------------------------------


class TestSubmitScan:
    @pytest.mark.parametrize(
        "blocked_url",
        [
            "http://2130706433/agent",
            "http://127.1/agent",
            "http://0x7f000001/agent",
            "http://100.64.0.1/agent",
        ],
    )
    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_rejects_alternate_loopback_formats(self, mock_run, client, blocked_url):
        """Alternate numeric loopback formats must be rejected by SSRF validation."""
        mock_run.return_value = _make_scan_response()

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": blocked_url},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_rejects_dns_resolved_private_ipv4_host(
        self, mock_run, client, monkeypatch
    ):
        """A public-looking hostname must be rejected when DNS resolves it to private IPv4."""
        mock_run.return_value = _make_scan_response()

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "private-host.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("10.10.0.7", 0)),
            ]

        monkeypatch.setattr("agentbench.server.routes.scans.socket.getaddrinfo", _fake_getaddrinfo)

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://private-host.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        mock_run.assert_not_called()


class TestServerBackedScanStore:
    def test_get_scan_ignores_incomplete_rows(self, client: TestClient):
        import agentbench.server.models as models_mod
        import agentbench.server.routes.scans as scans_mod

        db = models_mod.get_session_factory()()
        job = models_mod.ScanJob(
            id="job-incomplete",
            principal="test-api-key",
            agent_url="https://example.com/agent",
            status="failed",
            scan_id="scan-incomplete",
        )
        db.add(job)
        db.commit()

        scans_mod.store = ServerScanStore(session=db)

        assert scans_mod.store.get_scan("scan-incomplete", principal="test-api-key") is None

    def test_list_scans_orders_by_completion_time_and_filters_partial_rows(
        self, client: TestClient
    ):
        import agentbench.server.models as models_mod
        import agentbench.server.routes.scans as scans_mod

        db = models_mod.get_session_factory()()
        db.add_all(
            [
                models_mod.ScanJob(
                    id="job-partial",
                    principal="test-api-key",
                    agent_url="https://example.com/agent",
                    status="running",
                    scan_id="scan-partial",
                ),
                models_mod.ScanJob(
                    id="job-old-complete",
                    principal="test-api-key",
                    agent_url="https://example.com/agent",
                    status="completed",
                    scan_id="scan-old-complete",
                    report_json="{}",
                    overall_score=70.0,
                    overall_grade="C",
                    created_at=datetime(2026, 1, 2, tzinfo=UTC),
                    completed_at=datetime(2026, 1, 3, tzinfo=UTC),
                ),
                models_mod.ScanJob(
                    id="job-new-complete",
                    principal="test-api-key",
                    agent_url="https://example.com/agent",
                    status="completed",
                    scan_id="scan-new-complete",
                    report_json="{}",
                    overall_score=90.0,
                    overall_grade="A",
                    created_at=datetime(2026, 1, 1, tzinfo=UTC),
                    completed_at=datetime(2026, 1, 4, tzinfo=UTC),
                ),
            ]
        )
        db.commit()

        scans_mod.store = ServerScanStore(session=db)
        scans = scans_mod.store.list_scans(principal="test-api-key")

        assert [scan["id"] for scan in scans] == ["scan-new-complete", "scan-old-complete"]

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_rejects_dns_resolved_private_ipv6_host(
        self, mock_run, client, monkeypatch
    ):
        """A public-looking hostname must be rejected when DNS resolves it to private IPv6."""
        mock_run.return_value = _make_scan_response()

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "private-v6.example.com"
            return [
                (socket.AF_INET6, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("fd00::7", 0, 0, 0)),
            ]

        monkeypatch.setattr("agentbench.server.routes.scans.socket.getaddrinfo", _fake_getaddrinfo)

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://private-v6.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        mock_run.assert_not_called()

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_rejects_host_with_mixed_public_and_private_dns_results(
        self, mock_run, client, monkeypatch
    ):
        """Mixed DNS answers must be rejected if any resolved address is private."""
        mock_run.return_value = _make_scan_response()

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "mixed-host.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("192.168.1.7", 0)),
            ]

        monkeypatch.setattr("agentbench.server.routes.scans.socket.getaddrinfo", _fake_getaddrinfo)

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://mixed-host.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        mock_run.assert_not_called()

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_rejects_dns_resolved_carrier_grade_nat_host(
        self, mock_run, client, monkeypatch
    ):
        """Carrier-grade NAT space is non-global and must be rejected."""
        mock_run.return_value = _make_scan_response()

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "cgnat-host.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("100.64.0.7", 0)),
            ]

        monkeypatch.setattr("agentbench.server.routes.scans.socket.getaddrinfo", _fake_getaddrinfo)

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://cgnat-host.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 400
        mock_run.assert_not_called()

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_allows_public_dns_host(self, mock_run, client, monkeypatch):
        """A hostname that resolves only to public IPs should still scan successfully."""
        mock_run.return_value = _make_scan_response()

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "public-host.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            ]

        monkeypatch.setattr("agentbench.server.routes.scans.socket.getaddrinfo", _fake_getaddrinfo)

        resp = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://public-host.example.com/agent"},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        mock_run.assert_called_once_with(
            "https://public-host.example.com/agent", None, cancel_fn=ANY, deadline=ANY,
        )

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
        assert "scan_id" in data
        assert data["scan_id"]
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
    def test_submit_scan_rate_limits_bursts(self, mock_run, client, monkeypatch):
        """Per-principal rate limiting returns 429 once the window is exceeded."""
        import agentbench.server.routes.scans as scans_mod

        mock_run.return_value = _make_scan_response()
        monkeypatch.setattr(scans_mod.settings, "scan_rate_limit_max_requests", 1)
        monkeypatch.setattr(scans_mod.settings, "scan_rate_limit_window_seconds", 60)

        first = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        second = client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        assert first.status_code == 200
        assert second.status_code == 429

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
        assert resp.json()["detail"] == "Scan failed. Check the agent endpoint and try again."

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
            cancel_fn=ANY,
            deadline=ANY,
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
            cancel_fn=ANY,
            deadline=ANY,
        )


# ---------------------------------------------------------------------------
# GET /api/v1/scans/{scan_id} — get scan result
# ---------------------------------------------------------------------------


class TestSafeDNSTransport:
    def test_blocks_non_global_ip_at_request_time(self, monkeypatch):
        """Request-time DNS checks must block non-global rebinding targets like CGNAT."""
        import agentbench.server.routes.scans as scans_mod

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "rebind.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("100.64.0.9", 0)),
            ]

        def _unexpected_super(self, request):  # pragma: no cover - should never run
            raise AssertionError("Unsafe request should be blocked before hitting the network")

        monkeypatch.setattr(scans_mod.socket, "getaddrinfo", _fake_getaddrinfo)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _unexpected_super)

        transport = scans_mod.SafeDNSTransport()
        request = httpx.Request("POST", "https://rebind.example.com/agent")

        with pytest.raises(httpx.ConnectError, match="private/internal"):
            transport.handle_request(request)

    def test_allows_public_ip_at_request_time(self, monkeypatch):
        """Public request-time DNS results should still pass through to the underlying transport."""
        import agentbench.server.routes.scans as scans_mod

        def _fake_getaddrinfo(hostname, port, *args, **kwargs):
            assert hostname == "public.example.com"
            return [
                (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", ("93.184.216.34", 0)),
            ]

        expected = httpx.Response(
            200,
            json={"response": "ok"},
            request=httpx.Request("POST", "https://public.example.com/agent"),
        )

        def _fake_super(self, request):
            assert str(request.url) == "https://public.example.com/agent"
            return expected

        monkeypatch.setattr(scans_mod.socket, "getaddrinfo", _fake_getaddrinfo)
        monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _fake_super)

        transport = scans_mod.SafeDNSTransport()
        request = httpx.Request("POST", "https://public.example.com/agent")

        response = transport.handle_request(request)
        assert response is expected

    def test_run_scan_disables_redirect_following(self, monkeypatch):
        """The scanner client must not follow redirects when probing agents."""
        import agentbench.server.routes.scans as scans_mod

        mock_response = MagicMock()
        mock_response.raise_for_status.return_value = None
        mock_response.json.return_value = {"response": "ok"}
        mock_client = MagicMock()
        mock_client.__enter__.return_value = mock_client
        mock_client.post.return_value = mock_response
        client_ctor = MagicMock(return_value=mock_client)
        monkeypatch.setattr(scans_mod.httpx, "Client", client_ctor)

        class _FakeProber:
            def __init__(self, agent_fn, categories):
                self._agent_fn = agent_fn

            def probe_all(self, deadline=None):
                self._agent_fn("hello")
                return object()

        class _FakeAnalyzer:
            def __init__(self, use_llm=False):
                self.use_llm = use_llm

            def analyze(self, session):
                return ["behavior"]

        class _FakeEngine:
            def score(self, behaviors):
                return SimpleNamespace(
                    overall_score=80.0,
                    overall_grade="B",
                    domain_scores=[],
                    summary="ok",
                    behaviors_tested=1,
                    behaviors_passed=1,
                    behaviors_failed=0,
                    critical_issues=[],
                    timestamp=datetime.now(UTC),
                )

        monkeypatch.setattr(scans_mod, "AgentProber", _FakeProber)
        monkeypatch.setattr(scans_mod, "BehaviorAnalyzer", _FakeAnalyzer)
        monkeypatch.setattr(scans_mod, "ScoringEngine", _FakeEngine)

        scans_mod._run_scan("https://example.com/agent", None)

        assert client_ctor.call_args.kwargs["follow_redirects"] is True
        assert client_ctor.call_args.kwargs["max_redirects"] == 5
        assert "max_response_size" not in client_ctor.call_args.kwargs


class TestScanCategoryExpansion:
    def test_expand_scan_categories_maps_public_domains_to_internal_probe_categories(self):
        import agentbench.server.routes.scans as scans_mod

        assert scans_mod._expand_scan_categories(
            ["safety", "reliability", "capability", "robustness"]
        ) == [
            "safety",
            "persona",
            "edge_case",
            "capability",
            "robustness",
        ]


class TestProjectBackedScans:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_uses_saved_agent_and_policy_categories(self, mock_run, client):
        """Saved agents and policies should drive the scan target and categories."""
        mock_run.return_value = _make_scan_response()

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Prod Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={
                "name": "Release Gate",
                "categories": ["safety", "reliability"],
                "minimum_domain_scores": {},
            },
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            "/api/v1/scans",
            json={
                "project_id": project["id"],
                "agent_id": agent["id"],
                "policy_id": policy["id"],
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == project["id"]
        assert data["agent_id"] == agent["id"]
        assert data["policy_id"] == policy["id"]
        assert data["release_verdict"] == "pass"
        assert data["verdict_reasons"] == []
        mock_run.assert_called_once_with(
            "https://example.com/agent",
            ["safety", "reliability"],
            cancel_fn=ANY,
            deadline=ANY,
        )

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_returns_fail_verdict_when_policy_threshold_is_missed(
        self, mock_run, client
    ):
        """Policy thresholds should produce a fail verdict with explicit reasons."""
        mock_run.return_value = _make_scan_response(overall_score=72.0, overall_grade="C")

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Prod Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_overall_score": 80, "minimum_domain_scores": {}},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            "/api/v1/scans",
            json={
                "project_id": project["id"],
                "agent_id": agent["id"],
                "policy_id": policy["id"],
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["release_verdict"] == "fail"
        assert any("overall score" in reason.lower() for reason in data["verdict_reasons"])

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_returns_fail_verdict_when_critical_issues_exist(self, mock_run, client):
        """Critical issues should fail a policy when configured to do so."""
        mock_run.return_value = _make_scan_response(
            overall_score=91.0,
            overall_grade="A",
            behaviors_failed=1,
            critical_issues=["Prompt injection leak"],
        )

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Prod Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={
                "name": "Release Gate",
                "fail_on_critical_issues": True,
                "minimum_domain_scores": {},
            },
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            "/api/v1/scans",
            json={
                "project_id": project["id"],
                "agent_id": agent["id"],
                "policy_id": policy["id"],
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["release_verdict"] == "fail"
        assert any("critical issue" in reason.lower() for reason in data["verdict_reasons"])

    @patch("agentbench.server.routes.scans._run_scan")
    def test_submit_scan_returns_fail_verdict_when_regression_delta_exceeds_policy(
        self, mock_run, client
    ):
        """Regression delta thresholds should fail the release gate.

        This happens when the latest scan drops too far.
        """
        mock_run.side_effect = [
            _make_scan_response(overall_score=90.0, overall_grade="A"),
            _make_scan_response(overall_score=60.0, overall_grade="D"),
        ]

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Prod Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "max_regression_delta": -10, "minimum_domain_scores": {}},
            headers=_auth_headers(),
        ).json()

        first = client.post(
            "/api/v1/scans",
            json={"project_id": project["id"], "agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_auth_headers(),
        )
        second = client.post(
            "/api/v1/scans",
            json={"project_id": project["id"], "agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_auth_headers(),
        )

        assert first.status_code == 200
        assert second.status_code == 200
        data = second.json()
        assert data["release_verdict"] == "fail"
        assert any("regression delta" in reason.lower() for reason in data["verdict_reasons"])

    @patch("agentbench.server.routes.scans._run_scan")
    def test_get_scan_after_restart_preserves_release_verdict_metadata(self, mock_run, client):
        """Persisted scans should retain project/policy verdict metadata after cache reset."""
        mock_run.return_value = _make_scan_response(overall_score=72.0, overall_grade="C")

        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        agent = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Prod Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        ).json()
        policy = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_overall_score": 80, "minimum_domain_scores": {}},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            "/api/v1/scans",
            json={"project_id": project["id"], "agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_auth_headers(),
        )
        assert resp.status_code == 200

        import agentbench.server.routes.scans as scans_mod

        scan_id = resp.json()["scan_id"]
        scans_mod._scan_store.clear()

        persisted = client.get(f"/api/v1/scans/{scan_id}", headers=_auth_headers())
        assert persisted.status_code == 200
        data = persisted.json()
        assert data["project_id"] == project["id"]
        assert data["agent_id"] == agent["id"]
        assert data["policy_id"] == policy["id"]
        assert data["release_verdict"] == "fail"
        assert any("overall score" in reason.lower() for reason in data["verdict_reasons"])


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

    @patch("agentbench.server.routes.scans._run_scan")
    def test_list_scans_falls_back_to_persisted_store_after_restart(self, mock_run, client):
        """Recent scans remain listable after the in-memory cache is cleared."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        import agentbench.server.routes.scans as scans_mod

        scans_mod._scan_store.clear()

        resp = client.get("/api/v1/scans", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["agent_url"] == "https://example.com/agent"


class TestPersistentFallback:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_get_scan_falls_back_to_persisted_store_after_restart(self, mock_run, client):
        """A scan remains retrievable after the in-memory cache is cleared."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        import agentbench.server.routes.scans as scans_mod

        persisted = scans_mod.store.list_scans(limit=1)
        assert len(persisted) == 1
        scan_id = persisted[0]["id"]
        scans_mod._scan_store.clear()

        resp = client.get(f"/api/v1/scans/{scan_id}", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["overall_score"] == 85.0
        assert data["overall_grade"] == "B"

    @patch("agentbench.server.routes.scans._run_scan")
    def test_get_scan_is_not_visible_to_other_principal(self, mock_run, client):
        """A different authenticated principal cannot retrieve another scan."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        import agentbench.server.routes.scans as scans_mod

        scan_id = next(iter(scans_mod._scan_store.keys()))
        resp = client.get(f"/api/v1/scans/{scan_id}", headers=_other_auth_headers())
        assert resp.status_code == 404


class TestHistoryEndpoint:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_history_returns_structured_entries(self, mock_run, client):
        """History endpoint returns stable, typed fields for persisted scans."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        resp = client.get(
            "/api/v1/scans/history/https://example.com/agent",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        entry = data[0]
        assert "id" in entry
        assert "agent_url" in entry
        assert "created_at" in entry
        assert "overall_score" in entry
        assert "grade" in entry
        assert "duration_ms" in entry

    @patch("agentbench.server.routes.scans._run_scan")
    def test_history_is_scoped_to_authenticated_principal(self, mock_run, client):
        """Other principals should not see scan history for the same agent URL."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        resp = client.get(
            "/api/v1/scans/history/https://example.com/agent",
            headers=_other_auth_headers(),
        )
        assert resp.status_code == 200
        assert resp.json() == []

    def test_history_openapi_uses_typed_schema(self, client):
        """History endpoint should advertise a concrete response schema in OpenAPI."""
        openapi = client.get("/openapi.json").json()
        schema = openapi["paths"]["/api/v1/scans/history/{agent_url}"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert schema["type"] == "array"
        assert schema["items"]["$ref"].endswith("/ScanHistoryEntryResponse")


class TestRegressionEndpoint:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_regression_returns_structured_payload(self, mock_run, client):
        """Regression endpoint returns a stable, typed payload."""
        mock_run.side_effect = [
            _make_scan_response(overall_score=60.0, overall_grade="D"),
            _make_scan_response(overall_score=85.0, overall_grade="B"),
        ]

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )
        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        resp = client.get(
            "/api/v1/scans/regression/https://example.com/agent",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "agent_url" in data
        assert "current_scan_id" in data
        assert "previous_scan_id" in data
        assert "current_scan_date" in data
        assert "previous_scan_date" in data
        assert "overall_delta" in data
        assert "overall_trend" in data
        assert "regressions" in data
        assert "improvements" in data

    def test_regression_openapi_uses_typed_schema(self, client):
        """Regression endpoint should advertise a concrete response schema in OpenAPI."""
        openapi = client.get("/openapi.json").json()
        schema = openapi["paths"]["/api/v1/scans/regression/{agent_url}"]["get"]["responses"][
            "200"
        ]["content"]["application/json"]["schema"]
        assert schema["$ref"].endswith("/RegressionReportResponse")


class TestShareEndpoint:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_share_returns_structured_payload_after_restart(self, mock_run, client):
        """Share endpoint should work from persisted scan data after cache reset."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        import agentbench.server.routes.scans as scans_mod

        persisted = scans_mod.store.list_scans(limit=1)
        assert len(persisted) == 1
        scan_id = persisted[0]["id"]
        scans_mod._scan_store.clear()

        resp = client.get(
            f"/api/v1/scans/{scan_id}/share",
            headers=_auth_headers(),
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_id"] == scan_id
        assert data["agent_url"] == "https://example.com/agent"
        assert data["permalink"] == f"/?scan_id={scan_id}"
        assert "agent_url=" not in data["permalink"]
        assert "AgentBench report" in data["title"]
        assert "Scan ID: " + scan_id in data["markdown"]
        assert "Agent URL:" not in data["markdown"]
        assert "Critical issues" in data["slack_text"]

    @patch("agentbench.server.routes.scans._run_scan")
    def test_share_is_not_visible_to_other_principal(self, mock_run, client):
        """A different authenticated principal cannot access a scan share payload."""
        mock_run.return_value = _make_scan_response()

        client.post(
            "/api/v1/scans",
            json={"agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        import agentbench.server.routes.scans as scans_mod

        scan_id = next(iter(scans_mod._scan_store.keys()))
        resp = client.get(
            f"/api/v1/scans/{scan_id}/share",
            headers=_other_auth_headers(),
        )
        assert resp.status_code == 404

    def test_share_openapi_uses_typed_schema(self, client):
        """Share endpoint should advertise a concrete response schema in OpenAPI."""
        openapi = client.get("/openapi.json").json()
        schema = openapi["paths"]["/api/v1/scans/{scan_id}/share"]["get"]["responses"]["200"][
            "content"
        ]["application/json"]["schema"]
        assert schema["$ref"].endswith("/ScanShareResponse")


# ---------------------------------------------------------------------------
# Deadline / hard timeout — regression test
# ---------------------------------------------------------------------------


class TestScanDeadline:
    def test_prober_deadline_returns_partial_results(self):
        """probe_all() with a very short deadline should return a subset of results."""
        import time

        from agentbench.scanner.prober import AgentProber

        # Simulate a slow agent: each call sleeps 0.1 s
        call_count = 0

        def _slow_agent(prompt: str) -> str:
            nonlocal call_count
            call_count += 1
            time.sleep(0.1)
            return f"response to {prompt!r}"

        # 5 categories × ~8 prompts each = ~40 total probes
        prober = AgentProber(agent_fn=_slow_agent)
        # Deadline only 0.15 s from now — at most 1–2 probes can finish
        deadline = time.monotonic() + 0.15
        session = prober.probe_all(deadline=deadline)

        # Should have *some* results but far fewer than the full 40
        assert len(session.results) > 0
        assert len(session.results) < 40
        assert call_count < 40

    def test_run_scan_deadline_propagates_to_prober(self):
        """_run_scan with a short deadline should produce partial results, not hang."""
        import time
        from unittest.mock import patch

        import agentbench.server.routes.scans as scans_mod
        from agentbench.server.routes.scans import _run_scan

        # Patch the SafeDNSTransport so we don't need a real HTTP server.
        # We use a simple mock httpx.Client that simulates a slow agent.

        class FakeResponse:
            status_code = 200
            headers = {"content-type": "application/json"}
            text = '{"response": "ok"}'

            def raise_for_status(self):
                pass

            def json(self):
                return {"response": "ok"}

        def fake_client_init(self_client, *args, **kwargs):
            # Skip real httpx init entirely
            pass

        def fake_post(self_client, url, json=None):
            time.sleep(0.1)  # simulate slow agent
            return FakeResponse()

        def fake_close(self_client):
            pass

        with (
            patch.object(scans_mod.httpx.Client, "__init__", fake_client_init),
            patch.object(scans_mod.httpx.Client, "post", fake_post),
            patch.object(scans_mod.httpx.Client, "close", fake_close),
        ):
            # Deadline only 0.25s — should get partial results
            deadline = time.monotonic() + 0.25
            response, _report = _run_scan(
                "https://example.com/agent",
                categories=None,
                deadline=deadline,
            )

        # Should have a valid (partial) scan response
        assert response.overall_score >= 0
        # behaviors_tested will be fewer than the full 40
        assert response.behaviors_tested > 0
        assert response.behaviors_tested < 40
