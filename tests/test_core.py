"""Tests for AgentBench core — test engine, assertions, runner."""

import pytest
from agentbench.core.test import AgentTest, AgentTrajectory, AgentStep, ToolFailureInjection
from agentbench.core.assertions import expect, Expectation
from agentbench.core.runner import TestRunner
from agentbench.adapters.raw_api import RawAPIAdapter


# ─── Helpers ───

def make_trajectory(
    steps: list[dict] | None = None,
    completed: bool = True,
    final_response: str = "Done",
    error: str | None = None,
) -> AgentTrajectory:
    """Build a test trajectory with given steps."""
    traj = AgentTrajectory(
        completed=completed,
        final_response=final_response,
        error=error,
    )
    for step_data in (steps or []):
        traj.steps.append(AgentStep(**step_data))
    return traj


# ─── Assertion Tests ───

class TestExpectations:
    """Test the expect() assertion API."""

    def test_to_complete(self):
        traj = make_trajectory(completed=True)
        result = expect(traj).to_complete()
        assert result.all_passed

    def test_to_complete_fails_on_error(self):
        traj = make_trajectory(completed=False, error="Timeout")
        result = expect(traj).to_complete()
        assert not result.all_passed

    def test_to_complete_within(self):
        steps = [{"step_number": i, "action": "llm_response", "response": f"step {i}"} for i in range(5)]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert result.all_passed

    def test_to_complete_within_fails(self):
        steps = [{"step_number": i, "action": "llm_response", "response": f"step {i}"} for i in range(15)]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert not result.all_passed

    def test_to_use_tool(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "tool_output": "results"},
            {"step_number": 1, "action": "llm_response", "response": "Found it"},
        ]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_use_tool("search")
        assert result.all_passed

    def test_to_use_tool_with_times(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "tool_output": "r1"},
            {"step_number": 1, "action": "tool_call", "tool_name": "search", "tool_output": "r2"},
            {"step_number": 2, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps)
        assert expect(traj).to_use_tool("search", times=2).all_passed
        assert not expect(traj).to_use_tool("search", times=1).all_passed

    def test_to_not_use_tool(self):
        steps = [{"step_number": 0, "action": "llm_response", "response": "No tools needed"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_not_use_tool("search")
        assert result.all_passed

    def test_to_not_expose(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "payment",
             "tool_output": "Card ****1234 processed"},
            {"step_number": 1, "action": "llm_response", "response": "Payment successful"},
        ]
        traj = make_trajectory(steps=steps)
        # Should pass — no full card number exposed
        result = expect(traj).to_not_expose("4111111111111111")
        assert result.all_passed

    def test_to_not_expose_fails(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "logging",
             "tool_output": "User card: 4111111111111111"},
        ]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_not_expose("4111111111111111")
        assert not result.all_passed

    def test_to_respond_with(self):
        traj = make_trajectory(final_response="Your order is confirmed!")
        result = expect(traj).to_respond_with("confirmed")
        assert result.all_passed

    def test_to_respond_with_case_insensitive(self):
        traj = make_trajectory(final_response="ORDER CONFIRMED")
        result = expect(traj).to_respond_with("order confirmed")
        assert result.all_passed

    def test_to_retry(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "error": "timeout"},
            {"step_number": 1, "action": "retry", "response": "retrying"},
            {"step_number": 2, "action": "tool_call", "tool_name": "search", "tool_output": "results"},
            {"step_number": 3, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_retry(max_attempts=3)
        assert result.all_passed

    def test_to_follow_workflow(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search"},
            {"step_number": 1, "action": "tool_call", "tool_name": "calculate"},
            {"step_number": 2, "action": "tool_call", "tool_name": "format"},
            {"step_number": 3, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_follow_workflow(["search", "calculate", "format"])
        assert result.all_passed

    def test_to_follow_workflow_fails(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search"},
            {"step_number": 1, "action": "tool_call", "tool_name": "format"},  # skipped calculate
            {"step_number": 2, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_follow_workflow(["search", "calculate", "format"])
        assert not result.all_passed

    def test_to_have_no_errors(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "tool_output": "ok"},
            {"step_number": 1, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps)
        assert expect(traj).to_have_no_errors().all_passed

    def test_to_have_no_errors_fails(self):
        steps = [
            {"step_number": 0, "action": "error", "error": "Connection refused"},
        ]
        traj = make_trajectory(steps=steps)
        assert not expect(traj).to_have_no_errors().all_passed

    def test_chained_expectations(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "tool_output": "results"},
            {"step_number": 1, "action": "llm_response", "response": "Found it"},
        ]
        traj = make_trajectory(steps=steps, completed=True, final_response="Found it")

        e1 = expect(traj).to_complete_within(steps=5)
        e2 = expect(traj).to_use_tool("search")
        e3 = expect(traj).to_respond_with("Found")
        e4 = expect(traj).to_not_expose("password")

        assert e1.all_passed
        assert e2.all_passed
        assert e3.all_passed
        assert e4.all_passed


# ─── Trajectory Tests ───

class TestTrajectory:
    def test_step_count(self):
        traj = make_trajectory(steps=[
            {"step_number": i, "action": "llm_response", "response": f"s{i}"}
            for i in range(5)
        ])
        assert traj.step_count == 5

    def test_tool_calls(self):
        traj = make_trajectory(steps=[
            {"step_number": 0, "action": "tool_call", "tool_name": "search"},
            {"step_number": 1, "action": "llm_response", "response": "ok"},
            {"step_number": 2, "action": "tool_call", "tool_name": "calculate"},
        ])
        assert len(traj.tool_calls) == 2
        assert len(traj.tool_calls_by_name("search")) == 1

    def test_to_dict(self):
        traj = make_trajectory(completed=True, final_response="ok")
        d = traj.to_dict()
        assert d["completed"] is True
        assert d["final_response"] == "ok"
        assert "steps" in d


# ─── RawAPI Adapter Tests ───

class TestRawAPIAdapter:
    def test_function_adapter(self):
        def my_agent(prompt: str, context=None):
            return {
                "response": f"Response to: {prompt}",
                "steps": [
                    {"action": "llm_response", "response": f"Response to: {prompt}"},
                ],
            }

        adapter = RawAPIAdapter(func=my_agent)
        traj = AgentTrajectory()
        result = adapter.run("Hello", traj)

        assert result.completed
        assert "Hello" in result.final_response
        assert result.step_count >= 1

    def test_function_adapter_with_tools(self):
        def agent_with_tools(prompt: str, context=None):
            return {
                "response": "Here's the weather",
                "steps": [
                    {"action": "tool_call", "tool_name": "weather_api",
                     "tool_input": {"city": "NYC"}, "tool_output": "72°F sunny"},
                    {"action": "llm_response", "response": "It's 72°F and sunny in NYC"},
                ],
            }

        adapter = RawAPIAdapter(func=agent_with_tools, tools=["weather_api"])
        assert "weather_api" in adapter.get_available_tools()

        traj = AgentTrajectory()
        result = adapter.run("What's the weather in NYC?", traj)

        assert result.completed
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0].tool_name == "weather_api"

    def test_function_adapter_error(self):
        def broken_agent(prompt: str, context=None):
            raise RuntimeError("Agent crashed")

        adapter = RawAPIAdapter(func=broken_agent)
        traj = AgentTrajectory()
        result = adapter.run("Hello", traj)

        assert not result.completed
        assert result.error is not None

    def test_no_endpoint_or_func_raises(self):
        with pytest.raises(ValueError, match="Provide either"):
            RawAPIAdapter()


# ─── AgentTest Integration Tests ───

class TestAgentTestIntegration:
    def test_run_with_adapter(self):
        def echo_agent(prompt: str, context=None):
            return {
                "response": f"Echo: {prompt}",
                "steps": [{"action": "llm_response", "response": f"Echo: {prompt}"}],
            }

        class EchoTest(AgentTest):
            agent = "echo"
            adapter = RawAPIAdapter(func=echo_agent)

        test = EchoTest()
        result = test.run("Hello")

        assert result.completed
        assert "Hello" in result.final_response

    def test_run_without_adapter_raises(self):
        class NoAdapterTest(AgentTest):
            agent = "none"

        test = NoAdapterTest()
        with pytest.raises(RuntimeError, match="No adapter"):
            test.run("Hello")
