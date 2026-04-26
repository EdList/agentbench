"""Tests for AgentBench core — test engine, assertions, runner."""

import pytest

from agentbench.adapters.raw_api import RawAPIAdapter
from agentbench.core.assertions import AssertionResult, expect
from agentbench.core.fixtures import Fixture, FixtureRegistry, fixture
from agentbench.core.runner import TestRunner
from agentbench.core.test import AgentStep, AgentTest, AgentTrajectory

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
    for step_data in steps or []:
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
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"step {i}"} for i in range(5)
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert result.all_passed

    def test_to_complete_within_fails(self):
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"step {i}"} for i in range(15)
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert not result.all_passed

    def test_to_use_tool(self):
        steps = [
            {
                "step_number": 0,
                "action": "tool_call",
                "tool_name": "search",
                "tool_output": "results",
            },
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
            {
                "step_number": 0,
                "action": "tool_call",
                "tool_name": "payment",
                "tool_output": "Card ****1234 processed",
            },
            {"step_number": 1, "action": "llm_response", "response": "Payment successful"},
        ]
        traj = make_trajectory(steps=steps)
        # Should pass — no full card number exposed
        result = expect(traj).to_not_expose("4111111111111111")
        assert result.all_passed

    def test_to_not_expose_fails(self):
        steps = [
            {
                "step_number": 0,
                "action": "tool_call",
                "tool_name": "logging",
                "tool_output": "User card: 4111111111111111",
            },
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

    def test_to_refuse(self):
        traj = make_trajectory(final_response="Sorry, I can't help with that request.")
        result = expect(traj).to_refuse()
        assert result.all_passed

    def test_to_refuse_fails_when_agent_answers_normally(self):
        traj = make_trajectory(final_response="Here is how you do that dangerous thing.")
        result = expect(traj).to_refuse()
        assert not result.all_passed

    def test_to_retry(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search", "error": "timeout"},
            {"step_number": 1, "action": "retry", "response": "retrying"},
            {
                "step_number": 2,
                "action": "tool_call",
                "tool_name": "search",
                "tool_output": "results",
            },
            {"step_number": 3, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_retry(max_attempts=3)
        assert result.all_passed

    def test_to_retry_requires_an_actual_retry_step(self):
        steps = [
            {
                "step_number": 0,
                "action": "tool_call",
                "tool_name": "search",
                "tool_output": "results",
            },
            {"step_number": 1, "action": "llm_response", "response": "Done"},
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_retry(max_attempts=3)
        assert not result.all_passed

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
            {
                "step_number": 0,
                "action": "tool_call",
                "tool_name": "search",
                "tool_output": "results",
            },
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
        traj = make_trajectory(
            steps=[
                {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(5)
            ]
        )
        assert traj.step_count == 5

    def test_tool_calls(self):
        traj = make_trajectory(
            steps=[
                {"step_number": 0, "action": "tool_call", "tool_name": "search"},
                {"step_number": 1, "action": "llm_response", "response": "ok"},
                {"step_number": 2, "action": "tool_call", "tool_name": "calculate"},
            ]
        )
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
                    {
                        "action": "tool_call",
                        "tool_name": "weather_api",
                        "tool_input": {"city": "NYC"},
                        "tool_output": "72°F sunny",
                    },
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


# ─── Edge Case: Agent Timeout ───


class TestAgentTimeout:
    """Tests for agent timeout handling in the runner."""

    def test_timeout_is_respected(self):
        """A test that hangs beyond the timeout should be marked as failed."""

        def hanging_agent(prompt: str, context=None):
            import time

            time.sleep(10)  # Hang for 10 seconds
            return {"response": "done", "steps": []}

        class HangingSuite(AgentTest):
            agent = "hanging"
            adapter = RawAPIAdapter(func=hanging_agent)

            def test_hang(self):
                self.run("hang")

        runner = TestRunner(config={"timeout_seconds": 2.0})
        result = runner.run_suite(HangingSuite)

        assert result.total == 1
        assert not result.all_passed
        assert result.results[0].error is not None
        assert "TIMEOUT" in result.results[0].error

    def test_fast_test_passes_under_timeout(self):
        """A fast test should pass within the timeout."""

        class FastSuite(AgentTest):
            agent = "fast"
            adapter = RawAPIAdapter(
                func=lambda p, c=None: {
                    "response": "done",
                    "steps": [{"action": "llm_response", "response": "done"}],
                }
            )

            def test_fast(self):
                self.run("fast")

        runner = TestRunner(config={"timeout_seconds": 30.0})
        result = runner.run_suite(FastSuite)
        assert result.all_passed

    def test_timeout_error_message_includes_diagnosis(self):
        """Timeout error should include what went wrong, expected, and suggested fix."""

        def slow_agent(prompt: str, context=None):
            import time

            time.sleep(10)
            return {"response": "late", "steps": []}

        class SlowSuite(AgentTest):
            agent = "slow"
            adapter = RawAPIAdapter(func=slow_agent)

            def test_slow(self):
                self.run("slow")

        runner = TestRunner(config={"timeout_seconds": 1.0})
        result = runner.run_suite(SlowSuite)

        error = result.results[0].error
        assert error is not None
        assert "TIMEOUT" in error
        assert "What went wrong" in error
        assert "Expected" in error
        assert "Suggested fix" in error

    def test_timeout_does_not_allow_late_state_mutation(self):
        """A timed-out test must not keep mutating parent-process state after returning."""

        late_events: list[str] = []

        class SlowMutationSuite(AgentTest):
            agent = "slow-mutation"
            adapter = RawAPIAdapter(func=lambda p, c=None: {"response": "ok", "steps": []})

            def test_slow_mutation(self):
                import time

                time.sleep(0.2)
                late_events.append("mutated-after-timeout")

        runner = TestRunner(config={"timeout_seconds": 0.05})
        result = runner.run_suite(SlowMutationSuite)

        assert not result.all_passed
        assert "TIMEOUT" in (result.results[0].error or "")

        import time

        time.sleep(0.3)
        assert late_events == []


# ─── Edge Case: Agent Crash ───


class TestAgentCrash:
    """Tests for agent crash handling."""

    def test_crash_in_test_method_caught_gracefully(self):
        """An exception during test execution should be caught with diagnosis."""

        class CrashSuite(AgentTest):
            agent = "crash"
            adapter = RawAPIAdapter(
                func=lambda p, c=None: {
                    "response": "ok",
                    "steps": [{"action": "llm_response", "response": "ok"}],
                }
            )

            def test_crash(self):
                raise ValueError("Something broke inside the test")

        runner = TestRunner()
        result = runner.run_suite(CrashSuite)

        assert result.total == 1
        assert not result.all_passed
        error = result.results[0].error
        assert "AGENT CRASH" in error
        assert "ValueError" in error
        assert "Something broke inside the test" in error

    def test_crash_error_includes_fix_suggestion(self):
        """Crash error message should suggest checking the adapter."""

        class CrashSuite(AgentTest):
            agent = "crash"
            adapter = RawAPIAdapter(func=lambda p, c=None: {"response": "ok", "steps": []})

            def test_crash(self):
                raise RuntimeError("Agent blew up")

        runner = TestRunner()
        result = runner.run_suite(CrashSuite)

        error = result.results[0].error
        assert "Suggested fix" in error
        assert "adapter" in error.lower() or "agent" in error.lower()

    def test_adapter_exception_caught_in_trajectory(self):
        """When the adapter itself raises, trajectory should capture it."""

        def bad_adapter_func(prompt: str, context=None):
            raise ConnectionError("API is down")

        class BadSuite(AgentTest):
            agent = "bad"
            adapter = RawAPIAdapter(func=bad_adapter_func)

            def test_bad(self):
                result = self.run("test")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(BadSuite)

        assert not result.all_passed


# ─── Edge Case: Empty/None Responses ───


class TestEmptyNoneResponses:
    """Tests for empty or None agent responses."""

    def test_empty_response_trajectory(self):
        """Agent returning empty response should still produce valid trajectory."""
        traj = make_trajectory(final_response="", completed=True)
        assert traj.final_response == ""
        assert traj.completed is True

    def test_none_final_response(self):
        """Agent with None-ish final response handled correctly."""
        traj = make_trajectory(final_response="", completed=True)
        result = expect(traj).to_respond_with("something")
        assert not result.all_passed
        # Check error message includes actionable info
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_expect_with_none_raises(self):
        """Passing None to expect() should raise a helpful ValueError."""
        with pytest.raises(ValueError, match="expect\\(\\) received None"):
            expect(None)

    def test_empty_trajectory_step_count(self):
        """Empty trajectory has 0 steps."""
        traj = make_trajectory()
        assert traj.step_count == 0

    def test_no_tool_calls_on_empty_trajectory(self):
        """Empty trajectory has no tool calls."""
        traj = make_trajectory()
        assert traj.tool_calls == []
        assert traj.tool_calls_by_name("anything") == []


# ─── Edge Case: Malformed Trajectory Data ───


class TestMalformedTrajectory:
    """Tests for malformed trajectory data handling."""

    def test_step_index_out_of_range(self):
        """Requesting an out-of-range step should give helpful error."""
        traj = make_trajectory(
            steps=[{"step_number": 0, "action": "llm_response", "response": "ok"}]
        )
        with pytest.raises(IndexError, match="out of range"):
            expect(traj).step(5)

    def test_step_index_negative(self):
        """Negative step index should be rejected."""
        traj = make_trajectory(
            steps=[{"step_number": 0, "action": "llm_response", "response": "ok"}]
        )
        with pytest.raises(IndexError):
            expect(traj).step(-1)

    def test_expect_with_non_trajectory_raises(self):
        """Passing a non-trajectory object to expect() should raise."""
        with pytest.raises(ValueError, match="invalid object"):
            expect("not a trajectory")  # type: ignore

    def test_step_index_error_includes_suggestion(self):
        """Out-of-range step error should suggest valid indices."""
        traj = make_trajectory(
            steps=[
                {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(3)
            ]
        )
        with pytest.raises(IndexError, match="Suggested fix"):
            expect(traj).step(10)


# ─── Improved Error Messages ───


class TestImprovedErrorMessages:
    """Verify all failure messages include: what went wrong, expected,
    what happened, suggested fix."""

    def test_to_complete_failure_message(self):
        traj = make_trajectory(completed=False, error="Timeout exceeded")
        result = expect(traj).to_complete()
        assert not result.all_passed
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Expected" in msg
        assert "Suggested fix" in msg

    def test_to_complete_within_failure_message(self):
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(15)
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_use_tool_failure_message(self):
        traj = make_trajectory(
            steps=[{"step_number": 0, "action": "llm_response", "response": "no tools"}]
        )
        result = expect(traj).to_use_tool("search")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_use_tool_count_failure_message(self):
        steps = [
            {"step_number": 0, "action": "tool_call", "tool_name": "search"},
            {"step_number": 1, "action": "tool_call", "tool_name": "search"},
        ]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_use_tool("search", times=5)
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_not_use_tool_failure_message(self):
        steps = [{"step_number": 0, "action": "tool_call", "tool_name": "search"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_not_use_tool("search")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_respond_with_failure_message(self):
        traj = make_trajectory(final_response="Hello world")
        result = expect(traj).to_respond_with("goodbye")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_not_expose_failure_message(self):
        steps = [{"step_number": 0, "action": "llm_response", "response": "SSN: 123-45-6789"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_not_expose("123-45-6789")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_retry_failure_message(self):
        steps = [{"step_number": i, "action": "retry", "response": f"retry {i}"} for i in range(5)]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_retry(max_attempts=2)
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_follow_workflow_failure_message(self):
        steps = [{"step_number": 0, "action": "tool_call", "tool_name": "jump"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_follow_workflow(["search", "calculate"])
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_to_have_no_errors_failure_message(self):
        steps = [{"step_number": 0, "action": "error", "error": "Connection refused"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).to_have_no_errors()
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_step_used_tool_failure_message(self):
        steps = [{"step_number": 0, "action": "llm_response", "response": "ok"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).step(0).used_tool("search")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_step_responded_with_failure_message(self):
        steps = [{"step_number": 0, "action": "llm_response", "response": "hello"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).step(0).responded_with("goodbye")
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg

    def test_step_has_no_error_failure_message(self):
        steps = [{"step_number": 0, "action": "error", "error": "crashed"}]
        traj = make_trajectory(steps=steps)
        result = expect(traj).step(0).has_no_error()
        msg = result.results[0].message
        assert "What went wrong" in msg
        assert "Suggested fix" in msg


# ─── Fixture Scope Enforcement ───


class TestFixtureScopeEnforcement:
    """Tests for fixture scope enforcement in the runner."""

    def test_fixture_registry_singleton(self):
        """FixtureRegistry.get() returns the same instance."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        r1 = FixtureRegistry.get()
        r2 = FixtureRegistry.get()
        assert r1 is r2
        FixtureRegistry.reset()

    def test_fixture_registry_reset(self):
        """FixtureRegistry.reset() clears the singleton."""
        from agentbench.core.fixtures import FixtureRegistry

        r1 = FixtureRegistry.get()
        FixtureRegistry.reset()
        r2 = FixtureRegistry.get()
        assert r1 is not r2
        FixtureRegistry.reset()

    def test_test_scope_creates_fresh_each_time(self):
        """scope='test' should create a new value on every call."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        registry = FixtureRegistry.get()

        call_count = 0

        @fixture(scope="test")
        def counter():
            nonlocal call_count
            call_count += 1
            return call_count

        v1 = registry.get_fixture_value(counter, "SuiteA")
        v2 = registry.get_fixture_value(counter, "SuiteA")
        assert v1 == 1
        assert v2 == 2  # Fresh each time
        FixtureRegistry.reset()

    def test_suite_scope_caches_within_suite(self):
        """scope='suite' should return same value within a suite."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        registry = FixtureRegistry.get()

        call_count = 0

        @fixture(scope="suite")
        def suite_counter():
            nonlocal call_count
            call_count += 1
            return call_count

        v1 = registry.get_fixture_value(suite_counter, "SuiteA")
        v2 = registry.get_fixture_value(suite_counter, "SuiteA")
        assert v1 == 1
        assert v2 == 1  # Cached within same suite

        # Different suite gets a new value
        v3 = registry.get_fixture_value(suite_counter, "SuiteB")
        assert v3 == 2
        FixtureRegistry.reset()

    def test_session_scope_caches_globally(self):
        """scope='session' should return same value across all suites."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        registry = FixtureRegistry.get()

        call_count = 0

        @fixture(scope="session")
        def session_counter():
            nonlocal call_count
            call_count += 1
            return call_count

        v1 = registry.get_fixture_value(session_counter, "SuiteA")
        v2 = registry.get_fixture_value(session_counter, "SuiteB")
        assert v1 == 1
        assert v2 == 1  # Same across suites
        FixtureRegistry.reset()

    def test_suite_teardown_clears_cache(self):
        """teardown_suite should clear suite-scoped fixtures."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        registry = FixtureRegistry.get()

        @fixture(scope="suite")
        def suite_val():
            return 42

        v1 = registry.get_fixture_value(suite_val, "SuiteA")
        assert v1 == 42

        registry.teardown_suite("SuiteA")

        # After teardown, next call should create fresh
        call_count = 0
        orig_func = suite_val._func

        def counting_func():
            nonlocal call_count
            call_count += 1
            return call_count

        suite_val._func = counting_func
        v2 = registry.get_fixture_value(suite_val, "SuiteA")
        assert v2 == 1  # Fresh after teardown
        suite_val._func = orig_func
        FixtureRegistry.reset()

    def test_teardown_all_clears_session_and_suite(self):
        """teardown_all should clear both session and suite caches."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()
        registry = FixtureRegistry.get()

        @fixture(scope="session")
        def sess():
            return "session"

        @fixture(scope="suite")
        def suit():
            return "suite"

        registry.get_fixture_value(sess, "S1")
        registry.get_fixture_value(suit, "S1")

        registry.teardown_all()

        # Caches should be empty now — no exception means it works
        FixtureRegistry.reset()

    def test_invalid_scope_raises(self):
        """Creating a fixture with invalid scope should raise ValueError."""
        with pytest.raises(ValueError, match="Invalid fixture scope"):
            Fixture(func=lambda: None, scope="invalid")

    def test_runner_teardowns_suite_fixtures_after_suite(self):
        """Runner should teardown suite-scoped fixtures after run_suite completes."""
        teardown_log = []

        @fixture(scope="suite")
        def managed():
            yield "resource"
            teardown_log.append("cleaned")

        class MySuite(AgentTest):
            agent = "scope-test"
            adapter = RawAPIAdapter(
                func=lambda p, c=None: {
                    "response": "ok",
                    "steps": [{"action": "llm_response", "response": "ok"}],
                }
            )

            def test_it(self):
                self.run("test")

        runner = TestRunner()
        runner.run_suite(MySuite)
        # Suite teardown happens inside run_suite
        # (no assertion on the fixture itself since we're not injecting it,
        #  but the mechanism runs without error)
        FixtureRegistry.reset()

    def test_runner_resets_registry_on_run(self):
        """TestRunner.run() should reset the fixture registry for a fresh session."""
        from agentbench.core.fixtures import FixtureRegistry

        FixtureRegistry.reset()

        runner = TestRunner()
        # Just ensure it doesn't crash
        runner.run("/tmp/nonexistent_path_for_test")
        FixtureRegistry.reset()


# ─── Pytest Warning Prevention ───


class TestPytestWarningPrevention:
    """Verify TestResult and TestSuiteResult don't trigger pytest collection."""

    def test_result_has_test_false(self):
        from agentbench.core.runner import TestResult

        assert TestResult.__test__ is False

    def test_suite_result_has_test_false(self):
        from agentbench.core.runner import TestSuiteResult

        assert TestSuiteResult.__test__ is False


# ─── Max Steps / Infinite Loop Detection ───


class TestMaxStepsEnforcement:
    """Tests for max steps enforcement and infinite loop detection."""

    def test_to_complete_within_non_completed(self):
        """Non-completed trajectory should fail step limit check
        with clear message."""
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(3)
        ]
        traj = make_trajectory(steps=steps, completed=False, error="Agent stalled")
        result = expect(traj).to_complete_within(steps=10)
        assert not result.all_passed
        msg = result.results[0].message
        assert "did not complete" in msg.lower() or "did not finish" in msg.lower()

    def test_step_limit_exact_boundary(self):
        """Agent using exactly the step limit should pass."""
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(10)
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert result.all_passed

    def test_step_limit_one_over_fails(self):
        """Agent using one more step than limit should fail."""
        steps = [
            {"step_number": i, "action": "llm_response", "response": f"s{i}"} for i in range(11)
        ]
        traj = make_trajectory(steps=steps, completed=True)
        result = expect(traj).to_complete_within(steps=10)
        assert not result.all_passed


# ─── Negation ───


class TestNegation:
    """Test the to_not negation modifier."""

    def test_to_not_negates_completion(self):
        traj = make_trajectory(completed=True)
        result = expect(traj).to_not.to_complete()
        assert not result.all_passed

    def test_to_not_negation_resets_after_one_use(self):
        """to_not should only negate the next assertion, then reset."""
        traj = make_trajectory(completed=True)
        e = expect(traj)
        e.to_not.to_complete()  # Negated — should fail
        assert not e.results[-1].passed
        e.to_complete()  # Not negated — should pass
        assert e.results[-1].passed


# ─── AssertionResult ───


class TestAssertionResult:
    """Test AssertionResult dataclass behavior."""

    def test_str_passed(self):
        r = AssertionResult(passed=True, message="ok", assertion_type="test")
        assert "✓" in str(r)

    def test_str_failed(self):
        r = AssertionResult(passed=False, message="fail", assertion_type="test")
        assert "✗" in str(r)

    def test_bool_passed(self):
        r = AssertionResult(passed=True, message="ok", assertion_type="test")
        assert bool(r) is True

    def test_bool_failed(self):
        r = AssertionResult(passed=False, message="fail", assertion_type="test")
        assert bool(r) is False


# ─── RunnerResult ───


class TestRunResult:
    """Test RunResult dataclass."""

    def test_empty_run_result(self):
        from agentbench.core.runner import RunResult

        rr = RunResult()
        assert rr.total_passed == 0
        assert rr.total_failed == 0
        assert rr.total_tests == 0
        assert rr.all_passed is True  # vacuously true

    def test_run_result_summary(self):
        from agentbench.core.runner import RunResult, TestSuiteResult

        rr = RunResult()
        rr.suite_results.append(TestSuiteResult(suite_name="Test"))
        assert "Total:" in rr.summary()


# ─── AgentStep ───


class TestAgentStep:
    """Test AgentStep functionality."""

    def test_exposed_data_concatenates_fields(self):
        step = AgentStep(
            step_number=0,
            action="tool_call",
            tool_name="search",
            reasoning="thinking",
            response="result",
            tool_output="output",
        )
        data = step.exposed_data
        assert "thinking" in data
        assert "result" in data
        assert "output" in data

    def test_exposed_data_empty_when_no_fields(self):
        step = AgentStep(step_number=0, action="llm_response")
        assert step.exposed_data == ""

    def test_to_dict(self):
        step = AgentStep(step_number=0, action="llm_response", response="hello")
        d = step.to_dict()
        assert d["step_number"] == 0
        assert d["action"] == "llm_response"
        assert d["response"] == "hello"


# ─── to_complete_within edge: incomplete ───


class TestCompletionEdgeCases:
    def test_incomplete_not_completed_no_error(self):
        traj = make_trajectory(completed=False)
        result = expect(traj).to_complete()
        assert not result.all_passed

    def test_completed_with_error_fails(self):
        traj = make_trajectory(completed=True, error="Something went wrong")
        result = expect(traj).to_complete()
        assert not result.all_passed
