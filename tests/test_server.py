"""Tests for the AgentBench Cloud API server."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from typing import Any, Generator
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ensure dev API key is available for tests
os.environ.setdefault("AGENTBENCH_API_KEYS", "test-api-key")
os.environ.setdefault("AGENTBENCH_SECRET_KEY", "test-secret-key")
os.environ["AGENTBENCH_DATABASE_URL"] = "sqlite:///:memory:"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

from agentbench.server.models import Base  # noqa: E402


@pytest.fixture()
def db_engine():
    """Create an in-memory SQLite engine with all tables."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    return engine


@pytest.fixture()
def db_session(db_engine) -> Generator[Session, None, None]:
    """Provide a transactional DB session that rolls back after each test."""
    connection = db_engine.connect()
    transaction = connection.begin()
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = TestSession()
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture()
def client(db_session) -> TestClient:
    """TestClient with DB session overridden."""
    # Patch the global session factory so the dependency uses our test session
    with patch("agentbench.server.models.get_session_factory") as mock_factory:
        mock_factory.return_value.return_value = db_session
        # We need get_db to yield our session
        def _override_get_db():
            yield db_session

        from agentbench.server.app import create_app

        application = create_app()
        from agentbench.server.models import get_db

        application.dependency_overrides[get_db] = _override_get_db
        tc = TestClient(application)
        yield tc
        application.dependency_overrides.clear()


API_KEY = "test-api-key"
HEADERS = {"X-API-Key": API_KEY}


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealthCheck:
    def test_health_endpoint(self, client: TestClient):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["version"] == "0.1.0"


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

