"""Tests for the dashboard module — API endpoints."""

from __future__ import annotations

from pathlib import Path

import pytest

from agentbench.dashboard.app import create_dashboard_app
from agentbench.recorder.workflow import Turn, Workflow
from agentbench.replayer.report import ReplayReport


def _make_turn(index: int, msg: str, resp: str) -> Turn:
    return Turn(
        index=index, user_message=msg, agent_response=resp,
        latency_ms=100.0, timestamp="2025-01-01T00:00:00",
    )


def _save_workflow(name: str, turns: list[Turn], tmp_path: Path) -> None:
    wf = Workflow(
        name=name,
        agent_url="https://api.example.com/v1/chat/completions",
        agent_format="openai",
        turns=turns,
        total_duration_ms=len(turns) * 100.0,
    )
    wf.save(base_dir=tmp_path)


def _save_report(
    workflow_name: str, replay_of: str, score: float, passed: bool,
    tmp_path: Path,
) -> Path:
    report = ReplayReport(
        workflow_name=workflow_name,
        replay_of=replay_of,
        overall_score=score,
        passed=passed,
        created_at="2025-06-01T12:00:00",
    )
    reports_dir = tmp_path / ".agentbench" / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / f"{workflow_name}-20250601-120000.json"
    path.write_text(report.to_json())
    return path


@pytest.fixture
def app(tmp_path: Path):
    """Create a dashboard app pointing at tmp_path."""
    _save_workflow("flow-a", [_make_turn(0, "hi", "hello")], tmp_path)
    _save_workflow("flow-b", [_make_turn(0, "bye", "goodbye")], tmp_path)
    _save_report("flow-a-replay", "flow-a", 0.95, True, tmp_path)
    _save_report("flow-b-replay", "flow-b", 0.6, False, tmp_path)

    return create_dashboard_app(base_dir=tmp_path)


@pytest.fixture
def client(app):
    """Create a test client using httpx ASGI transport."""
    from starlette.testclient import TestClient

    return TestClient(app)


# ---------------------------------------------------------------------------
# GET /api/stats
# ---------------------------------------------------------------------------


class TestStatsEndpoint:
    def test_stats_basic(self, client):
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_workflows"] == 2
        assert data["total_reports"] == 2
        assert data["regression_count"] == 1
        assert data["healthy"] is False
        assert 0 < data["average_score"] < 1

    def test_stats_empty(self, tmp_path: Path):
        app = create_dashboard_app(base_dir=tmp_path)
        from starlette.testclient import TestClient

        c = TestClient(app)
        resp = c.get("/api/stats")
        data = resp.json()
        assert data["total_workflows"] == 0
        assert data["total_reports"] == 0
        assert data["healthy"] is True
        assert data["average_score"] == 1.0


# ---------------------------------------------------------------------------
# GET /api/workflows
# ---------------------------------------------------------------------------


class TestWorkflowsEndpoint:
    def test_list_workflows(self, client):
        resp = client.get("/api/workflows")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        names = [w["name"] for w in data]
        assert "flow-a" in names
        assert "flow-b" in names

    def test_workflow_has_latest_score(self, client):
        resp = client.get("/api/workflows")
        data = resp.json()
        flow_a = next(w for w in data if w["name"] == "flow-a")
        assert flow_a["latest_score"] == 0.95
        assert flow_a["latest_passed"] is True

    def test_workflow_has_turn_count(self, client):
        resp = client.get("/api/workflows")
        data = resp.json()
        flow_a = next(w for w in data if w["name"] == "flow-a")
        assert flow_a["turn_count"] == 1


# ---------------------------------------------------------------------------
# GET /api/workflows/{name}
# ---------------------------------------------------------------------------


class TestWorkflowDetailEndpoint:
    def test_workflow_detail(self, client):
        resp = client.get("/api/workflows/flow-a")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "flow-a"
        assert data["turn_count"] == 1
        assert data["user_messages"] == ["hi"]
        assert data["tool_call_sequence"] == []

    def test_workflow_not_found(self, client):
        resp = client.get("/api/workflows/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# GET /api/reports
# ---------------------------------------------------------------------------


class TestReportsEndpoint:
    def test_list_reports(self, client):
        resp = client.get("/api/reports")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert len(data["reports"]) == 2

    def test_pagination(self, client):
        resp = client.get("/api/reports?limit=1&offset=0")
        data = resp.json()
        assert data["total"] == 2
        assert len(data["reports"]) == 1


# ---------------------------------------------------------------------------
# GET /api/timeline
# ---------------------------------------------------------------------------


class TestTimelineEndpoint:
    def test_timeline(self, client):
        resp = client.get("/api/timeline")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["workflow"] in ("flow-a", "flow-b")
        assert "score" in data[0]
        assert "passed" in data[0]

    def test_timeline_empty(self, tmp_path: Path):
        app = create_dashboard_app(base_dir=tmp_path)
        from starlette.testclient import TestClient

        c = TestClient(app)
        resp = c.get("/api/timeline")
        assert resp.json() == []


# ---------------------------------------------------------------------------
# GET / (dashboard HTML)
# ---------------------------------------------------------------------------


class TestDashboardUI:
    def test_serves_html(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "text/html" in resp.headers.get("content-type", "")
