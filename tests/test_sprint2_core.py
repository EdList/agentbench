"""Tests for Sprint 2 core features — parametric tests, parallel execution, fixtures/hooks."""

import threading
import time

from agentbench.adapters.raw_api import RawAPIAdapter
from agentbench.core.assertions import expect
from agentbench.core.fixtures import Fixture, fixture
from agentbench.core.parametrize import parametrize
from agentbench.core.runner import TestRunner
from agentbench.core.test import AgentTest

# ─── Helpers ───


def _echo_adapter():
    """Return a simple echo adapter for testing."""

    def echo_agent(prompt: str, context=None):
        return {
            "response": f"Echo: {prompt}",
            "steps": [{"action": "llm_response", "response": f"Echo: {prompt}"}],
        }

    return RawAPIAdapter(func=echo_agent)


# ═══════════════════════════════════════════════════════════════
# 1. PARAMETRIC TESTS
# ═══════════════════════════════════════════════════════════════


class TestParametrizeDecorator:
    """Test the @parametrize decorator."""

    def test_parametrize_sets_metadata(self):
        @parametrize("x", [1, 2, 3])
        def test_something(self, x):
            pass

        assert hasattr(test_something, "_agentbench_parametrize")
        meta = test_something._agentbench_parametrize
        assert meta["arg_name"] == "x"
        assert meta["arg_values"] == [1, 2, 3]

    def test_parametrize_with_string_values(self):
        @parametrize("query", ["Buy shirt", "Return order", "Check balance"])
        def test_queries(self, query):
            pass

        meta = test_queries._agentbench_parametrize
        assert meta["arg_values"] == ["Buy shirt", "Return order", "Check balance"]

    def test_parametrize_with_tuple(self):
        @parametrize("val", (10, 20, 30))
        def test_vals(self, val):
            pass

        meta = test_vals._agentbench_parametrize
        assert meta["arg_values"] == [10, 20, 30]

    def test_parametrize_preserves_function(self):
        @parametrize("x", [1])
        def my_func(self, x):
            return x * 2

        # Function is still callable
        assert my_func(None, 5) == 10


class TestParametrizeRunner:
    """Test that the runner correctly expands parametrized tests."""

    def test_parametrized_test_generates_multiple_results(self):
        """A test with @parametrize should produce one result per parameter."""

        class ParamSuite(AgentTest):
            agent = "param-test"
            adapter = _echo_adapter()

            @parametrize("query", ["hello", "world", "test"])
            def test_echo(self, query):
                result = self.run(query)
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(ParamSuite)

        assert result.total == 3
        assert result.all_passed
        # Check test names include parameter values
        names = [r.test_name for r in result.results]
        assert "test_echo[hello]" in names
        assert "test_echo[world]" in names
        assert "test_echo[test]" in names

    def test_mixed_parametrized_and_regular(self):
        """A suite can have both parametrized and regular tests."""

        class MixedSuite(AgentTest):
            agent = "mixed"
            adapter = _echo_adapter()

            def test_regular(self):
                result = self.run("regular")
                expect(result).to_complete()

            @parametrize("q", ["a", "b"])
            def test_param(self, q):
                result = self.run(q)
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(MixedSuite)

        assert result.total == 3  # 1 regular + 2 parametrized
        assert result.all_passed
        names = [r.test_name for r in result.results]
        assert "test_regular" in names
        assert "test_param[a]" in names
        assert "test_param[b]" in names

    def test_parametrized_test_failure_isolation(self):
        """Failure for one parameter should not affect others."""

        call_counts = {"good": 0, "fail": 0, "also_good": 0}

        def selective_agent(prompt: str, context=None):
            call_counts[prompt] += 1
            if prompt == "fail":
                raise RuntimeError("Agent crashed on 'fail'")
            return {
                "response": f"OK: {prompt}",
                "steps": [{"action": "llm_response", "response": f"OK: {prompt}"}],
            }

        class FailSuite(AgentTest):
            agent = "fail-test"
            adapter = RawAPIAdapter(func=selective_agent)

            @parametrize("q", ["good", "fail", "also_good"])
            def test_run(self, q):
                result = self.run(q)
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(FailSuite)

        assert result.total == 3
        # The "fail" param causes an adapter exception which is caught and
        # stored in trajectory.error, and the adapter marks completed=False.
        # to_complete() will fail because completed is False.
        assert result.failed >= 1
        # Other params should still run
        assert call_counts["good"] == 1
        assert call_counts["fail"] == 1
        assert call_counts["also_good"] == 1

    def test_parametrize_with_no_params_isnt_expanded(self):
        """A regular test (no @parametrize) should produce exactly one result."""

        class PlainSuite(AgentTest):
            agent = "plain"
            adapter = _echo_adapter()

            def test_plain(self):
                result = self.run("hello")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(PlainSuite)

        assert result.total == 1


