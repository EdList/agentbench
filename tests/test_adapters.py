"""Tests for AgentBench adapters — RawAPIAdapter and LangChainAdapter."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from agentbench.adapters.base import AgentAdapter
from agentbench.adapters.langchain import LangChainAdapter, _TrajectoryCallback
from agentbench.adapters.raw_api import RawAPIAdapter
from agentbench.core.test import (
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)

# ─── Helpers ───

def _empty_trajectory() -> AgentTrajectory:
    return AgentTrajectory()


# ─── RawAPIAdapter: Constructor ───

class TestRawAPIAdapterInit:
    def test_function_mode(self):
        adapter = RawAPIAdapter(func=lambda p, ctx: {"response": "ok"})
        assert adapter._func is not None
        assert adapter._endpoint is None

    def test_http_mode(self):
        adapter = RawAPIAdapter(endpoint="http://localhost:8000/chat")
        assert adapter._endpoint == "http://localhost:8000/chat"
        assert adapter._func is None

    def test_custom_headers(self):
        headers = {"Authorization": "Bearer tok123"}
        adapter = RawAPIAdapter(endpoint="http://x/y", headers=headers)
        assert adapter._headers == headers

    def test_default_headers_empty(self):
        adapter = RawAPIAdapter(endpoint="http://x/y")
        assert adapter._headers == {}

    def test_custom_timeout(self):
        adapter = RawAPIAdapter(endpoint="http://x/y", timeout=60.0)
        assert adapter._timeout == 60.0

    def test_default_timeout(self):
        adapter = RawAPIAdapter(endpoint="http://x/y")
        assert adapter._timeout == 30.0

    def test_no_endpoint_or_func_raises(self):
        with pytest.raises(ValueError, match="Provide either"):
            RawAPIAdapter()

    def test_tools_list(self):
        adapter = RawAPIAdapter(
            func=lambda p, ctx: {},
            tools=["search", "calculator"],
        )
        assert adapter.get_available_tools() == ["search", "calculator"]

    def test_default_tools_empty(self):
        adapter = RawAPIAdapter(func=lambda p, ctx: {})
        assert adapter.get_available_tools() == []


# ─── RawAPIAdapter: Function Mode ───

class TestRawAPIAdapterFunctionMode:
    def test_basic_response(self):
        def agent(prompt, context):
            return {"response": f"Echo: {prompt}", "steps": []}

        adapter = RawAPIAdapter(func=agent)
        traj = adapter.run("Hello", _empty_trajectory())
        assert traj.completed
        assert traj.final_response == "Echo: Hello"

    def test_response_with_steps(self):
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "search", "tool_output": "results"},
                    {"action": "llm_response", "response": "Found it"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        traj = adapter.run("Search", _empty_trajectory())
        assert traj.completed
        assert traj.step_count == 2
        assert traj.tool_calls[0].tool_name == "search"

    def test_empty_steps_with_response(self):
        """When steps list is empty but response exists, should record a step."""
        def agent(prompt, context):
            return {"response": "No steps needed", "steps": []}

        adapter = RawAPIAdapter(func=agent)
        traj = adapter.run("Hi", _empty_trajectory())
        assert traj.completed
        assert traj.step_count == 1  # auto-recorded step
        assert traj.steps[0].action == "llm_response"

    def test_response_without_steps_key(self):
        """When no steps key but response exists, should record a step."""
        def agent(prompt, context):
            return {"response": "Plain response"}

        adapter = RawAPIAdapter(func=agent)
        traj = adapter.run("Hi", _empty_trajectory())
        assert traj.completed
        assert traj.step_count == 1
        assert traj.steps[0].response == "Plain response"

    def test_non_dict_return(self):
        """Function returns a string instead of dict."""
        def agent(prompt, context):
            return "just a string"

        adapter = RawAPIAdapter(func=agent)
        traj = adapter.run("Hi", _empty_trajectory())
        assert traj.completed
        assert traj.final_response == "just a string"
        assert traj.steps[0].response == "just a string"

    def test_function_raises_error(self):
        def broken(prompt, context):
            raise RuntimeError("Agent crashed")

        adapter = RawAPIAdapter(func=broken)
        traj = adapter.run("Hello", _empty_trajectory())
        assert not traj.completed
        assert "Agent crashed" in traj.error
        assert traj.steps[-1].action == "error"

    def test_context_passed(self):
        received_context = {}

        def agent(prompt, context):
            received_context.update(context or {})
            return {"response": "ok"}

        adapter = RawAPIAdapter(func=agent)
        _traj = adapter.run("Hi", _empty_trajectory(), context={"key": "val"})
        assert received_context == {"key": "val"}


# ─── RawAPIAdapter: Failure Injection ───

class TestRawAPIAdapterFailureInjection:
    def test_failure_injected_for_matching_tool(self):
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "search", "tool_output": "results"},
                    {"action": "llm_response", "response": "ok"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        failures = [
            ToolFailureInjection(
                tool_name="search", fail_times=1,
                error_message="Injected fail"
            )
        ]
        traj = adapter.run("Search", _empty_trajectory(), failure_injections=failures)

        # The tool_call step should be replaced with an error
        error_steps = [s for s in traj.steps if s.action == "error"]
        assert len(error_steps) >= 1
        assert "Injected fail" in error_steps[0].error

    def test_failure_not_injected_for_non_matching_tool(self):
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "calc", "tool_output": "42"},
                    {"action": "llm_response", "response": "ok"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        failures = [ToolFailureInjection(tool_name="search", fail_times=1, error_message="fail")]
        traj = adapter.run("Calc", _empty_trajectory(), failure_injections=failures)
        assert traj.completed
        # No error steps — tool name didn't match
        error_steps = [s for s in traj.steps if s.action == "error"]
        assert len(error_steps) == 0

    def test_failure_injected_only_fail_times(self):
        """Failure should be injected only fail_times times."""
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "search", "tool_output": "r1"},
                    {"action": "tool_call", "tool_name": "search", "tool_output": "r2"},
                    {"action": "llm_response", "response": "ok"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        failures = [ToolFailureInjection(tool_name="search", fail_times=1, error_message="fail")]
        traj = adapter.run("Search", _empty_trajectory(), failure_injections=failures)
        error_steps = [s for s in traj.steps if s.action == "error"]
        # Only first call should fail
        assert len(error_steps) == 1


# ─── RawAPIAdapter: Latency Injection ───

class TestRawAPIAdapterLatencyInjection:
    def test_latency_injected(self):
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "search", "tool_output": "r"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        latencies = [ToolLatencyInjection(tool_name="search", delay_ms=200)]

        start = time.time()
        traj = adapter.run("Search", _empty_trajectory(), latency_injections=latencies)
        elapsed = (time.time() - start) * 1000

        assert traj.completed
        assert elapsed >= 150  # Allow some margin

    def test_latency_not_injected_for_non_matching(self):
        def agent(prompt, context):
            return {
                "response": "Done",
                "steps": [
                    {"action": "tool_call", "tool_name": "calc", "tool_output": "42"},
                ],
            }

        adapter = RawAPIAdapter(func=agent)
        latencies = [ToolLatencyInjection(tool_name="search", delay_ms=500)]

        start = time.time()
        traj = adapter.run("Calc", _empty_trajectory(), latency_injections=latencies)
        elapsed = (time.time() - start) * 1000

        assert traj.completed
        assert elapsed < 200  # Should be fast — no delay injected


# ─── RawAPIAdapter: HTTP Mode ───

class TestRawAPIAdapterHTTPMode:
    @patch("agentbench.adapters.raw_api.httpx.Client")
    def test_successful_http_call(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "response": "HTTP response",
            "completed": True,
            "steps": [
                {"action": "llm_response", "response": "HTTP response"},
            ],
            "tokens": 150,
            "cost": 0.002,
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        adapter = RawAPIAdapter(
            endpoint="http://localhost:8000/chat",
            headers={"Authorization": "Bearer tok"},
            timeout=60,
        )
        traj = adapter.run("Hello", _empty_trajectory())

        assert traj.completed
        assert traj.final_response == "HTTP response"
        assert traj.step_count == 1
        assert traj.total_tokens == 150
        assert traj.total_cost_usd == 0.002

        # Verify correct payload
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "http://localhost:8000/chat"
        payload = call_args[1]["json"]
        assert payload["prompt"] == "Hello"
        assert call_args[1]["headers"] == {"Authorization": "Bearer tok"}

    @patch("agentbench.adapters.raw_api.httpx.Client")
    def test_http_failure_injections_in_payload(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.json.return_value = {"response": "ok", "completed": True}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        adapter = RawAPIAdapter(endpoint="http://x/y")
        failures = [ToolFailureInjection(tool_name="db", fail_times=2, error_message="timeout")]
        latencies = [ToolLatencyInjection(tool_name="search", delay_ms=500)]

        _traj = adapter.run(
            "Test", _empty_trajectory(),
            failure_injections=failures,
            latency_injections=latencies,
        )

        payload = mock_client.post.call_args[1]["json"]
        assert len(payload["inject_failures"]) == 1
        assert payload["inject_failures"][0]["tool"] == "db"
        assert len(payload["inject_latency"]) == 1
        assert payload["inject_latency"][0]["tool"] == "search"

    @patch("agentbench.adapters.raw_api.httpx.Client")
    def test_http_error_returns_incomplete_trajectory(self, mock_client_cls):
        mock_client = MagicMock()
        mock_client.post.side_effect = ConnectionError("Connection refused")
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        adapter = RawAPIAdapter(endpoint="http://localhost:8000/chat")
        traj = adapter.run("Hello", _empty_trajectory())

        assert not traj.completed
        assert "Connection refused" in traj.error
        assert traj.steps[-1].action == "error"

    @patch("agentbench.adapters.raw_api.httpx.Client")
    def test_http_non_200_status(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("500 Server Error")

        mock_client = MagicMock()
        mock_client.post.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        adapter = RawAPIAdapter(endpoint="http://x/y")
        traj = adapter.run("Hello", _empty_trajectory())
        assert not traj.completed


# ─── RawAPIAdapter: _should_inject_failure (inherited from base) ───

class TestRawAPIAdapterShouldInjectFailure:
    def test_no_injections_returns_none(self):
        adapter = RawAPIAdapter(func=lambda p, c: {})
        assert adapter._should_inject_failure("search", None) is None

    def test_empty_list_returns_none(self):
        adapter = RawAPIAdapter(func=lambda p, c: {})
        assert adapter._should_inject_failure("search", []) is None

    def test_matching_tool_decrements_counter(self):
        adapter = RawAPIAdapter(func=lambda p, c: {})
        inj = ToolFailureInjection(tool_name="search", fail_times=2, error_message="fail")
        assert adapter._should_inject_failure("search", [inj]) == "fail"
        assert inj.fail_times == 1  # decremented
        assert adapter._should_inject_failure("search", [inj]) == "fail"
        assert inj.fail_times == 0
        assert adapter._should_inject_failure("search", [inj]) is None  # exhausted

    def test_non_matching_tool_returns_none(self):
        adapter = RawAPIAdapter(func=lambda p, c: {})
        inj = ToolFailureInjection(tool_name="search", fail_times=1, error_message="fail")
        assert adapter._should_inject_failure("calc", [inj]) is None


# ─── RawAPIAdapter: _safe_step_kwargs (inherited from base) ───

class TestSafeStepKwargs:
    def test_filters_invalid_keys(self):
        data = {
            "action": "tool_call",
            "tool_name": "search",
            "extra_key": "ignored",
            "step_number": 0,
        }
        result = AgentAdapter._safe_step_kwargs(data)
        assert "extra_key" not in result
        assert "step_number" not in result
        assert result["action"] == "tool_call"
        assert result["tool_name"] == "search"

    def test_all_valid_keys_preserved(self):
        data = {
            "action": "tool_call",
            "tool_name": "search",
            "tool_input": {"q": "test"},
            "tool_output": "results",
            "reasoning": "thinking",
            "response": "resp",
            "latency_ms": 42.0,
            "error": None,
        }
        result = AgentAdapter._safe_step_kwargs(data)
        assert len(result) == 8


# ─── LangChainAdapter: Init & Tools ───

class TestLangChainAdapterInit:
    def test_explicit_tools(self):
        executor = MagicMock()
        adapter = LangChainAdapter(executor, tools=["search", "calc"])
        assert adapter.get_available_tools() == ["search", "calc"]

    def test_tools_from_executor(self):
        tool_a = MagicMock()
        tool_a.name = "search"
        tool_b = MagicMock()
        tool_b.name = "calc"
        executor = MagicMock()
        executor.tools = [tool_a, tool_b]

        adapter = LangChainAdapter(executor)
        assert adapter.get_available_tools() == ["search", "calc"]

    def test_tools_fallback_on_error(self):
        executor = MagicMock()
        executor.tools = property(lambda self: (_ for _ in ()).throw(RuntimeError("no tools")))
        # When tools raises, get_available_tools catches Exception
        # and returns []
        type(executor).tools = property(
            lambda self: (
                _ for _ in ()
            ).throw(RuntimeError("no tools"))
        )
        adapter = LangChainAdapter(executor)
        assert adapter.get_available_tools() == []


# ─── LangChainAdapter: Run ───

class TestLangChainAdapterRun:
    def test_successful_run(self):
        executor = MagicMock()
        executor.invoke.return_value = {"output": "The answer is 42"}

        adapter = LangChainAdapter(executor)
        traj = adapter.run("What is the answer?", _empty_trajectory())

        assert traj.completed
        assert traj.final_response == "The answer is 42"
        assert traj.total_latency_ms > 0

        # Verify invoke called correctly
        call_args = executor.invoke.call_args
        assert call_args[0][0] == {"input": "What is the answer?"}
        config = call_args[1]["config"]
        assert len(config["callbacks"]) == 1
        assert isinstance(config["callbacks"][0], _TrajectoryCallback)

    def test_run_with_exception(self):
        executor = MagicMock()
        executor.invoke.side_effect = RuntimeError("LLM unavailable")

        adapter = LangChainAdapter(executor)
        traj = adapter.run("Hello", _empty_trajectory())

        assert not traj.completed
        assert "LLM unavailable" in traj.error
        assert traj.steps[-1].action == "error"

    def test_invoke_result_without_output_key(self):
        """When result has no 'output' key, str(result) is used."""
        executor = MagicMock()
        executor.invoke.return_value = {"text": "some text"}

        adapter = LangChainAdapter(executor)
        traj = adapter.run("Hi", _empty_trajectory())
        assert traj.completed
        assert traj.final_response == "{'text': 'some text'}"


# ─── LangChainAdapter: _TrajectoryCallback ───

class TestTrajectoryCallback:
    def test_on_llm_end_records_step(self):
        traj = AgentTrajectory()
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=[],
            latency_injections=[],
            max_steps=50,
        )

        # Simulate LLM start/end
        cb.on_llm_start({}, ["test prompt"])
        mock_response = MagicMock()
        mock_response.generations = [[MagicMock(text="Hello from LLM")]]
        cb.on_llm_end(mock_response)

        assert len(traj.steps) == 1
        assert traj.steps[0].action == "llm_response"
        assert traj.steps[0].response == "Hello from LLM"

    def test_on_tool_end_records_step(self):
        traj = AgentTrajectory()
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=[],
            latency_injections=[],
            max_steps=50,
        )

        cb.on_tool_start({"name": "search"}, "query string")
        cb.on_tool_end("search results")

        assert len(traj.steps) == 1
        assert traj.steps[0].action == "tool_call"
        assert traj.steps[0].tool_name == "search"
        assert traj.steps[0].tool_output == "search results"

    def test_on_tool_error_records_error(self):
        traj = AgentTrajectory()
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=[],
            latency_injections=[],
            max_steps=50,
        )

        cb.on_tool_start({"name": "db"}, "query")
        cb.on_tool_error(Exception("DB connection failed"), serialized={"name": "db"})

        assert len(traj.steps) == 1
        assert traj.steps[0].action == "error"
        assert "DB connection failed" in traj.steps[0].error

    def test_failure_injection_in_on_agent_action(self):
        traj = AgentTrajectory()
        failures = [
            ToolFailureInjection(
                tool_name="search", fail_times=1,
                error_message="Injected!"
            )
        ]
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=failures,
            latency_injections=[],
            max_steps=50,
        )

        action = MagicMock()
        action.tool = "search"
        action.tool_input = {"q": "test"}
        cb.on_agent_action(action)

        assert len(traj.steps) == 1
        assert traj.steps[0].action == "error"
        assert traj.steps[0].error == "Injected!"
        assert failures[0].fail_times == 0  # decremented

    def test_failure_injection_skips_on_tool_end(self):
        """When failure is injected, on_tool_end should be skipped.

        Note: on_tool_start resets the _injected_failure flag, so we
        call on_tool_end directly after on_agent_action without an
        intervening on_tool_start to verify the skip behavior.
        """
        traj = AgentTrajectory()
        failures = [ToolFailureInjection(tool_name="search", fail_times=1, error_message="fail")]
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=failures,
            latency_injections=[],
            max_steps=50,
        )

        # Trigger failure injection
        action = MagicMock()
        action.tool = "search"
        action.tool_input = {}
        cb.on_agent_action(action)

        # on_tool_end should be skipped due to _injected_failure flag
        cb.on_tool_end("results")

        # Only the error step from on_agent_action should exist
        assert len(traj.steps) == 1
        assert traj.steps[0].action == "error"

    def test_latency_injection_in_on_tool_start(self):
        traj = AgentTrajectory()
        latencies = [ToolLatencyInjection(tool_name="search", delay_ms=150)]
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=[],
            latency_injections=latencies,
            max_steps=50,
        )

        start = time.time()
        cb.on_tool_start({"name": "search"}, "query")
        elapsed_ms = (time.time() - start) * 1000

        assert elapsed_ms >= 100  # Allow margin

    def test_on_llm_end_with_bad_response(self):
        """When response.generations is missing, str(response) is used."""
        traj = AgentTrajectory()
        cb = _TrajectoryCallback(
            trajectory=traj,
            failure_injections=[],
            latency_injections=[],
            max_steps=50,
        )

        cb.on_llm_start({}, ["prompt"])
        cb.on_llm_end("raw string response")

        assert len(traj.steps) == 1
        assert traj.steps[0].response == "raw string response"


# ─── LangChainAdapter: Latency & Failure Injection via run() ───

class TestLangChainAdapterInjections:
    def test_latency_injection_via_run(self):
        """Verify latency injection is passed to callback."""
        executor = MagicMock()
        executor.invoke.return_value = {"output": "done"}

        latencies = [ToolLatencyInjection(tool_name="search", delay_ms=100)]
        adapter = LangChainAdapter(executor)
        traj = adapter.run(
            "test", _empty_trajectory(),
            latency_injections=latencies,
        )
        assert traj.completed

    def test_failure_injection_via_run(self):
        executor = MagicMock()
        executor.invoke.return_value = {"output": "done"}

        failures = [ToolFailureInjection(tool_name="search", fail_times=1)]
        adapter = LangChainAdapter(executor)
        traj = adapter.run(
            "test", _empty_trajectory(),
            failure_injections=failures,
        )
        assert traj.completed
