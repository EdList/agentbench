"""Tests for project, saved-agent, and scan-policy APIs."""

from __future__ import annotations

import os
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

os.environ["AGENTBENCH_API_KEYS"] = "test-api-key,other-api-key"
os.environ["AGENTBENCH_SECRET_KEY"] = "test-secret-key-for-agentbench-32bytes"

from agentbench.server.auth import settings as auth_settings
from agentbench.server.models import Base
from agentbench.server.schemas import DomainScoreResponse, ScanResponse

auth_settings.api_keys = ["test-api-key", "other-api-key"]
auth_settings.secret_key = "test-secret-key-for-agentbench-32bytes"


@pytest.fixture()
def db_engine():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(db_engine) -> Generator[Session, None, None]:
    connection = db_engine.connect()
    transaction = connection.begin()
    test_session = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = test_session()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session) -> TestClient:
    with patch("agentbench.server.models.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value = db_session

        def _override_get_db():
            yield db_session

        from agentbench.server.app import create_app
        from agentbench.server.models import get_db

        application = create_app()
        application.dependency_overrides[get_db] = _override_get_db
        tc = TestClient(application)
        yield tc
        application.dependency_overrides.clear()


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


class TestProjectsApi:
    def test_create_project(self, client: TestClient):
        resp = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent", "description": "Customer support release gate"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "Support Agent"
        assert data["description"] == "Customer support release gate"
        assert data["id"]
        assert data["created_at"]

    def test_list_projects_is_scoped_to_principal(self, client: TestClient):
        client.post(
            "/api/v1/projects",
            json={"name": "Team One"},
            headers=_auth_headers(),
        )
        client.post(
            "/api/v1/projects",
            json={"name": "Team Two"},
            headers=_other_auth_headers(),
        )

        resp = client.get("/api/v1/projects", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert [project["name"] for project in data["projects"]] == ["Team One"]


class TestSavedAgentsApi:
    def test_create_saved_agent_under_project(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Production Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["project_id"] == project["id"]
        assert data["name"] == "Production Agent"
        assert data["agent_url"] == "https://example.com/agent"
        assert data["id"]

    def test_list_saved_agents_returns_project_agents(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        client.post(
            f"/api/v1/projects/{project['id']}/agents",
            json={"name": "Production Agent", "agent_url": "https://example.com/agent"},
            headers=_auth_headers(),
        )

        resp = client.get(f"/api/v1/projects/{project['id']}/agents", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["agents"][0]["name"] == "Production Agent"

    def test_other_principal_cannot_access_project_agents(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.get(f"/api/v1/projects/{project['id']}/agents", headers=_other_auth_headers())

        assert resp.status_code == 404


class TestScanPoliciesApi:
    def test_create_policy_under_project(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={
                "name": "Release Gate",
                "categories": ["safety", "reliability"],
                "minimum_overall_score": 80,
                "minimum_domain_scores": {"Safety": 90},
                "fail_on_critical_issues": True,
                "max_regression_delta": -5,
            },
            headers=_auth_headers(),
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["project_id"] == project["id"]
        assert data["name"] == "Release Gate"
        assert data["categories"] == ["safety", "reliability"]
        assert data["minimum_overall_score"] == 80
        assert data["minimum_domain_scores"] == {"Safety": 90}
        assert data["fail_on_critical_issues"] is True
        assert data["max_regression_delta"] == -5

    def test_policy_validation_rejects_unknown_category(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "categories": ["persona"], "minimum_domain_scores": {}},
            headers=_auth_headers(),
        )

        assert resp.status_code == 422
        assert "category" in resp.text.lower()

    def test_policy_validation_rejects_unknown_domain_threshold(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_domain_scores": {"Latency": 90}},
            headers=_auth_headers(),
        )

        assert resp.status_code == 422
        assert "domain" in resp.text.lower()

    def test_list_policies_returns_project_policies(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()
        client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_domain_scores": {}},
            headers=_auth_headers(),
        )

        resp = client.get(f"/api/v1/projects/{project['id']}/policies", headers=_auth_headers())

        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["policies"][0]["name"] == "Release Gate"

    def test_policy_validation_rejects_invalid_threshold(self, client: TestClient):
        project = client.post(
            "/api/v1/projects",
            json={"name": "Support Agent"},
            headers=_auth_headers(),
        ).json()

        resp = client.post(
            f"/api/v1/projects/{project['id']}/policies",
            json={"name": "Release Gate", "minimum_overall_score": 120},
            headers=_auth_headers(),
        )

        assert resp.status_code == 422


class TestProjectGateApi:
    @patch("agentbench.server.routes.scans._run_scan")
    def test_project_gate_returns_machine_friendly_verdict_payload(self, mock_run, client: TestClient):
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

        resp = client.post(
            f"/api/v1/projects/{project['id']}/gate",
            json={"agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_auth_headers(),
        )

        assert resp.status_code == 200
        data = resp.json()
        assert data["project_id"] == project["id"]
        assert data["agent_id"] == agent["id"]
        assert data["policy_id"] == policy["id"]
        assert data["release_verdict"] == "fail"
        assert any("overall score" in reason.lower() for reason in data["verdict_reasons"])
        assert data["scan_id"]
        assert data["permalink"] == f"/?scan_id={data['scan_id']}"
        assert data["overall_score"] == 72.0
        assert data["overall_grade"] == "C"

    @patch("agentbench.server.routes.scans._run_scan")
    def test_project_gate_rejects_cross_principal_access(self, mock_run, client: TestClient):
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

        resp = client.post(
            f"/api/v1/projects/{project['id']}/gate",
            json={"agent_id": agent["id"], "policy_id": policy["id"]},
            headers=_other_auth_headers(),
        )

        assert resp.status_code == 404