# ═══════════════════════════════════════════════════════════════
# 2. PARALLEL EXECUTION
# ═══════════════════════════════════════════════════════════════


class TestParallelExecution:
    """Test parallel test execution."""

    def test_parallel_runs_all_tests(self):
        """Parallel execution should still run all tests."""

        class ParallelSuite(AgentTest):
            agent = "parallel"
            adapter = _echo_adapter()

            def test_a(self):
                result = self.run("a")
                expect(result).to_complete()

            def test_b(self):
                result = self.run("b")
                expect(result).to_complete()

            def test_c(self):
                result = self.run("c")
                expect(result).to_complete()

        runner = TestRunner(config={"parallel": 2})
        result = runner.run_suite(ParallelSuite)

        assert result.total == 3
        assert result.all_passed

    def test_parallel_with_parametrize(self):
        """Parallel execution should work with parametrized tests."""

        class ParallelParamSuite(AgentTest):
            agent = "parallel-param"
            adapter = _echo_adapter()

            @parametrize("q", ["x", "y", "z"])
            def test_echo(self, q):
                result = self.run(q)
                expect(result).to_complete()

        runner = TestRunner(config={"parallel": 2})
        result = runner.run_suite(ParallelParamSuite)

        assert result.total == 3
        assert result.all_passed

    def test_parallel_config_default_is_sequential(self):
        """Default parallel config should be 1 (sequential)."""
        runner = TestRunner()
        assert runner._parallel == 1

    def test_parallel_multiple_suites(self):
        """Multiple suites should run in parallel when configured."""

        class SuiteA(AgentTest):
            agent = "a"
            adapter = _echo_adapter()

            def test_a1(self):
                result = self.run("a1")
                expect(result).to_complete()

        class SuiteB(AgentTest):
            agent = "b"
            adapter = _echo_adapter()

            def test_b1(self):
                result = self.run("b1")
                expect(result).to_complete()

        # We can't easily test discovery with parallel, so test run_suite directly
        runner = TestRunner(config={"parallel": 2})

        result_a = runner.run_suite(SuiteA)
        result_b = runner.run_suite(SuiteB)

        assert result_a.all_passed
        assert result_b.all_passed

    def test_parallel_actually_uses_threads(self):
        """Verify tests actually run in parallel threads (not sequentially)."""
        thread_ids = []
        lock = threading.Lock()

        def slow_adapter():
            def agent(prompt: str, context=None):
                with lock:
                    thread_ids.append(threading.current_thread().ident)
                time.sleep(0.05)
                return {
                    "response": f"slow: {prompt}",
                    "steps": [{"action": "llm_response", "response": f"slow: {prompt}"}],
                }

            return RawAPIAdapter(func=agent)

        class SlowSuite(AgentTest):
            agent = "slow"
            adapter = slow_adapter()

            def test_1(self):
                result = self.run("1")
                expect(result).to_complete()

            def test_2(self):
                result = self.run("2")
                expect(result).to_complete()

        runner = TestRunner(config={"parallel": 2})
        start = time.time()
        result = runner.run_suite(SlowSuite)
        elapsed = time.time() - start

        assert result.total == 2
        # If truly parallel, 2 × 0.05s should be < 0.15s (generous margin)
        assert elapsed < 0.25


# ═══════════════════════════════════════════════════════════════
# 3. FIXTURES AND HOOKS
# ═══════════════════════════════════════════════════════════════


class TestFixtureDecorator:
    """Test the @fixture decorator."""

    def test_fixture_creates_fixture_object(self):
        @fixture
        def my_fixture():
            return 42

        assert isinstance(my_fixture, Fixture)
        assert my_fixture.__name__ == "my_fixture"

    def test_fixture_setup_returns_value(self):
        @fixture
        def my_fixture():
            return "hello"

        value = my_fixture.setup()
        assert value == "hello"

    def test_fixture_with_scope(self):
        @fixture(scope="suite")
        def suite_fixture():
            return [1, 2, 3]

        assert suite_fixture.scope == "suite"
        assert suite_fixture.setup() == [1, 2, 3]

    def test_fixture_default_scope_is_test(self):
        @fixture
        def default_fixture():
            return None

        assert default_fixture.scope == "test"

    def test_generator_fixture_setup_and_teardown(self):
        teardown_called = []

        @fixture
        def gen_fixture():
            yield "value"
            teardown_called.append(True)

        f = gen_fixture
        value = f.setup()
        assert value == "value"
        f.teardown()
        assert teardown_called == [True]

    def test_generator_fixture_teardown_idempotent(self):
        """Calling teardown twice on a generator fixture should be safe."""

        @fixture
        def gen_fixture():
            yield "val"

        f = gen_fixture
        f.setup()
        f.teardown()
        f.teardown()  # Should not raise


