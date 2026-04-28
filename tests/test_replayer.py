"""Tests for the replayer module — diff engine, replay engine, and reports."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentbench.recorder.workflow import ToolCall, Turn, Workflow
from agentbench.replayer.diff import (
    WorkflowDiffer,
    _args_similarity,
    _string_similarity,
)
from agentbench.replayer.replayer import ReplayEngine
from agentbench.replayer.report import ReplayReport, TurnResult


def _make_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=MagicMock(),
    )


def _make_turn(
    index: int, msg: str, resp: str, tools: list[ToolCall] | None = None,
) -> Turn:
    return Turn(
        index=index,
        user_message=msg,
        agent_response=resp,
        tool_calls=tools or [],
        latency_ms=100.0,
        timestamp="2025-01-01T00:00:00",
    )


def _make_tool(name: str, args: str = "{}") -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, arguments=args)


# ---------------------------------------------------------------------------
# String similarity
# ---------------------------------------------------------------------------


class TestStringSimilarity:
    def test_identical(self):
        assert _string_similarity("hello", "hello") == 1.0

    def test_empty_both(self):
        assert _string_similarity("", "") == 1.0

    def test_empty_one(self):
        assert _string_similarity("hello", "") == 0.0

    def test_similar(self):
        score = _string_similarity("hello world", "hello earth")
        assert 0.4 < score < 0.9

    def test_case_insensitive(self):
        assert _string_similarity("Hello", "hello") == 1.0


class TestArgsSimilarity:
    def test_identical_json(self):
        score = _args_similarity('{"a": 1}', '{"a": 1}')
        assert score == 1.0

    def test_empty_both(self):
        score = _args_similarity("{}", "{}")
        assert score == 1.0

    def test_different_keys(self):
        score = _args_similarity('{"a": 1}', '{"b": 1}')
        assert score < 0.5

    def test_same_keys_different_values(self):
        score = _args_similarity('{"q": "shirt"}', '{"q": "pants"}')
        assert 0.0 < score < 1.0

    def test_numeric_tolerance(self):
        score = _args_similarity('{"price": 29.99}', '{"price": 30.00}')
        assert score > 0.9

    def test_non_json_fallback(self):
        score = _args_similarity("some text", "some text")
        assert score == 1.0


# ---------------------------------------------------------------------------
# WorkflowDiffer
# ---------------------------------------------------------------------------


class TestWorkflowDiffer:
    def test_identical_workflows(self):
        original = [
            _make_turn(0, "hi", "hello"),
            _make_turn(1, "buy", "ordered"),
        ]
        replayed = [
            _make_turn(0, "hi", "hello"),
            _make_turn(1, "buy", "ordered"),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.passed
        assert result.overall_score == 1.0

    def test_completely_different(self):
        original = [_make_turn(0, "hi", "hello")]
        replayed = [_make_turn(0, "hi", "goodbye")]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        # response_similarity will be low, but tool calls match (both empty)
        assert isinstance(result.overall_score, float)

    def test_extra_turn_in_replay(self):
        original = [_make_turn(0, "hi", "hello")]
        replayed = [
            _make_turn(0, "hi", "hello"),
            _make_turn(1, "extra", "response"),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        # Extra turn scores 0, pulling overall down
        assert result.overall_score < 1.0

    def test_missing_turn_in_replay(self):
        original = [
            _make_turn(0, "hi", "hello"),
            _make_turn(1, "buy", "ordered"),
        ]
        replayed = [_make_turn(0, "hi", "hello")]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.overall_score < 1.0

    def test_empty_workflows(self):
        differ = WorkflowDiffer()
        result = differ.diff_turns([], [])
        assert result.overall_score == 1.0
        assert result.passed

    def test_tool_sequence_mismatch(self):
        original = [
            _make_turn(0, "hi", "ok", [_make_tool("search"), _make_tool("buy")]),
        ]
        replayed = [
            _make_turn(0, "hi", "ok", [_make_tool("buy"), _make_tool("search")]),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.turn_diffs[0].tool_sequence_match < 1.0

    def test_tool_sequence_exact_match(self):
        original = [
            _make_turn(0, "hi", "ok", [_make_tool("search"), _make_tool("buy")]),
        ]
        replayed = [
            _make_turn(0, "hi", "ok", [_make_tool("search"), _make_tool("buy")]),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.turn_diffs[0].tool_sequence_match == 1.0

    def test_tool_args_comparison(self):
        original = [
            _make_turn(
                0, "hi", "ok",
                [_make_tool("search", '{"query": "shirt"}')],
            ),
        ]
        replayed = [
            _make_turn(
                0, "hi", "ok",
                [_make_tool("search", '{"query": "shirt"}')],
            ),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.turn_diffs[0].tool_args_score == 1.0

    def test_tool_args_different_values(self):
        original = [
            _make_turn(
                0, "hi", "ok",
                [_make_tool("search", '{"query": "shirt"}')],
            ),
        ]
        replayed = [
            _make_turn(
                0, "hi", "ok",
                [_make_tool("search", '{"query": "pants"}')],
            ),
        ]
        differ = WorkflowDiffer()
        result = differ.diff_turns(original, replayed)
        assert result.turn_diffs[0].tool_args_score < 1.0

    def test_custom_threshold(self):
        original = [_make_turn(0, "hi", "hello")]
        replayed = [_make_turn(0, "hi", "something completely different")]
        differ = WorkflowDiffer(threshold=0.3)
        result = differ.diff_turns(original, replayed)
        # Low threshold, likely still passes since tool seq = 1.0
        assert isinstance(result.passed, bool)


# ---------------------------------------------------------------------------
# ReplayEngine
# ---------------------------------------------------------------------------


class TestReplayEngine:
    def _openai_response(
        self, content: str, tool_calls: list[dict] | None = None,
    ) -> httpx.Response:
        msg: dict = {"content": content, "role": "assistant"}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        return _make_response(
            {
                "choices": [{"message": msg, "finish_reason": "stop"}],
                "model": "gpt-4o-mini",
                "usage": {"prompt_tokens": 10, "completion_tokens": 20},
            }
        )

    def test_replay_single_turn(self):
        workflow = Workflow(
            name="test-flow",
            agent_url="https://api.example.com/v1/chat/completions",
            agent_format="openai",
            turns=[_make_turn(0, "hi", "original response")],
            total_duration_ms=100.0,
        )

        mock_post = MagicMock(
            return_value=self._openai_response("replayed response"),
        )

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine()
            replayed = engine.replay(workflow)

        assert replayed.name == "test-flow-replay"
        assert replayed.turn_count == 1
        assert replayed.turns[0].agent_response == "replayed response"
        assert replayed.turns[0].user_message == "hi"
        assert replayed.metadata["replay_of"] == "test-flow"

    def test_replay_multi_turn(self):
        workflow = Workflow(
            name="multi",
            agent_url="https://api.example.com/v1/chat/completions",
            agent_format="openai",
            turns=[
                _make_turn(0, "msg1", "resp1"),
                _make_turn(1, "msg2", "resp2"),
            ],
            total_duration_ms=200.0,
        )

        call_count = 0

        def _mock_post(self_client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return self._openai_response(f"replay-{call_count}")

        with patch.object(httpx.Client, "post", _mock_post):
            engine = ReplayEngine()
            replayed = engine.replay(workflow)

        assert replayed.turn_count == 2
        assert replayed.turns[0].agent_response == "replay-1"
        assert replayed.turns[1].agent_response == "replay-2"

    def test_replay_with_tool_calls(self):
        workflow = Workflow(
            name="tools",
            agent_url="https://api.example.com/v1/chat/completions",
            agent_format="openai",
            turns=[
                _make_turn(
                    0, "search", "ok",
                    [_make_tool("product_search", '{"q": "shirt"}')],
                ),
            ],
            total_duration_ms=100.0,
        )

        mock_post = MagicMock(
            return_value=self._openai_response(
                "found it",
                [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "product_search",
                            "arguments": '{"q": "shirt"}',
                        },
                    }
                ],
            ),
        )

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine()
            replayed = engine.replay(workflow)

        assert replayed.turns[0].tool_calls[0].name == "product_search"

    def test_replay_raw_format(self):
        workflow = Workflow(
            name="raw-test",
            agent_url="https://agent.example.com/api/chat",
            agent_format="raw",
            turns=[_make_turn(0, "hello", "original")],
            total_duration_ms=100.0,
        )

        mock_post = MagicMock(
            return_value=_make_response({"response": "replayed"}),
        )

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine(agent_format="raw")
            replayed = engine.replay(workflow)

        assert replayed.turns[0].agent_response == "replayed"

    def test_replay_uses_original_url_when_none(self):
        workflow = Workflow(
            name="url-test",
            agent_url="https://original.example.com/v1/chat/completions",
            agent_format="openai",
            turns=[_make_turn(0, "hi", "ok")],
            total_duration_ms=100.0,
        )

        mock_post = MagicMock(
            return_value=self._openai_response("ok"),
        )

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine()  # No URL → uses workflow's URL
            replayed = engine.replay(workflow)

        assert replayed.agent_url == "https://original.example.com/v1/chat/completions"

    def test_replay_error_captured(self):
        workflow = Workflow(
            name="error-test",
            agent_url="https://unreachable.example.com",
            agent_format="openai",
            turns=[_make_turn(0, "hi", "original")],
            total_duration_ms=100.0,
        )

        mock_post = MagicMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine()
            replayed = engine.replay(workflow)

        assert replayed.turns[0].error is not None
        assert "Connection refused" in replayed.turns[0].agent_response

    def test_replay_stop_on_error(self):
        workflow = Workflow(
            name="stop-test",
            agent_url="https://unreachable.example.com",
            agent_format="openai",
            turns=[
                _make_turn(0, "msg1", "r1"),
                _make_turn(1, "msg2", "r2"),
            ],
            total_duration_ms=200.0,
        )

        mock_post = MagicMock(side_effect=httpx.ConnectError("fail"))

        with patch.object(httpx.Client, "post", mock_post):
            engine = ReplayEngine(stop_on_error=True)
            replayed = engine.replay(workflow)

        # Should stop after first error
        assert replayed.turn_count == 1


# ---------------------------------------------------------------------------
# ReplayReport
# ---------------------------------------------------------------------------


class TestReplayReport:
    def test_from_diff(self):
        differ = WorkflowDiffer()
        original = [_make_turn(0, "hi", "hello")]
        replayed = [_make_turn(0, "hi", "hello")]
        diff_result = differ.diff_turns(original, replayed)

        report = ReplayReport.from_diff(
            workflow_name="test-replay",
            replay_of="test-flow",
            diff_result=diff_result,
            original_responses=["hello"],
            replayed_responses=["hello"],
            original_tool_names=[[]],
            replayed_tool_names=[[]],
            user_messages=["hi"],
        )

        assert report.workflow_name == "test-replay"
        assert report.replay_of == "test-flow"
        assert report.turn_count == 1
        assert report.passed
        assert report.pass_count == 1

    def test_from_diff_with_regression(self):
        differ = WorkflowDiffer()
        original = [_make_turn(0, "hi", "hello")]
        replayed = [_make_turn(0, "hi", "something totally different")]
        diff_result = differ.diff_turns(original, replayed)

        report = ReplayReport.from_diff(
            workflow_name="regression-test",
            replay_of="test-flow",
            diff_result=diff_result,
            original_responses=["hello"],
            replayed_responses=["something totally different"],
            original_tool_names=[[]],
            replayed_tool_names=[[]],
            user_messages=["hi"],
        )

        # Response similarity will be low
        assert report.turn_results[0].response_similarity < 0.5

    def test_serialization_roundtrip(self):
        report = ReplayReport(
            workflow_name="test",
            replay_of="original",
            turn_results=[
                TurnResult(
                    turn_index=0,
                    user_message="hi",
                    original_response="hello",
                    replayed_response="hello",
                    original_tools=[],
                    replayed_tools=[],
                    tool_sequence_match=1.0,
                    tool_args_score=1.0,
                    response_similarity=1.0,
                    score=1.0,
                    passed=True,
                )
            ],
            overall_score=1.0,
            passed=True,
        )

        json_str = report.to_json()
        loaded = ReplayReport.from_json(json_str)
        assert loaded.workflow_name == "test"
        assert loaded.turn_count == 1
        assert loaded.passed

    def test_save_and_load(self, tmp_path: Path):
        report = ReplayReport(
            workflow_name="save-test",
            replay_of="original",
            overall_score=0.95,
            passed=True,
        )
        path = report.save(base_dir=tmp_path)
        assert path.exists()

        loaded = ReplayReport.load(path)
        assert loaded.workflow_name == "save-test"
        assert loaded.overall_score == 0.95

    def test_load_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="Report not found"):
            ReplayReport.load(tmp_path / "nonexistent.json")

    def test_fail_count(self):
        report = ReplayReport(
            workflow_name="test",
            replay_of="orig",
            turn_results=[
                TurnResult(
                    turn_index=0, user_message="a",
                    original_response="a", replayed_response="b",
                    original_tools=[], replayed_tools=[],
                    tool_sequence_match=1.0, tool_args_score=1.0,
                    response_similarity=0.2, score=0.5, passed=False,
                ),
                TurnResult(
                    turn_index=1, user_message="b",
                    original_response="b", replayed_response="b",
                    original_tools=[], replayed_tools=[],
                    tool_sequence_match=1.0, tool_args_score=1.0,
                    response_similarity=1.0, score=1.0, passed=True,
                ),
            ],
            overall_score=0.75,
            passed=False,
        )
        assert report.fail_count == 1
        assert report.pass_count == 1


# ---------------------------------------------------------------------------
# End-to-end: record → replay → diff → report
# ---------------------------------------------------------------------------


class TestEndToEndReplay:
    def test_record_replay_diff(self):
        """Full pipeline: create workflow, mock replay, diff, report."""
        # 1. Create "original" workflow
        original = Workflow(
            name="e2e-flow",
            agent_url="https://api.example.com/v1/chat/completions",
            agent_format="openai",
            turns=[
                _make_turn(
                    0, "buy shirt", "found shirt",
                    [_make_tool("search", '{"q": "shirt"}')],
                ),
                _make_turn(1, "checkout", "order placed"),
            ],
            total_duration_ms=200.0,
        )

        # 2. Mock replay
        def _mock_post(self_client, url, **kwargs):
            data = kwargs.get("json", {})
            msgs = data.get("messages", [])
            last_msg = msgs[-1]["content"] if msgs else ""

            if last_msg == "buy shirt":
                return _make_response(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "found shirt",
                                    "tool_calls": [
                                        {
                                            "id": "c1",
                                            "function": {
                                                "name": "search",
                                                "arguments": '{"q": "shirt"}',
                                            },
                                        }
                                    ],
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "model": "gpt-4o",
                    }
                )
            elif last_msg == "checkout":
                return _make_response(
                    {
                        "choices": [
                            {
                                "message": {
                                    "content": "order placed",
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "model": "gpt-4o",
                    }
                )
            return _make_response(
                {"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]}
            )

        with patch.object(httpx.Client, "post", _mock_post):
            engine = ReplayEngine()
            replayed = engine.replay(original)

        # 3. Diff
        differ = WorkflowDiffer()
        diff_result = differ.diff_turns(original.turns, replayed.turns)

        # 4. Report
        report = ReplayReport.from_diff(
            workflow_name=replayed.name,
            replay_of=original.name,
            diff_result=diff_result,
            original_responses=[t.agent_response for t in original.turns],
            replayed_responses=[t.agent_response for t in replayed.turns],
            original_tool_names=[
                [tc.name for tc in t.tool_calls] for t in original.turns
            ],
            replayed_tool_names=[
                [tc.name for tc in t.tool_calls] for t in replayed.turns
            ],
            user_messages=original.user_messages,
        )

        assert report.passed
        assert report.overall_score == 1.0
        assert report.turn_count == 2
        assert report.pass_count == 2