class TestAuth:
    def test_no_api_key_returns_401(self, client: TestClient):
        resp = client.post("/api/v1/runs", json={"test_suite_code": "pass"})
        assert resp.status_code == 401

    def test_invalid_api_key_returns_401(self, client: TestClient):
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers={"X-API-Key": "wrong-key"},
        )
        assert resp.status_code == 401

    def test_valid_api_key_accepted(self, client: TestClient):
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "print('hello')"},
            headers=HEADERS,
        )
        assert resp.status_code == 201

    def test_bearer_jwt_accepted(self, client: TestClient):
        from agentbench.server.auth import create_access_token

        token = create_access_token("test-user")
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "print('hello')"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 201


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class TestRuns:
    def test_submit_run_with_code(self, client: TestClient):
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "from agentbench import AgentTest"},
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"
        assert data["id"]

    def test_submit_run_with_path(self, client: TestClient):
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_path": "/tmp/tests"},
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["status"] == "pending"

    def test_submit_run_requires_code_or_path(self, client: TestClient):
        resp = client.post(
            "/api/v1/runs",
            json={"name": "empty run"},
            headers=HEADERS,
        )
        assert resp.status_code == 422

    def test_list_runs_empty(self, client: TestClient):
        resp = client.get("/api/v1/runs", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["runs"] == []
        assert data["total"] == 0

    def test_list_runs_after_submit(self, client: TestClient):
        # Submit a run first
        client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers=HEADERS,
        )
        resp = client.get("/api/v1/runs", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert len(data["runs"]) >= 1

    def test_get_run_by_id(self, client: TestClient):
        submit = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers=HEADERS,
        )
        run_id = submit.json()["id"]
        resp = client.get(f"/api/v1/runs/{run_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["id"] == run_id

    def test_get_run_not_found(self, client: TestClient):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/runs/{fake_id}", headers=HEADERS)
        assert resp.status_code == 404

    def test_list_runs_pagination(self, client: TestClient):
        # Create a couple runs
        for _ in range(3):
            client.post(
                "/api/v1/runs",
                json={"test_suite_code": "pass"},
                headers=HEADERS,
            )
        resp = client.get("/api/v1/runs?limit=2&offset=0", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["runs"]) <= 2


# ---------------------------------------------------------------------------
# Trajectories
# ---------------------------------------------------------------------------

class TestTrajectories:
    def test_upload_trajectory(self, client: TestClient):
        resp = client.post(
            "/api/v1/trajectories",
            json={
                "name": "golden-run-1",
                "data": {
                    "steps": [
                        {"action": "tool_call", "tool_name": "search", "tool_input": {"q": "hello"}},
                        {"action": "response", "response": "Hello!"},
                    ],
                },
                "prompt": "Say hello",
                "tags": ["golden", "smoke"],
            },
            headers=HEADERS,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["name"] == "golden-run-1"
        assert data["step_count"] == 2
        assert "golden" in data["tags"]

    def test_list_trajectories(self, client: TestClient):
        resp = client.get("/api/v1/trajectories", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "trajectories" in data
        assert "total" in data

    def test_list_trajectories_pagination(self, client: TestClient):
        # Upload a few trajectories
        for i in range(3):
            client.post(
                "/api/v1/trajectories",
                json={
                    "name": f"traj-{i}",
                    "data": {"steps": [{"action": "response", "response": f"step {i}"}]},
                },
                headers=HEADERS,
            )
        resp = client.get("/api/v1/trajectories?limit=2&offset=0", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["trajectories"]) <= 2

    def test_diff_trajectory(self, client: TestClient):
        # Upload two trajectories to diff
        client.post(
            "/api/v1/trajectories",
            json={
                "name": "golden-for-diff",
                "data": {
                    "name": "golden",
                    "steps": [
                        {"action": "tool_call", "tool_name": "search", "tool_input": {"q": "test"}},
                    ],
                },
            },
            headers=HEADERS,
        )
        client.post(
            "/api/v1/trajectories",
            json={
                "name": "current-for-diff",
                "data": {
                    "name": "current",
                    "steps": [
                        {"action": "tool_call", "tool_name": "search", "tool_input": {"q": "test"}},
                    ],
                },
            },
            headers=HEADERS,
        )
        resp = client.get("/api/v1/trajectories/golden-for-diff/diff", headers=HEADERS)
        assert resp.status_code == 200
        data = resp.json()
        assert "golden_name" in data
        assert "current_name" in data
        assert "diffs" in data
        assert "summary" in data

    def test_diff_trajectory_not_found(self, client: TestClient):
        resp = client.get("/api/v1/trajectories/nonexistent/diff", headers=HEADERS)
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# JWT token auth
# ---------------------------------------------------------------------------

class TestJWT:
    def test_create_and_decode_token(self):
        from agentbench.server.auth import create_access_token, decode_access_token

        token = create_access_token("user-123")
        payload = decode_access_token(token)
        assert payload["sub"] == "user-123"
        assert "exp" in payload
        assert "iat" in payload

    def test_expired_token_rejected(self, client: TestClient):
        from datetime import timedelta

        from agentbench.server.auth import create_access_token

        token = create_access_token("user-123", expires_delta=timedelta(seconds=-1))
        resp = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_default_settings(self):
        from agentbench.server.config import ServerConfig

        cfg = ServerConfig()
        assert cfg.port == 8000
        assert cfg.host == "0.0.0.0"
        assert cfg.debug is False
        assert len(cfg.api_keys) >= 1  # at least dev-key

    def test_cors_origins_default(self):
        from agentbench.server.config import ServerConfig

        cfg = ServerConfig()
        assert "*" in cfg.cors_origins


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class TestModels:
    def test_create_tables_in_memory(self):
        from agentbench.server.models import Base

        engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=engine)
        # Verify tables were created
        from sqlalchemy import inspect as sa_inspect

        inspector = sa_inspect(engine)
        table_names = inspector.get_table_names()
        expected = {"users", "projects", "test_suites", "runs", "run_results", "trajectories"}
        assert expected.issubset(set(table_names))

    def test_run_model_defaults(self):
        from agentbench.server.models import Run

        run = Run(id=str(uuid.uuid4()), status="pending", total_tests=0, passed=0, failed=0)
        assert run.status == "pending"
        assert run.total_tests == 0
        assert run.passed == 0
        assert run.failed == 0

    def test_trajectory_model(self):
        from agentbench.server.models import Trajectory

        traj = Trajectory(id=str(uuid.uuid4()), name="test-traj", data="{}", step_count=0)
        assert traj.step_count == 0
        assert traj.name == "test-traj"