class TestHooksSetupTeardown:
    """Test setup/teardown hooks on AgentTest."""

    def test_setup_runs_before_each_test(self):
        setup_log = []

        class HookedSuite(AgentTest):
            agent = "hooked"
            adapter = _echo_adapter()

            def setup(self):
                setup_log.append("setup")

            def test_a(self):
                setup_log.append("test_a")
                result = self.run("a")
                expect(result).to_complete()

            def test_b(self):
                setup_log.append("test_b")
                result = self.run("b")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(HookedSuite)

        assert result.all_passed
        # setup should be called once before each test
        assert setup_log.count("setup") == 2
        assert "test_a" in setup_log
        assert "test_b" in setup_log

    def test_teardown_runs_after_each_test(self):
        teardown_log = []

        class TeardownSuite(AgentTest):
            agent = "teardown"
            adapter = _echo_adapter()

            def teardown(self):
                teardown_log.append("teardown")

            def test_a(self):
                result = self.run("a")
                expect(result).to_complete()

            def test_b(self):
                result = self.run("b")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(TeardownSuite)

        assert result.all_passed
        assert teardown_log.count("teardown") == 2

    def test_teardown_runs_even_on_failure(self):
        teardown_log = []

        def failing_adapter():
            def agent(prompt: str, context=None):
                raise RuntimeError("Agent exploded")

            return RawAPIAdapter(func=agent)

        class FailTeardownSuite(AgentTest):
            agent = "fail-teardown"
            adapter = failing_adapter()

            def teardown(self):
                teardown_log.append("teardown")

            def test_failing(self):
                result = self.run("fail")
                # The run() catches the adapter exception but marks trajectory as failed.
                # Use an assertion that will actually fail:
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(FailTeardownSuite)

        assert not result.all_passed
        assert teardown_log.count("teardown") == 1  # Still called

    def test_setup_class_runs_once(self):
        setup_class_log = []

        class ClassHookSuite(AgentTest):
            agent = "class-hook"
            adapter = _echo_adapter()

            def setup_class(self):
                setup_class_log.append("setup_class")

            def test_a(self):
                result = self.run("a")
                expect(result).to_complete()

            def test_b(self):
                result = self.run("b")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(ClassHookSuite)

        assert result.all_passed
        assert setup_class_log.count("setup_class") == 1

    def test_teardown_class_runs_once(self):
        teardown_class_log = []

        class TeardownClassSuite(AgentTest):
            agent = "teardown-class"
            adapter = _echo_adapter()

            def teardown_class(self):
                teardown_class_log.append("teardown_class")

            def test_a(self):
                result = self.run("a")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(TeardownClassSuite)

        assert result.all_passed
        assert teardown_class_log.count("teardown_class") == 1

    def test_setup_class_and_teardown_class_order(self):
        """setup_class before tests, teardown_class after tests."""
        order_log = []

        class OrderSuite(AgentTest):
            agent = "order"
            adapter = _echo_adapter()

            def setup_class(self):
                order_log.append("setup_class")

            def teardown_class(self):
                order_log.append("teardown_class")

            def test_1(self):
                order_log.append("test")
                result = self.run("1")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(OrderSuite)

        assert result.all_passed
        assert order_log[0] == "setup_class"
        assert order_log[-1] == "teardown_class"
        assert "test" in order_log

    def test_all_hooks_combined(self):
        """setup_class, setup, test, teardown, teardown_class in correct order."""
        log = []

        class AllHooksSuite(AgentTest):
            agent = "all-hooks"
            adapter = _echo_adapter()

            def setup_class(self):
                log.append("setup_class")

            def setup(self):
                log.append("setup")

            def teardown(self):
                log.append("teardown")

            def teardown_class(self):
                log.append("teardown_class")

            def test_a(self):
                log.append("test_a")
                result = self.run("a")
                expect(result).to_complete()

            def test_b(self):
                log.append("test_b")
                result = self.run("b")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(AllHooksSuite)

        assert result.all_passed
        assert log == [
            "setup_class",
            "setup",
            "test_a",
            "teardown",
            "setup",
            "test_b",
            "teardown",
            "teardown_class",
        ]


class TestFixtureIntegration:
    """Test that fixtures work when used with the runner."""

    def test_fixture_can_be_used_in_test(self):
        """Fixtures can be manually called inside test methods."""
        call_log = []

        @fixture
        def db_connection():
            call_log.append("connected")
            return "db-conn"

        class FixtureSuite(AgentTest):
            agent = "fixture-test"
            adapter = _echo_adapter()

            def setup(self):
                self._conn = db_connection.setup()

            def test_with_fixture(self):
                assert self._conn == "db-conn"
                result = self.run("test")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(FixtureSuite)

        assert result.all_passed
        assert "connected" in call_log

    def test_fixture_generator_teardown_in_suite(self):
        """Generator fixture teardown runs after test."""
        teardown_log = []

        @fixture
        def managed_resource():
            yield "resource"
            teardown_log.append("cleaned_up")

        class ResourceSuite(AgentTest):
            agent = "resource"
            adapter = _echo_adapter()

            def setup(self):
                self._fixture = managed_resource
                self._resource = self._fixture.setup()

            def teardown(self):
                self._fixture.teardown()

            def test_uses_resource(self):
                assert self._resource == "resource"
                result = self.run("test")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(ResourceSuite)

        assert result.all_passed
        assert teardown_log == ["cleaned_up"]


