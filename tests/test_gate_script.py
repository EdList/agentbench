"""Tests for the GitHub Actions AgentBench gate helper script."""

from __future__ import annotations

import json
import runpy
from pathlib import Path

import pytest

SCRIPT_PATH = Path(__file__).resolve().parent.parent / "scripts" / "agentbench_gate.py"


class DummyResponse:
    def __init__(self, payload: dict, status_code: int = 200):
        self._payload = payload
        self.status_code = status_code
        self.text = json.dumps(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}: {self.text}")

    def json(self) -> dict:
        return self._payload


def _set_required_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[Path, Path]:
    output_path = tmp_path / "github_output.txt"
    summary_path = tmp_path / "github_summary.md"
    monkeypatch.setenv("AGENTBENCH_BASE_URL", "https://agentbench.example.com")
    monkeypatch.setenv("AGENTBENCH_API_KEY", "secret-api-key")
    monkeypatch.setenv("AGENTBENCH_PROJECT_ID", "proj_123")
    monkeypatch.setenv("AGENTBENCH_AGENT_ID", "agent_123")
    monkeypatch.setenv("AGENTBENCH_POLICY_ID", "policy_123")
    monkeypatch.setenv("AGENTBENCH_POLL_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("AGENTBENCH_TIMEOUT_SECONDS", "1")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary_path))
    return output_path, summary_path


def _job_created_payload() -> dict:
    return {
        "job_id": "job_123",
        "status": "queued",
        "cancel_requested": False,
        "project_id": "proj_123",
        "agent_id": "agent_123",
        "policy_id": "policy_123",
        "agent_url": "https://agentbench.example.com/agent",
        "scan_id": None,
        "release_verdict": None,
        "verdict_reasons": [],
        "overall_score": None,
        "overall_grade": None,
        "permalink": None,
        "error_detail": None,
    }


def _terminal_payload(*, verdict: str, score: float, grade: str, scan_id: str, reasons: list[str] | None = None) -> dict:
    return {
        "job_id": "job_123",
        "status": "completed",
        "cancel_requested": False,
        "project_id": "proj_123",
        "agent_id": "agent_123",
        "policy_id": "policy_123",
        "agent_url": "https://agentbench.example.com/agent",
        "scan_id": scan_id,
        "release_verdict": verdict,
        "verdict_reasons": reasons or [],
        "overall_score": score,
        "overall_grade": grade,
        "permalink": f"/?scan_id={scan_id}",
        "error_detail": None,
    }


def test_gate_script_exits_zero_and_writes_outputs_on_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    output_path, summary_path = _set_required_env(monkeypatch, tmp_path)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        assert url == "https://agentbench.example.com/api/v1/projects/proj_123/gate/jobs"
        assert headers["X-API-Key"] == "secret-api-key"
        assert json == {"agent_id": "agent_123", "policy_id": "policy_123"}
        return DummyResponse(_job_created_payload())

    poll_responses = iter(
        [
            DummyResponse({**_job_created_payload(), "status": "running"}),
            DummyResponse(_terminal_payload(verdict="pass", score=91.0, grade="A", scan_id="scan_123")),
        ]
    )

    def fake_get(url: str, *, headers: dict, timeout: float):
        assert url == "https://agentbench.example.com/api/v1/scans/jobs/job_123"
        assert headers["X-API-Key"] == "secret-api-key"
        return next(poll_responses)

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    assert "PASS" in stdout
    assert "job_123" in stdout
    output_text = output_path.read_text()
    assert "scan_id=scan_123" in output_text
    assert "release_verdict=pass" in output_text
    assert "permalink=/?scan_id=scan_123" in output_text
    assert "report_url=https://agentbench.example.com/?scan_id=scan_123" in output_text
    assert "pr_comment_body<<" in output_text
    summary_text = summary_path.read_text()
    assert "## AgentBench Gate" in summary_text
    assert "https://agentbench.example.com/?scan_id=scan_123" in summary_text


def test_gate_script_exits_one_on_fail_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    _set_required_env(monkeypatch, tmp_path)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return DummyResponse(_job_created_payload())

    def fake_get(url: str, *, headers: dict, timeout: float):
        return DummyResponse(
            _terminal_payload(
                verdict="fail",
                score=72.0,
                grade="C",
                scan_id="scan_456",
                reasons=["Overall score 72.0 is below the required 80.0."],
            )
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert exc_info.value.code == 1
    stdout = capsys.readouterr().out
    assert "FAIL" in stdout
    assert "Overall score 72.0 is below the required 80.0." in stdout


def test_gate_script_can_fail_on_warn_when_strict_mode_enabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    _set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv("AGENTBENCH_FAIL_ON_WARN", "true")

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return DummyResponse(_job_created_payload())

    def fake_get(url: str, *, headers: dict, timeout: float):
        return DummyResponse(
            _terminal_payload(
                verdict="warn",
                score=84.0,
                grade="B",
                scan_id="scan_warn",
                reasons=["A domain regressed by 6.0 points."],
            )
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert exc_info.value.code == 1


def test_gate_script_writes_pr_comment_body_with_reasons(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
):
    output_path, _ = _set_required_env(monkeypatch, tmp_path)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return DummyResponse(_job_created_payload())

    def fake_get(url: str, *, headers: dict, timeout: float):
        return DummyResponse(
            _terminal_payload(
                verdict="fail",
                score=65.0,
                grade="D",
                scan_id="scan_999",
                reasons=[
                    "Overall score 65.0 is below the required 80.0.",
                    "Critical issues were detected.",
                ],
            )
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    with pytest.raises(SystemExit):
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    output_text = output_path.read_text()
    assert "pr_comment_body<<" in output_text
    assert "**Verdict:** FAIL" in output_text
    assert "Overall score 65.0 is below the required 80.0." in output_text
    assert "https://agentbench.example.com/?scan_id=scan_999" in output_text


def test_gate_script_exits_two_on_unknown_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
):
    _set_required_env(monkeypatch, tmp_path)

    def fake_post(url: str, *, headers: dict, json: dict, timeout: float):
        return DummyResponse(_job_created_payload())

    def fake_get(url: str, *, headers: dict, timeout: float):
        return DummyResponse(
            _terminal_payload(
                verdict="mystery",
                score=65.0,
                grade="D",
                scan_id="scan_weird",
            )
        )

    monkeypatch.setattr("httpx.post", fake_post)
    monkeypatch.setattr("httpx.get", fake_get)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert exc_info.value.code == 2
    assert "Unexpected AgentBench release verdict" in capsys.readouterr().out


def test_gate_script_exits_two_when_required_env_is_missing(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
):
    monkeypatch.delenv("AGENTBENCH_BASE_URL", raising=False)
    monkeypatch.delenv("AGENTBENCH_API_KEY", raising=False)
    monkeypatch.delenv("AGENTBENCH_PROJECT_ID", raising=False)
    monkeypatch.delenv("AGENTBENCH_AGENT_ID", raising=False)
    monkeypatch.delenv("AGENTBENCH_POLICY_ID", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        runpy.run_path(str(SCRIPT_PATH), run_name="__main__")

    assert exc_info.value.code == 2
    assert "Missing required environment variables" in capsys.readouterr().out
