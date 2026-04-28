"""Tests for the recorder module — Workflow model and SessionRecorder."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from agentbench.recorder.recorder import SessionRecorder
from agentbench.recorder.workflow import ToolCall, Turn, Workflow


def _make_response(json_data: dict, status_code: int = 200) -> httpx.Response:
    """Build a fake httpx.Response with the given JSON body."""
    return httpx.Response(
        status_code=status_code,
        json=json_data,
        request=MagicMock(),
    )


# ---------------------------------------------------------------------------
# Workflow data model
# ---------------------------------------------------------------------------


class TestToolCall:
    def test_parsed_arguments_valid_json(self):
        tc = ToolCall(id="c1", name="search", arguments='{"query": "shirt"}')
        assert tc.parsed_arguments() == {"query": "shirt"}

    def test_parsed_arguments_invalid_json(self):
        tc = ToolCall(id="c2", name="search", arguments="not-json")
        assert tc.parsed_arguments() == {}

    def test_parsed_arguments_none(self):
        tc = ToolCall(id="c3", name="search", arguments="{}")
        assert tc.parsed_arguments() == {}


class TestTurn:
    def test_auto_timestamp(self):
        turn = Turn(index=0, user_message="hi", agent_response="hello")
        assert turn.timestamp  # not empty
        assert "T" in turn.timestamp  # ISO format

    def test_explicit_timestamp(self):
        turn = Turn(
            index=0,
            user_message="hi",
            agent_response="hello",
            timestamp="2025-01-01T00:00:00",
        )
        assert turn.timestamp == "2025-01-01T00:00:00"

    def test_default_fields(self):
        turn = Turn(index=0, user_message="hi", agent_response="hello")
        assert turn.tool_calls == []
        assert turn.latency_ms == 0.0
        assert turn.error is None
        assert turn.metadata == {}


class TestWorkflow:
    def _make_workflow(self, **kwargs) -> Workflow:
        defaults = dict(
            name="test-flow",
            agent_url="http://localhost:8000/chat",
            agent_format="openai",
        )
        defaults.update(kwargs)
        return Workflow(**defaults)

    def _make_turn(self, index: int, msg: str, resp: str, tools=None) -> Turn:
        return Turn(
            index=index,
            user_message=msg,
            agent_response=resp,
            tool_calls=tools or [],
            latency_ms=100.0,
            timestamp="2025-01-01T00:00:00",
        )

    def test_auto_created_at(self):
        wf = self._make_workflow()
        assert wf.created_at
        assert "T" in wf.created_at

    def test_turn_count(self):
        wf = self._make_workflow()
        assert wf.turn_count == 0
        wf.add_turn(self._make_turn(0, "hi", "hello"))
        assert wf.turn_count == 1
        wf.add_turn(self._make_turn(1, "buy", "ordered"))
        assert wf.turn_count == 2

    def test_total_tool_calls(self):
        wf = self._make_workflow()
        tools = [ToolCall(id="c1", name="search", arguments="{}")]
        wf.add_turn(self._make_turn(0, "hi", "ok", tools))
        assert wf.total_tool_calls == 1
        wf.add_turn(self._make_turn(1, "buy", "ok", tools))
        assert wf.total_tool_calls == 2

    def test_tool_call_sequence(self):
        wf = self._make_workflow()
        wf.add_turn(
            self._make_turn(
                0,
                "hi",
                "ok",
                [
                    ToolCall(id="c1", name="search", arguments="{}"),
                    ToolCall(id="c2", name="add_to_cart", arguments="{}"),
                ],
            )
        )
        wf.add_turn(
            self._make_turn(
                1,
                "buy",
                "ok",
                [ToolCall(id="c3", name="payment", arguments="{}")],
            )
        )
        assert wf.tool_call_sequence == ["search", "add_to_cart", "payment"]

    def test_user_messages(self):
        wf = self._make_workflow()
        wf.add_turn(self._make_turn(0, "hi", "hello"))
        wf.add_turn(self._make_turn(1, "buy shirt", "ordered"))
        assert wf.user_messages == ["hi", "buy shirt"]

    def test_add_turn_updates_duration(self):
        wf = self._make_workflow()
        wf.add_turn(self._make_turn(0, "a", "b"))
        assert wf.total_duration_ms == 100.0
        wf.add_turn(self._make_turn(1, "c", "d"))
        assert wf.total_duration_ms == 200.0

    def test_serialization_roundtrip(self):
        wf = self._make_workflow()
        wf.add_turn(
            self._make_turn(
                0,
                "hi",
                "hello",
                [ToolCall(id="c1", name="search", arguments='{"q": "test"}')],
            )
        )
        json_str = wf.to_json()
        loaded = Workflow.from_json(json_str)
        assert loaded.name == wf.name
        assert loaded.agent_url == wf.agent_url
        assert loaded.turn_count == 1
        assert loaded.turns[0].user_message == "hi"
        assert loaded.turns[0].tool_calls[0].name == "search"
        assert loaded.tool_call_sequence == ["search"]

    def test_dict_roundtrip(self):
        wf = self._make_workflow()
        wf.add_turn(self._make_turn(0, "hi", "hello"))
        d = wf.to_dict()
        loaded = Workflow.from_dict(d)
        assert loaded.name == wf.name
        assert loaded.turn_count == 1

    def test_save_and_load(self, tmp_path: Path):
        wf = self._make_workflow()
        wf.add_turn(self._make_turn(0, "hi", "hello"))
        saved_path = wf.save(base_dir=tmp_path)
        assert saved_path.exists()

        loaded = Workflow.load("test-flow", base_dir=tmp_path)
        assert loaded.name == "test-flow"
        assert loaded.turn_count == 1
        assert loaded.turns[0].user_message == "hi"

    def test_load_missing_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError, match="not found"):
            Workflow.load("nonexistent", base_dir=tmp_path)

    def test_list_workflows(self, tmp_path: Path):
        wf1 = self._make_workflow(name="flow-a")
        wf1.save(base_dir=tmp_path)
        wf2 = self._make_workflow(name="flow-b")
        wf2.save(base_dir=tmp_path)

        listed = Workflow.list_workflows(base_dir=tmp_path)
        assert len(listed) == 2
        names = [n for n, _ in listed]
        assert "flow-a" in names
        assert "flow-b" in names

    def test_list_workflows_empty(self, tmp_path: Path):
        listed = Workflow.list_workflows(base_dir=tmp_path)
        assert listed == []

    def test_delete_workflow(self, tmp_path: Path):
        wf = self._make_workflow()
        wf.save(base_dir=tmp_path)
        assert Workflow.delete("test-flow", base_dir=tmp_path) is True
        assert not (
            tmp_path / ".agentbench" / "workflows" / "test-flow.json"
        ).exists()

    def test_delete_missing(self, tmp_path: Path):
        assert Workflow.delete("nonexistent", base_dir=tmp_path) is False


# ---------------------------------------------------------------------------
# SessionRecorder — using httpx mock transport
# ---------------------------------------------------------------------------


class TestSessionRecorder:
    def _openai_response(
        self, content: str, tool_calls: list[dict] | None = None
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

    def test_send_openai_format(self):
        mock_post = MagicMock(
            return_value=self._openai_response(
                "I'll search for that.",
                [
                    {
                        "id": "call_1",
                        "function": {
                            "name": "product_search",
                            "arguments": '{"query": "blue shirt"}',
                        },
                    }
                ],
            )
        )

        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://api.example.com/v1/chat/completions",
                workflow_name="test",
                agent_format="openai",
            )
            turn = recorder.send("Buy me a blue shirt")
            recorder.finish()

        assert turn.user_message == "Buy me a blue shirt"
        assert turn.agent_response == "I'll search for that."
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "product_search"
        assert turn.error is None
        assert turn.metadata["model"] == "gpt-4o-mini"

    def test_send_raw_format(self):
        mock_post = MagicMock(
            return_value=_make_response(
                {
                    "response": "Order confirmed!",
                    "tool_calls": [
                        {
                            "name": "payment",
                            "arguments": {"amount": 29.99},
                            "result": "success",
                        }
                    ],
                }
            )
        )

        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://agent.example.com/api/chat",
                workflow_name="test",
                agent_format="raw",
            )
            turn = recorder.send("Check out")
            recorder.finish()

        assert turn.agent_response == "Order confirmed!"
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0].name == "payment"
        assert turn.tool_calls[0].result == "success"

    def test_send_captures_error(self):
        mock_post = MagicMock(side_effect=httpx.ConnectError("Connection refused"))

        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://unreachable.example.com",
                workflow_name="test",
            )
            turn = recorder.send("hello")
            recorder.finish()

        assert turn.error is not None
        assert "Connection refused" in turn.agent_response

    def test_multi_turn_conversation(self):
        call_count = 0

        def _mock_post(self_client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return self._openai_response(f"Response {call_count}")

        with patch.object(httpx.Client, "post", _mock_post):
            recorder = SessionRecorder(
                agent_url="https://api.example.com/v1/chat/completions",
                workflow_name="multi",
                agent_format="openai",
            )
            recorder.send("message 1")
            recorder.send("message 2")
            recorder.send("message 3")
            workflow = recorder.finish()

        assert workflow.turn_count == 3
        assert workflow.user_messages == [
            "message 1",
            "message 2",
            "message 3",
        ]
        # Duration should be > 0 (real perf_counter)
        assert workflow.total_duration_ms > 0

    def test_cancel_does_not_error(self):
        mock_post = MagicMock(
            return_value=self._openai_response("hi"),
        )
        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://example.com",
                workflow_name="test",
            )
            recorder.send("hello")
            recorder.cancel()  # should not raise

    def test_finish_returns_workflow(self):
        mock_post = MagicMock(return_value=self._openai_response("ok"))
        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://example.com",
                workflow_name="test",
            )
            recorder.send("hi")
            wf = recorder.finish()
        assert isinstance(wf, Workflow)
        assert wf.name == "test"


# ---------------------------------------------------------------------------
# Workflow persistence integration
# ---------------------------------------------------------------------------


class TestWorkflowPersistence:
    def test_save_and_reload_preserves_tool_calls(self, tmp_path: Path):
        mock_post = MagicMock(
            return_value=_make_response(
                {
                    "choices": [
                        {
                            "message": {
                                "content": "Done!",
                                "tool_calls": [
                                    {
                                        "id": "c1",
                                        "function": {
                                            "name": "search",
                                            "arguments": '{"q": "test"}',
                                        },
                                    }
                                ],
                            }
                        }
                    ],
                    "model": "gpt-4o",
                }
            )
        )

        with patch.object(httpx.Client, "post", mock_post):
            recorder = SessionRecorder(
                agent_url="https://api.example.com/v1/chat/completions",
                workflow_name="integration-test",
            )
            recorder.send("search for test")
            wf = recorder.finish()
            wf.save(base_dir=tmp_path)

        # Reload and verify
        loaded = Workflow.load("integration-test", base_dir=tmp_path)
        assert loaded.turn_count == 1
        assert loaded.turns[0].agent_response == "Done!"
        assert loaded.turns[0].tool_calls[0].name == "search"
        assert loaded.turns[0].tool_calls[0].parsed_arguments() == {"q": "test"}
        assert loaded.tool_call_sequence == ["search"]