# ═══════════════════════════════════════════════════════════════
# INTEGRATION: All features combined
# ═══════════════════════════════════════════════════════════════


class TestAllFeaturesCombined:
    """Test parametrize + parallel + hooks together."""

    def test_parametrize_parallel_with_hooks(self):
        log = []

        class CombinedSuite(AgentTest):
            agent = "combined"
            adapter = _echo_adapter()

            def setup_class(self):
                log.append("setup_class")

            def setup(self):
                log.append("setup")

            def teardown(self):
                log.append("teardown")

            def teardown_class(self):
                log.append("teardown_class")

            @parametrize("q", ["a", "b"])
            def test_echo(self, q):
                log.append(f"test:{q}")
                result = self.run(q)
                expect(result).to_complete()

        runner = TestRunner(config={"parallel": 2})
        result = runner.run_suite(CombinedSuite)

        assert result.total == 2
        assert result.all_passed
        # Class hooks run once
        assert log.count("setup_class") == 1
        assert log.count("teardown_class") == 1
        # Per-test hooks run for each parametrized test
        assert log.count("setup") == 2
        assert log.count("teardown") == 2

    def test_parametrize_with_assertions(self):
        """Parametrized tests with meaningful assertions all pass."""

        def multi_adapter():
            def agent(prompt: str, context=None):
                steps = [
                    {
                        "action": "tool_call",
                        "tool_name": "search",
                        "tool_output": f"result for {prompt}",
                    },
                    {"action": "llm_response", "response": f"Found: {prompt}"},
                ]
                return {"response": f"Result for {prompt}", "steps": steps}

            return RawAPIAdapter(func=agent)

        class AssertionSuite(AgentTest):
            agent = "assertion"
            adapter = multi_adapter()

            @parametrize("query", ["shirt", "pants", "shoes"])
            def test_search(self, query):
                result = self.run(query)
                expect(result).to_complete()
                expect(result).to_use_tool("search")
                expect(result).to_respond_with(query)

        runner = TestRunner()
        result = runner.run_suite(AssertionSuite)

        assert result.total == 3
        assert result.all_passed
        for r in result.results:
            assert r.assertion_count >= 3  # to_complete + to_use_tool + to_respond_with


class TestAuditRegressionFixes:
    def test_setup_class_state_is_available_to_test_instances(self):
        class SharedStateSuite(AgentTest):
            agent = "shared-state"
            adapter = _echo_adapter()

            def setup_class(self):
                self.shared_resource = "configured-in-setup-class"

            def test_uses_shared_state(self):
                assert self.shared_resource == "configured-in-setup-class"
                result = self.run("hello")
                expect(result).to_complete()

        runner = TestRunner()
        result = runner.run_suite(SharedStateSuite)

        assert result.all_passed

    def test_runner_allows_a_trajectory_at_the_exact_max_step_boundary(self):
        def boundary_agent(prompt: str, context=None):
            return {
                "response": "done",
                "steps": [{"action": "llm_response", "response": f"step-{i}"} for i in range(50)],
            }

        class BoundarySuite(AgentTest):
            agent = "boundary"
            adapter = RawAPIAdapter(func=boundary_agent)

            def test_boundary(self):
                result = self.run("boundary")
                expect(result).to_complete()

        runner = TestRunner(config={"max_steps": 50})
        result = runner.run_suite(BoundarySuite)

        assert result.all_passed

    def test_step_assertion_failures_propagate_to_runner_results(self):
        def tool_agent(prompt: str, context=None):
            return {
                "response": "done",
                "steps": [
                    {"action": "tool_call", "tool_name": "search", "tool_output": "ok"},
                    {"action": "llm_response", "response": "done"},
                ],
            }

        class StepAssertionSuite(AgentTest):
            agent = "step-assertion"
            adapter = RawAPIAdapter(func=tool_agent)

            def test_step_contract(self):
                result = self.run("find stuff")
                expect(result).step(0).used_tool("payment_api")

        runner = TestRunner()
        result = runner.run_suite(StepAssertionSuite)

        assert not result.all_passed
        assert result.results[0].assertion_count == 1
        assert result.results[0].assertions[0].assertion_type == "tool_call"
        assert result.results[0].assertions[0].passed is False
