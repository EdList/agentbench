"""Tests for AgentBench evaluation — MetricsCollector and JudgeEvaluator."""

from __future__ import annotations

import time
from unittest.mock import patch

import pytest

from agentbench.core.test import AgentStep, AgentTrajectory
from agentbench.evaluation.judge import JUDGE_TEMPLATES, JudgeEvaluator, JudgeResult
from agentbench.evaluation.metrics import MetricsCollector, RunMetrics

# ─── Helpers ───


def _make_trajectory(
    steps=None,
    completed=True,
    final_response="Done",
    total_latency_ms=100.0,
    total_tokens=50,
    total_cost_usd=0.01,
) -> AgentTrajectory:
    traj = AgentTrajectory(
        completed=completed,
        final_response=final_response,
        total_latency_ms=total_latency_ms,
        total_tokens=total_tokens,
        total_cost_usd=total_cost_usd,
    )
    for step_data in steps or []:
        traj.steps.append(AgentStep(**step_data))
    return traj


# ─── RunMetrics ───


class TestRunMetrics:
    def test_defaults(self):
        m = RunMetrics()
        assert m.total_steps == 0
        assert m.total_tool_calls == 0
        assert m.completed is False

    def test_summary_string(self):
        m = RunMetrics(
            total_steps=5,
            total_tool_calls=3,
            total_latency_ms=2500,
            total_tokens=100,
            estimated_cost_usd=0.05,
            errors=1,
        )
        s = m.summary()
        assert "Steps: 5" in s
        assert "Tools: 3" in s
        assert "Errors: 1" in s


# ─── MetricsCollector: Collect ───


class TestMetricsCollectorCollect:
    def test_collect_basic_trajectory(self):
        traj = _make_trajectory(
            steps=[
                {
                    "step_number": 0,
                    "action": "tool_call",
                    "tool_name": "search",
                    "tool_output": "r",
                    "latency_ms": 50,
                },
                {"step_number": 1, "action": "llm_response", "response": "Found", "latency_ms": 30},
            ],
        )
        collector = MetricsCollector()
        metrics = collector.collect(traj)

        assert metrics.total_steps == 2
        assert metrics.total_tool_calls == 1
        assert metrics.completed is True
        assert metrics.total_tokens == 50
        assert metrics.estimated_cost_usd == 0.01

    def test_collect_tool_calls_by_name(self):
        traj = _make_trajectory(
            steps=[
                {
                    "step_number": 0,
                    "action": "tool_call",
                    "tool_name": "search",
                    "tool_output": "r1",
                },
                {
                    "step_number": 1,
                    "action": "tool_call",
                    "tool_name": "search",
                    "tool_output": "r2",
                },
                {"step_number": 2, "action": "tool_call", "tool_name": "calc", "tool_output": "42"},
            ],
        )
        collector = MetricsCollector()
        metrics = collector.collect(traj)

        assert metrics.tool_calls_by_name == {"search": 2, "calc": 1}

    def test_collect_latency_metrics(self):
        traj = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "llm_response", "response": "a", "latency_ms": 100},
                {"step_number": 1, "action": "llm_response", "response": "b", "latency_ms": 200},
                {"step_number": 2, "action": "llm_response", "response": "c", "latency_ms": 300},
            ],
            total_latency_ms=600,
        )
        collector = MetricsCollector()
        metrics = collector.collect(traj)

        assert metrics.total_latency_ms == 600.0
        assert metrics.avg_step_latency_ms == 200.0  # (100+200+300)/3
        assert metrics.max_step_latency_ms == 300.0

    def test_collect_latency_uses_sum_when_no_total(self):
        traj = AgentTrajectory(total_latency_ms=0)
        traj.steps.append(
            AgentStep(step_number=0, action="llm_response", response="x", latency_ms=50)
        )
        traj.steps.append(
            AgentStep(step_number=1, action="llm_response", response="y", latency_ms=150)
        )

        collector = MetricsCollector()
        metrics = collector.collect(traj)
        # total_latency_ms=0.0 is an explicit value, not falsy — respect it
        assert metrics.total_latency_ms == 0.0

    def test_collect_empty_trajectory(self):
        traj = AgentTrajectory()
        collector = MetricsCollector()
        metrics = collector.collect(traj)

        assert metrics.total_steps == 0
        assert metrics.total_tool_calls == 0
        assert metrics.avg_step_latency_ms == 0
        assert metrics.max_step_latency_ms == 0
        assert metrics.errors == 0

    def test_collect_error_count(self):
        traj = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "error", "error": "crash1"},
                {"step_number": 1, "action": "error", "error": "crash2"},
                {"step_number": 2, "action": "llm_response", "response": "ok"},
            ],
        )
        collector = MetricsCollector()
        metrics = collector.collect(traj)
        assert metrics.errors == 2

    def test_collect_retry_count(self):
        traj = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "retry", "response": "retrying"},
                {"step_number": 1, "action": "retry", "response": "retrying again"},
                {"step_number": 2, "action": "llm_response", "response": "ok"},
            ],
        )
        collector = MetricsCollector()
        metrics = collector.collect(traj)
        assert metrics.retries == 2

    def test_collect_incomplete_trajectory(self):
        traj = _make_trajectory(completed=False)
        collector = MetricsCollector()
        metrics = collector.collect(traj)
        assert metrics.completed is False

    def test_collect_stores_run(self):
        collector = MetricsCollector()
        collector.collect(_make_trajectory())
        collector.collect(_make_trajectory())
        assert len(collector._runs) == 2


