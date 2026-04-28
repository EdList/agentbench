"""Tests for the gate module — CI gate runner."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentbench.gate.runner import GateResult, GateRunner, WorkflowGateResult
from agentbench.recorder.workflow import Turn, Workflow


def _make_response(json_data: dict) -> httpx.Response:
    return httpx.Response(
        status_code=200, json=json_data, request=MagicMock(),
    )


def _make_turn(index: int, msg: str, resp: str) -> Turn:
    return Turn(
        index=index, user_message=msg, agent_response=resp,
        latency_ms=100.0, timestamp="2025-01-01T00:00:00",
    )


def _openai_resp(content: str) -> httpx.Response:
    return _make_response({
        "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
        "model": "test",
    })


def _save_workflow(name: str, turns: list[Turn], tmp_path: Path) -> None:
    wf = Workflow(
        name=name,
        agent_url="https://api.example.com/v1/chat/completions",
        agent_format="openai",
        turns=turns,
        total_duration_ms=len(turns) * 100.0,
    )
    wf.save(base_dir=tmp_path)


# ---------------------------------------------------------------------------
# GateRunner
# ---------------------------------------------------------------------------


class TestGateRunner:
    def test_no_workflows_passes(self, tmp_path: Path):
        runner = GateRunner(
            agent_url="https://example.com",
            workflows_dir=tmp_path,
        )
        result = runner.run()
        assert result.passed
        assert result.total_workflows == 0
        assert result.pass_rate == 1.0

    def test_single_workflow_pass(self, tmp_path: Path):
        _save_workflow("flow-a", [_make_turn(0, "hi", "hello")], tmp_path)

        mock_post = MagicMock(return_value=_openai_resp("hello"))

        with patch.object(httpx.Client, "post", mock_post):
            runner = GateRunner(
                agent_url="https://example.com",
                workflows_dir=tmp_path,
            )
            result = runner.run(workflow_names=["flow-a"])

        assert result.passed
        assert result.passed_workflows == 1
        assert result.failed_workflows == 0

    def test_single_workflow_fail(self, tmp_path: Path):
        _save_workflow("flow-a", [_make_turn(0, "hi", "hello")], tmp_path)

        mock_post = MagicMock(
            return_value=_openai_resp("something completely different and unrelated"),
        )

        with patch.object(httpx.Client, "post", mock_post):
            runner = GateRunner(
                agent_url="https://example.com",
                workflows_dir=tmp_path,
                threshold=0.99,  # Very high threshold to force failure
            )
            result = runner.run(workflow_names=["flow-a"])

        assert not result.passed
        assert result.failed_workflows == 1

    def test_multiple_workflows(self, tmp_path: Path):
        _save_workflow(
            "flow-a",
            [_make_turn(0, "hi", "hello")],
            tmp_path,
        )
        _save_workflow(
            "flow-b",
            [_make_turn(0, "bye", "goodbye")],
            tmp_path,
        )

        call_count = 0

        def _mock_post(self_client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _openai_resp("ok")

        with patch.object(httpx.Client, "post", _mock_post):
            runner = GateRunner(
                agent_url="https://example.com",
                workflows_dir=tmp_path,
                threshold=0.3,
            )
            result = runner.run(workflow_names=["flow-a", "flow-b"])

        assert result.total_workflows == 2
        assert result.total_turns == 2

    def test_missing_workflow(self, tmp_path: Path):
        runner = GateRunner(
            agent_url="https://example.com",
            workflows_dir=tmp_path,
        )
        result = runner.run(workflow_names=["nonexistent"])

        assert not result.passed
        assert result.workflow_results[0].error is not None
        assert "not found" in result.workflow_results[0].error

    def test_discover_all_workflows(self, tmp_path: Path):
        _save_workflow("alpha", [_make_turn(0, "a", "b")], tmp_path)
        _save_workflow("beta", [_make_turn(0, "c", "d")], tmp_path)

        mock_post = MagicMock(return_value=_openai_resp("ok"))

        with patch.object(httpx.Client, "post", mock_post):
            runner = GateRunner(
                agent_url="https://example.com",
                workflows_dir=tmp_path,
                threshold=0.3,
            )
            result = runner.run()  # No workflow_names → discovers all

        assert result.total_workflows == 2

    def test_pass_rate_property(self, tmp_path: Path):
        _save_workflow("ok-flow", [_make_turn(0, "hi", "hello")], tmp_path)

        mock_post = MagicMock(return_value=_openai_resp("hello"))

        with patch.object(httpx.Client, "post", mock_post):
            runner = GateRunner(
                agent_url="https://example.com",
                workflows_dir=tmp_path,
            )
            result = runner.run(workflow_names=["ok-flow"])

        assert result.pass_rate == 1.0

    def test_pass_rate_zero(self):
        result = GateResult(total_workflows=3, failed_workflows=3)
        result.passed = False
        assert result.pass_rate == 0.0

    def test_timestamps(self, tmp_path: Path):
        runner = GateRunner(
            agent_url="https://example.com",
            workflows_dir=tmp_path,
        )
        result = runner.run()
        assert result.started_at
        assert result.finished_at
        assert "T" in result.started_at


# ---------------------------------------------------------------------------
# WorkflowGateResult
# ---------------------------------------------------------------------------


class TestWorkflowGateResult:
    def test_defaults(self):
        wr = WorkflowGateResult(
            workflow_name="test",
            passed=True,
            score=0.95,
            turn_count=2,
            pass_count=2,
            fail_count=0,
        )
        assert wr.error is None
        assert wr.report is None

    def test_with_error(self):
        wr = WorkflowGateResult(
            workflow_name="test",
            passed=False,
            score=0.0,
            turn_count=0,
            pass_count=0,
            fail_count=0,
            error="Something broke",
        )
        assert wr.error == "Something broke"


# ---------------------------------------------------------------------------
# GateResult
# ---------------------------------------------------------------------------


class TestGateResult:
    def test_empty(self):
        result = GateResult()
        assert result.passed
        assert result.pass_rate == 1.0

    def test_mixed_results(self):
        result = GateResult(
            passed=False,
            total_workflows=3,
            passed_workflows=2,
            failed_workflows=1,
        )
        assert result.pass_rate == pytest.approx(2 / 3)
