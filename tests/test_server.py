"""Tests for the AgentBench Cloud API server."""

from __future__ import annotations

import os
import uuid
from collections.abc import Generator
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

# Ensure dev API key is available for tests
os.environ["AGENTBENCH_API_KEYS"] = "test-api-key,other-api-key"
os.environ["AGENTBENCH_SECRET_KEY"] = "test-secret-key-for-agentbench-32bytes"
os.environ["AGENTBENCH_DATABASE_URL"] = "sqlite:///:memory:"

from agentbench.server.auth import settings as auth_settings

auth_settings.api_keys = ["test-api-key", "other-api-key"]
auth_settings.secret_key = "test-secret-key-for-agentbench-32bytes"


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
    TestSession = sessionmaker(autocommit=False, autoflush=False, bind=connection)  # noqa: N806
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

    def test_api_key_principals_do_not_collide_on_shared_prefix(self, monkeypatch):
        from agentbench.server.auth import require_auth

        key_one = "sharedprefix-alpha"
        key_two = "sharedprefix-beta"
        monkeypatch.setattr(auth_settings, "api_keys", [key_one, key_two])

        principal_one = require_auth(api_key=key_one, credentials=None)
        principal_two = require_auth(api_key=key_two, credentials=None)

        assert principal_one != principal_two


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

    def test_runs_are_scoped_to_principal(self, client: TestClient):
        own = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers={"X-API-Key": "test-api-key"},
        ).json()
        other = client.post(
            "/api/v1/runs",
            json={"test_suite_code": "pass"},
            headers={"X-API-Key": "other-api-key"},
        ).json()

        own_list = client.get("/api/v1/runs", headers={"X-API-Key": "test-api-key"})
        other_list = client.get("/api/v1/runs", headers={"X-API-Key": "other-api-key"})

        assert own_list.status_code == 200
        assert other_list.status_code == 200
        assert [run["id"] for run in own_list.json()["runs"]] == [own["id"]]
        assert [run["id"] for run in other_list.json()["runs"]] == [other["id"]]

        forbidden = client.get(f"/api/v1/runs/{other['id']}", headers={"X-API-Key": "test-api-key"})
        assert forbidden.status_code == 404


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
                        {
                            "action": "tool_call",
                            "tool_name": "search",
                            "tool_input": {"q": "hello"},
                        },
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

    def test_trajectories_are_scoped_to_principal(self, client: TestClient):
        own = client.post(
            "/api/v1/trajectories",
            json={"name": "own-traj", "data": {"steps": []}},
            headers={"X-API-Key": "test-api-key"},
        ).json()
        client.post(
            "/api/v1/trajectories",
            json={"name": "other-traj", "data": {"steps": []}},
            headers={"X-API-Key": "other-api-key"},
        )

        own_list = client.get("/api/v1/trajectories", headers={"X-API-Key": "test-api-key"})
        other_list = client.get("/api/v1/trajectories", headers={"X-API-Key": "other-api-key"})

        assert own_list.status_code == 200
        assert other_list.status_code == 200
        assert [traj["id"] for traj in own_list.json()["trajectories"]] == [own["id"]]
        assert [traj["name"] for traj in other_list.json()["trajectories"]] == ["other-traj"]

        forbidden = client.get(
            "/api/v1/trajectories/other-traj/diff", headers={"X-API-Key": "test-api-key"}
        )
        assert forbidden.status_code == 404


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
    def test_production_config_requires_explicit_secret_and_api_keys(self, monkeypatch):
        from agentbench.server.config import ServerConfig

        monkeypatch.delenv("AGENTBENCH_SECRET_KEY", raising=False)
        monkeypatch.delenv("AGENTBENCH_API_KEYS", raising=False)
        monkeypatch.delenv("AGENTBENCH_DEBUG", raising=False)

        with pytest.raises(ValueError):
            ServerConfig()

    def test_debug_config_allows_dev_defaults(self, monkeypatch):
        from agentbench.server.config import ServerConfig

        monkeypatch.delenv("AGENTBENCH_SECRET_KEY", raising=False)
        monkeypatch.delenv("AGENTBENCH_API_KEYS", raising=False)
        monkeypatch.setenv("AGENTBENCH_DEBUG", "true")

        cfg = ServerConfig()
        assert cfg.debug is True
        assert cfg.secret_key == "dev-secret-change-me"
        assert cfg.api_keys == ["dev-key"]

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
        expected = {
            "users",
            "projects",
            "test_suites",
            "runs",
            "run_results",
            "trajectories",
            "scan_jobs",
        }
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


class TestStartup:
    def test_create_app_starts_on_empty_database(self, monkeypatch, tmp_path):
        import agentbench.server.models as models_mod
        from agentbench.server.app import create_app
        from agentbench.server.config import settings as server_settings

        monkeypatch.setattr(models_mod, "_engine", None, raising=False)
        monkeypatch.setattr(models_mod, "_SessionLocal", None, raising=False)
        monkeypatch.setattr(server_settings, "database_url", f"sqlite:///{tmp_path / 'fresh.db'}")

        with TestClient(create_app()) as client:
            response = client.get("/health")

        assert response.status_code == 200