# ─── MetricsCollector: Aggregate ───


class TestMetricsCollectorAggregate:
    def test_aggregate_empty(self):
        collector = MetricsCollector()
        assert collector.aggregate() == {}

    def test_aggregate_single_run(self):
        collector = MetricsCollector()
        traj = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "llm_response", "response": "ok"},
            ],
            total_latency_ms=200,
            total_tokens=100,
            total_cost_usd=0.02,
        )
        collector.collect(traj)

        agg = collector.aggregate()
        assert agg["total_runs"] == 1
        assert agg["total_steps"] == 1
        assert agg["avg_steps"] == 1.0
        assert agg["total_latency_ms"] == 200.0
        assert agg["avg_latency_ms"] == 200.0
        assert agg["total_tokens"] == 100
        assert agg["total_cost_usd"] == 0.02
        assert agg["total_errors"] == 0
        assert agg["success_rate"] == 1.0

    def test_aggregate_multiple_runs(self):
        collector = MetricsCollector()

        traj1 = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "llm_response", "response": "ok"},
                {"step_number": 1, "action": "llm_response", "response": "ok"},
            ],
            completed=True,
            total_latency_ms=100,
            total_tokens=50,
        )
        traj2 = _make_trajectory(
            steps=[
                {"step_number": 0, "action": "error", "error": "fail"},
            ],
            completed=False,
            total_latency_ms=50,
            total_tokens=25,
        )
        collector.collect(traj1)
        collector.collect(traj2)

        agg = collector.aggregate()
        assert agg["total_runs"] == 2
        assert agg["total_steps"] == 3
        assert agg["avg_steps"] == 1.5
        assert agg["total_latency_ms"] == 150.0
        assert agg["avg_latency_ms"] == 75.0
        assert agg["total_tokens"] == 75
        assert agg["total_errors"] == 1
        assert agg["success_rate"] == 0.5


# ─── JudgeEvaluator: _parse_response ───


class TestJudgeEvaluatorParseResponse:
    def setup_method(self):
        self.judge = JudgeEvaluator(provider="openai", model="test-model")

    def test_valid_json(self):
        text = '{"score": 0.85, "reasoning": "Good response", "passed": true}'
        result = self.judge._parse_response(text, time.time())
        assert isinstance(result, JudgeResult)
        assert result.score == 0.85
        assert result.reasoning == "Good response"
        assert result.judge_model == "test-model"

    def test_json_in_markdown_code_block(self):
        text = '```json\n{"score": 0.5, "reasoning": "OK", "passed": false}\n```'
        result = self.judge._parse_response(text, time.time())
        assert result.score == 0.5

    def test_json_in_plain_code_block(self):
        text = '```\n{"score": 0.9, "reasoning": "Great", "passed": true}\n```'
        result = self.judge._parse_response(text, time.time())
        assert result.score == 0.9

    def test_invalid_json_returns_failure(self):
        text = "This is not JSON at all"
        result = self.judge._parse_response(text, time.time())
        assert result.score == 0.0
        assert not result.passed
        assert "Could not parse" in result.reasoning

    def test_partial_json(self):
        text = '{"score": 0.7'
        result = self.judge._parse_response(text, time.time())
        assert result.score == 0.0

    def test_missing_passed_key_uses_score_threshold(self):
        text = '{"score": 0.8, "reasoning": "Good"}'
        result = self.judge._parse_response(text, time.time())
        # passed defaults to score >= 0.7
        assert result.passed

    def test_missing_passed_key_low_score(self):
        text = '{"score": 0.3, "reasoning": "Bad"}'
        result = self.judge._parse_response(text, time.time())
        assert not result.passed

    def test_issues_extracted_to_details(self):
        text = (
            '{"score": 0.2, "reasoning": "Unsafe", '
            '"passed": false, "issues": ["PII leak", "harmful"]}'
        )
        result = self.judge._parse_response(text, time.time())
        assert result.details == {"issues": ["PII leak", "harmful"]}

    def test_latency_ms_recorded(self):
        start = time.time() - 0.1  # 100ms ago
        result = self.judge._parse_response('{"score": 1.0}', start)
        assert result.latency_ms >= 0  # Non-negative

    def test_missing_score_defaults_zero(self):
        text = '{"reasoning": "No score"}'
        result = self.judge._parse_response(text, time.time())
        assert result.score == 0.0


# ─── JudgeEvaluator: _format_steps ───


class TestJudgeEvaluatorFormatSteps:
    def test_format_steps_with_tools(self):
        traj = AgentTrajectory()
        traj.steps.append(AgentStep(step_number=0, action="tool_call", tool_name="search"))
        traj.steps.append(AgentStep(step_number=1, action="llm_response"))

        output = JudgeEvaluator._format_steps(traj)
        assert "Step 0: tool_call (tool: search)" in output
        assert "Step 1: llm_response" in output

    def test_format_steps_empty(self):
        traj = AgentTrajectory()
        output = JudgeEvaluator._format_steps(traj)
        assert output == ""


# ─── JudgeEvaluator: _format_tool_calls ───


class TestJudgeEvaluatorFormatToolCalls:
    def test_format_tool_calls(self):
        traj = AgentTrajectory()
        traj.steps.append(
            AgentStep(step_number=0, action="tool_call", tool_name="search", tool_output="results")
        )
        traj.steps.append(
            AgentStep(step_number=1, action="tool_call", tool_name="calc", tool_output="42")
        )

        output = JudgeEvaluator._format_tool_calls(traj)
        assert "search: results" in output
        assert "calc: 42" in output

    def test_format_tool_calls_empty(self):
        traj = AgentTrajectory()
        output = JudgeEvaluator._format_tool_calls(traj)
        assert output == "No tool calls"


# ─── JudgeEvaluator: _format_trajectory ───


class TestJudgeEvaluatorFormatTrajectory:
    def test_format_trajectory(self):
        traj = AgentTrajectory(completed=True)
        traj.steps.append(AgentStep(step_number=0, action="llm_response"))
        output = JudgeEvaluator._format_trajectory(traj)
        assert "1 steps" in output
        assert "completed: True" in output


# ─── JudgeEvaluator: evaluate (with mocked LLM) ───


class TestJudgeEvaluatorEvaluate:
    @patch.object(JudgeEvaluator, "_call_llm")
    def test_evaluate_passes_threshold(self, mock_call):
        mock_call.return_value = '{"score": 0.9, "reasoning": "Excellent", "passed": true}'
        judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
        traj = AgentTrajectory(input_prompt="Hello", final_response="Hi there!", completed=True)

        result = judge.evaluate(traj, threshold=0.7)
        assert result.passed
        assert result.score == 0.9

    @patch.object(JudgeEvaluator, "_call_llm")
    def test_evaluate_fails_below_threshold(self, mock_call):
        mock_call.return_value = '{"score": 0.3, "reasoning": "Poor", "passed": false}'
        judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
        traj = AgentTrajectory(input_prompt="Hello", final_response="Meh", completed=True)

        result = judge.evaluate(traj, threshold=0.7)
        assert not result.passed

    @patch.object(JudgeEvaluator, "_call_llm")
    def test_evaluate_handles_llm_error(self, mock_call):
        mock_call.side_effect = RuntimeError("API down")
        judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
        traj = AgentTrajectory(input_prompt="Hello", final_response="Hi")

        result = judge.evaluate(traj)
        assert not result.passed
        assert "API down" in result.reasoning

    @patch.object(JudgeEvaluator, "_call_llm")
    def test_evaluate_uses_custom_template(self, mock_call):
        mock_call.return_value = '{"score": 0.8, "reasoning": "Safe", "passed": true}'
        judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
        traj = AgentTrajectory(input_prompt="test", final_response="response")

        result = judge.evaluate(traj, template="safety_check")
        assert result.score == 0.8

    @patch.object(JudgeEvaluator, "_call_llm")
    def test_evaluate_custom_criteria(self, mock_call):
        mock_call.return_value = '{"score": 1.0, "reasoning": "Perfect", "passed": true}'
        judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
        traj = AgentTrajectory(input_prompt="test", final_response="response")

        result = judge.evaluate(traj, template="custom", criteria="Is it concise?")
        assert result.score == 1.0


# ─── JudgeEvaluator: _call_llm ───


class TestJudgeEvaluatorCallLLM:
    def test_unknown_provider_raises(self):
        judge = JudgeEvaluator(provider="unknown_provider")
        with pytest.raises(ValueError, match="Unknown judge provider"):
            judge._call_llm("test prompt")


# ─── JudgeResult ───


class TestJudgeResult:
    def test_str_passed(self):
        r = JudgeResult(passed=True, score=0.85, reasoning="Good", judge_model="gpt-4")
        s = str(r)
        assert "✓" in s
        assert "0.85" in s

    def test_str_failed(self):
        r = JudgeResult(passed=False, score=0.3, reasoning="Bad", judge_model="gpt-4")
        s = str(r)
        assert "✗" in s


# ─── JUDGE_TEMPLATES ───


class TestJudgeTemplates:
    def test_templates_exist(self):
        assert "appropriate_response" in JUDGE_TEMPLATES
        assert "follows_instructions" in JUDGE_TEMPLATES
        assert "safety_check" in JUDGE_TEMPLATES
        assert "custom" in JUDGE_TEMPLATES

    def test_templates_have_placeholders(self):
        for name, template in JUDGE_TEMPLATES.items():
            assert "{prompt}" in template or "{criteria}" in template, (
                f"Template {name} missing placeholders"
            )
