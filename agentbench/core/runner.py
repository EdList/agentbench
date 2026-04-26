"""Test runner — discovers, executes, and reports on agent test suites."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import logging
import threading
import time
import traceback

logger = logging.getLogger(__name__)
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbench.core.assertions import (
    AssertionResult,
    _clear_active_test,
    _set_active_test,
)
from agentbench.core.fixtures import FixtureRegistry
from agentbench.core.test import AgentTest, AgentTrajectory
from agentbench.multiagent.test import MultiAgentTest
from agentbench.property.properties import Property


@dataclass
class TestResult:
    """Result of a single test method."""

    __test__ = False  # Prevent pytest collection warning

    test_name: str
    suite_name: str
    passed: bool = False
    assertions: list[AssertionResult] = field(default_factory=list)
    trajectory: AgentTrajectory | None = None
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def assertion_count(self) -> int:
        return len(self.assertions)

    @property
    def passed_assertions(self) -> int:
        return sum(1 for a in self.assertions if a.passed)

    @property
    def failed_assertions(self) -> list[AssertionResult]:
        return [a for a in self.assertions if not a.passed]


@dataclass
class TestSuiteResult:
    """Result of an entire test suite run."""

    __test__ = False  # Prevent pytest collection warning

    suite_name: str
    results: list[TestResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def passed(self) -> int:
        return sum(1 for r in self.results if r.passed)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.passed)

    @property
    def skipped(self) -> int:
        return sum(1 for r in self.results if r.error and "skip" in r.error.lower())

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    def summary(self) -> str:
        lines = [
            f"\n{'=' * 60}",
            f"  Suite: {self.suite_name}",
            f"  {self.passed} passed, {self.failed} failed, {self.skipped} skipped",
            f"  Duration: {self.total_duration_ms / 1000:.1f}s",
            f"{'=' * 60}",
        ]

        for r in self.results:
            icon = "✓" if r.passed else "✗"
            duration = f"({r.duration_ms / 1000:.1f}s)" if r.duration_ms else ""
            lines.append(f"  {icon} {r.test_name} {duration}")
            if not r.passed:
                for a in r.failed_assertions:
                    lines.append(f"    → {a.message}")
                if r.error:
                    lines.append(f"    → ERROR: {r.error}")

        return "\n".join(lines)


@dataclass
class RunResult:
    """Result of a complete test run (multiple suites)."""

    suite_results: list[TestSuiteResult] = field(default_factory=list)
    total_duration_ms: float = 0.0

    @property
    def total_passed(self) -> int:
        return sum(s.passed for s in self.suite_results)

    @property
    def total_failed(self) -> int:
        return sum(s.failed for s in self.suite_results)

    @property
    def total_tests(self) -> int:
        return sum(s.total for s in self.suite_results)

    @property
    def all_passed(self) -> bool:
        return all(s.all_passed for s in self.suite_results)

    def summary(self) -> str:
        lines = [s.summary() for s in self.suite_results]
        lines.append(f"\n{'─' * 60}")
        lines.append(
            f"  Total: {self.total_passed} passed, {self.total_failed} failed, "
            f"{self.total_tests} tests"
        )
        lines.append(f"  Duration: {self.total_duration_ms / 1000:.1f}s")
        return "\n".join(lines)


class TestRunner:
    """Discovers and runs agent test suites."""

    # Prevent pytest from trying to collect this class as a test
    __test__ = False

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}
        self._verbose = self._config.get("verbose", False)
        self._filter = self._config.get("filter", None)
        self._parallel = max(1, self._config.get("parallel", 1))
        self._default_timeout = self._config.get("timeout_seconds", 300.0)
        self._max_steps = self._config.get("max_steps", 50)
        # Fixture registry for scope management
        self._fixture_registry = FixtureRegistry.get()

    def discover_suites(self, path: Path | str) -> list[type]:
        """Discover AgentTest and MultiAgentTest subclasses in the given path."""
        path = Path(path)
        suites: list[type] = []

        if path.is_file() and path.suffix == ".py":
            suites.extend(self._find_suites_in_file(path))
        elif path.is_dir():
            for py_file in sorted(path.rglob("test_*.py")):
                suites.extend(self._find_suites_in_file(py_file))

        # Check for adversarial suite attachments and expand
        additional: list[type] = []
        for suite in list(suites):
            adversarial_suite_cls = getattr(suite, "_adversarial_suite", None)
            if adversarial_suite_cls is not None:
                additional.append(adversarial_suite_cls)
        suites.extend(additional)

        return suites

    def _find_suites_in_file(self, path: Path) -> list[type]:
        """Find AgentTest and MultiAgentTest subclasses in a Python file."""
        suites = []
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, AgentTest) and obj is not AgentTest:
                        suites.append(obj)
                    elif issubclass(obj, MultiAgentTest) and obj is not MultiAgentTest:
                        suites.append(obj)
        except Exception as e:
            import logging

            logging.warning("Could not load %s: %s", path, e)
        return suites

    def _discover_test_methods(self, suite_class: type) -> list[tuple[str, str, dict | None]]:
        """Discover test methods and their parametrize metadata.

        Returns:
            List of (method_name, display_name, param_info) where param_info
            is None for a plain test or a dict with metadata for parametrized,
            property-based, or adversarial variant iterations.
        """
        # Try to create temp instance for method discovery
        try:
            temp_instance = suite_class()
        except Exception:
            temp_instance = None

        test_methods: list[str] = []
        if temp_instance is not None:
            test_methods = [
                name
                for name, method in inspect.getmembers(temp_instance, predicate=inspect.ismethod)
                if name.startswith("test_")
            ]

        # Apply filter if specified
        if self._filter:
            import re

            pattern = re.compile(self._filter, re.IGNORECASE)
            test_methods = [m for m in test_methods if pattern.search(m)]

        expanded: list[tuple[str, str, dict | None]] = []
        for method_name in test_methods:
            raw_method = getattr(suite_class, method_name, None)
            if raw_method is None:
                raw_method = getattr(type(temp_instance), method_name, None)

            parametrize_meta = getattr(raw_method, "_agentbench_parametrize", None)
            if parametrize_meta:
                arg_name = parametrize_meta["arg_name"]
                for value in parametrize_meta["arg_values"]:
                    display_name = f"{method_name}[{value}]"
                    param = {"arg_name": arg_name, "value": value}
                    expanded.append((method_name, display_name, param))
            else:
                expanded.append((method_name, method_name, None))

        # --- Discover Property-based test attributes ---
        discovered_names = {name for name, _, _ in expanded}
        for attr_name in dir(suite_class):
            if not attr_name.startswith("test_"):
                continue
            attr = getattr(suite_class, attr_name, None)
            if attr is None:
                continue
            is_prop = isinstance(attr, Property)
            has_meta = hasattr(attr, "_agentbench_property")
            if (is_prop or has_meta) and attr_name not in discovered_names:
                logger.warning(
                    "Property-based test discovered for '%s' — experimental feature",
                    attr_name,
                )
                expanded.append((attr_name, attr_name, {"property_test": True}))

        # --- Adversarial variant expansion ---
        if getattr(suite_class, "_adversarial_enabled", False):
            logger.warning("Adversarial variant expansion is experimental — test quality may vary")
            config = getattr(suite_class, "_adversarial_config", {})
            mutators = config.get("mutators", [])
            count = config.get("count", 5)

            original_methods = getattr(suite_class, "_adversarial_original_methods", {})

            for method_name in test_methods:
                # Skip methods that are already adversarial variants
                if "_adversarial_" in method_name:
                    continue

                # Extract a base prompt from the original method
                base_prompt = "default test prompt"
                if method_name in original_methods:
                    extracted = self._extract_adversarial_prompt(original_methods[method_name])
                    if extracted:
                        base_prompt = extracted

                for mutator in mutators:
                    variants = mutator.generate(base_prompt)[:count]
                    for i, variant_prompt in enumerate(variants):
                        variant_name = f"{method_name}_adversarial_{i}"
                        param = {
                            "adversarial_variant": True,
                            "original_method": method_name,
                            "variant_prompt": variant_prompt,
                        }
                        expanded.append((variant_name, variant_name, param))

        return expanded

    def run_suite(self, suite_class: type) -> TestSuiteResult:
        """Run all test methods in a suite."""
        suite_name = suite_class.__name__
        suite_result = TestSuiteResult(suite_name=suite_name)
        suite_start = time.time()

        # Handle MultiAgentTest subclasses with a simpler execution path
        if issubclass(suite_class, MultiAgentTest):
            return self._run_multi_agent_suite(suite_class, suite_result, suite_start)

        # Discover test methods (with parametrize expansion)
        test_items = self._discover_test_methods(suite_class)

        if self._parallel > 1:
            # Parallel execution of individual tests within the suite
            # Create a shared class-level instance for setup_class / teardown_class
            class_instance = suite_class()

            # setup_class hook
            self._run_class_hook(class_instance, "setup_class")
            shared_class_state = {
                key: value
                for key, value in class_instance.__dict__.items()
                if key not in {"trajectory", "_expectations"}
            }

            try:
                with ThreadPoolExecutor(max_workers=self._parallel) as executor:
                    futures = {}
                    for idx, (method_name, display_name, param_info) in enumerate(test_items):
                        instance = suite_class()
                        instance.__dict__.update(shared_class_state)
                        future = executor.submit(
                            self._run_single_test,
                            instance,
                            method_name,
                            display_name,
                            param_info,
                            suite_name,
                        )
                        futures[future] = idx

                    results_by_index: dict[int, TestResult] = {}
                    for future in as_completed(futures):
                        result = future.result()
                        results_by_index[futures[future]] = result
                    for idx in sorted(results_by_index):
                        suite_result.results.append(results_by_index[idx])
            finally:
                # teardown_class hook
                self._run_class_hook(class_instance, "teardown_class")
                # Teardown suite-scoped fixtures for this suite
                self._fixture_registry.teardown_suite(suite_name)
        else:
            # Sequential execution
            class_instance = suite_class()

            # setup_class hook
            self._run_class_hook(class_instance, "setup_class")
            shared_class_state = {
                key: value
                for key, value in class_instance.__dict__.items()
                if key not in {"trajectory", "_expectations"}
            }

            try:
                for method_name, display_name, param_info in test_items:
                    # Create a fresh instance for each test to prevent state leakage
                    instance = suite_class()
                    instance.__dict__.update(shared_class_state)
                    result = self._run_single_test(
                        instance, method_name, display_name, param_info, suite_name
                    )
                    suite_result.results.append(result)
            finally:
                # teardown_class hook
                self._run_class_hook(class_instance, "teardown_class")
                # Teardown suite-scoped fixtures for this suite
                self._fixture_registry.teardown_suite(suite_name)

        suite_result.total_duration_ms = (time.time() - suite_start) * 1000
        return suite_result

    def _run_class_hook(self, instance: AgentTest, hook_name: str) -> None:
        """Run a class-level hook (setup_class / teardown_class) if it exists."""
        hook = getattr(instance, hook_name, None)
        if hook and callable(hook):
            try:
                hook()
            except Exception as e:
                import logging

                logging.warning("Error in %s for %s: %s", hook_name, type(instance).__name__, e)

    def _run_single_test(
        self,
        instance: AgentTest,
        method_name: str,
        display_name: str,
        param_info: dict | None,
        suite_name: str,
    ) -> TestResult:
        """Run a single test (with setup/teardown hooks and optional parametrize)."""
        test_start = time.time()
        result = TestResult(test_name=display_name, suite_name=suite_name)

        # Determine timeout for this test
        timeout_seconds = self._config.get("timeout_seconds", self._default_timeout)

        # Use a threading-based timeout mechanism
        test_error: list[str | None] = [None]
        test_done = threading.Event()

        def _execute():
            try:
                self._execute_test_body(
                    instance, method_name, display_name, param_info, suite_name, result
                )
            except Exception as exc:
                test_error[0] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            finally:
                test_done.set()

        worker = threading.Thread(target=_execute, daemon=True)
        worker.start()

        # Wait for test to complete or timeout
        if not test_done.wait(timeout=timeout_seconds):
            # Test timed out
            result.passed = False
            result.error = (
                f"TIMEOUT: Test '{display_name}' exceeded {timeout_seconds}s timeout.\n"
                f"  What went wrong: The test did not finish within the allowed time.\n"
                f"  Expected: Test completes within {timeout_seconds}s.\n"
                f"  What happened: Test is still running after "
                f"{timeout_seconds}s (possible infinite loop or agent hang).\n"
                f"  Suggested fix: Increase 'timeout_seconds' in "
                f"config, optimize the agent, or check for infinite loops."
            )

        # If the test itself recorded an error (not timeout)
        if test_error[0] is not None and result.error is None:
            result.passed = False
            result.error = test_error[0]

        result.duration_ms = (time.time() - test_start) * 1000
        return result

    def _execute_test_body(
        self,
        instance: AgentTest,
        method_name: str,
        display_name: str,
        param_info: dict | None,
        suite_name: str,
        result: TestResult,
    ) -> None:
        """Execute the actual test logic (called inside a thread)."""
        try:
            # Tell expect() which test instance to register with
            _set_active_test(instance)
            instance._expectations = []

            # Pass runner-level config to the test instance
            bench_config = self._config.get("bench_config")
            if bench_config and not instance.config:
                instance.config = bench_config

            # setup hook (before each test)
            setup = getattr(instance, "setup", None)
            if setup and callable(setup):
                if param_info:
                    import inspect as _inspect

                    sig = _inspect.signature(setup)
                    if param_info["arg_name"] in sig.parameters:
                        setup(**{param_info["arg_name"]: param_info["value"]})
                    else:
                        setup()
                else:
                    setup()

            # Execute the test method
            if param_info and param_info.get("property_test"):
                # ── Property-based test ──
                prop = getattr(type(instance), method_name, None)
                if prop is None:
                    prop = getattr(instance.__class__, method_name, None)
                if isinstance(prop, Property):
                    prop_results = prop.check(instance=instance)
                    for pr in prop_results:
                        if pr.passed:
                            result.assertions.append(
                                AssertionResult(
                                    passed=True,
                                    message=(f"Property passed for input: {pr.input_value!r}"),
                                    assertion_type="property_test",
                                )
                            )
                        else:
                            msg = f"Property failed for input: {pr.input_value!r}"
                            if pr.error:
                                msg += f"\n  Error: {pr.error}"
                            if pr.was_shrunk:
                                msg += f"\n  Shrunk to: {pr.shrink_result.minimal!r}"
                            result.assertions.append(
                                AssertionResult(
                                    passed=False,
                                    message=msg,
                                    assertion_type="property_test",
                                )
                            )
                    if result.assertions:
                        result.passed = all(a.passed for a in result.assertions)
                    else:
                        result.passed = True
                return  # Skip trajectory / expect collection

            elif param_info and param_info.get("adversarial_variant"):
                # ── Adversarial variant ──
                instance.run(param_info["variant_prompt"])

            else:
                # ── Normal test method ──
                method = getattr(instance, method_name)
                if param_info:
                    method(**{param_info["arg_name"]: param_info["value"]})
                else:
                    method()

            # Validate trajectory data
            if instance.trajectory is not None:
                self._validate_trajectory(instance.trajectory, display_name, result)

            # Collect trajectory
            if instance.trajectory:
                result.trajectory = instance.trajectory
                result.trajectory.test_name = f"{suite_name}.{display_name}"
                result.trajectory.agent_name = instance.agent or suite_name

            # Collect assertion results
            _active_expectations = getattr(instance, "_expectations", [])
            instance._expectations = []

            for exp in _active_expectations:
                result.assertions.extend(exp.results)

            # Determine pass/fail
            if result.assertions:
                result.passed = all(a.passed for a in result.assertions)
            else:
                result.passed = True

        except AssertionError as e:
            result.passed = False
            result.error = (
                f"AssertionError in '{display_name}': {e}\n"
                f"  What went wrong: An explicit assertion in the test method failed.\n"
                f"  Suggested fix: Review the test logic and agent output to ensure they match."
            )
        except Exception as e:
            result.passed = False
            exc_type = type(e).__name__
            exc_msg = str(e)
            result.error = (
                f"AGENT CRASH in '{display_name}': {exc_type}: {exc_msg}\n"
                f"  What went wrong: The agent or test raised an unhandled exception.\n"
                f"  Expected: Test should complete without exceptions.\n"
                f"  What happened: {exc_type} was raised: {exc_msg}\n"
                f"  Suggested fix: Check the agent adapter configuration "
                f"and ensure the agent function handles edge cases."
            )
            traceback.print_exc()
        finally:
            # teardown hook (after each test) — always run
            teardown = getattr(instance, "teardown", None)
            if teardown and callable(teardown):
                try:
                    teardown()
                except Exception as e:
                    import logging

                    logging.warning("Error in teardown for %s: %s", display_name, e)

            _clear_active_test()

    def _run_multi_agent_suite(
        self,
        suite_class: type,
        suite_result: TestSuiteResult,
        suite_start: float,
    ) -> TestSuiteResult:
        """Run a MultiAgentTest suite with simplified execution.

        MultiAgentTest doesn't share the setup/teardown/expect infrastructure
        of AgentTest, so we discover test methods, create fresh instances, and
        treat assertion failures as test failures.
        """
        test_methods = [
            name
            for name in dir(suite_class)
            if name.startswith("test_") and callable(getattr(suite_class, name, None))
        ]

        for method_name in test_methods:
            instance = suite_class()
            result = TestResult(
                test_name=method_name,
                suite_name=suite_result.suite_name,
            )
            test_start = time.time()
            try:
                method = getattr(instance, method_name)
                method()
                result.passed = True
            except AssertionError as e:
                result.passed = False
                result.error = str(e)
            except Exception as e:
                result.passed = False
                result.error = f"{type(e).__name__}: {e}"
            result.duration_ms = (time.time() - test_start) * 1000
            suite_result.results.append(result)

        suite_result.total_duration_ms = (time.time() - suite_start) * 1000
        return suite_result

    @staticmethod
    def _extract_adversarial_prompt(method: Any) -> str | None:
        """Try to extract a prompt string from a test method's source."""
        import re as _re

        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):
            return None

        # Look for self.run("...") calls
        match = _re.search(r'self\.run\(\s*["\'](.+?)["\']', source)
        if match:
            return match.group(1)

        # Look for any string constant
        match = _re.search(r'["\']([a-zA-Z][^"\']{10,})["\']', source)
        if match:
            return match.group(1)

        return None

    def _validate_trajectory(
        self, trajectory: AgentTrajectory, test_name: str, result: TestResult
    ) -> None:
        """Validate trajectory data and add warnings for malformed/edge-case data."""
        # Check for empty trajectory
        if trajectory.step_count == 0 and not trajectory.final_response:
            # Don't fail — just note it as a warning in assertions
            pass

        # Check for None/empty final response
        if trajectory.completed and not trajectory.final_response:
            from agentbench.core.assertions import AssertionResult

            result.assertions.append(
                AssertionResult(
                    passed=True,  # Not a failure, but informational
                    message=(
                        "Agent completed but returned empty final response.\n"
                        "  What happened: Agent marked as completed but final_response is empty.\n"
                        "  Suggested fix: Ensure the agent returns a meaningful final response."
                    ),
                    assertion_type="trajectory_validation",
                )
            )

        # Check for infinite loop detection (max steps)
        max_steps = self._max_steps
        if trajectory.step_count > max_steps:
            from agentbench.core.assertions import AssertionResult

            result.assertions.append(
                AssertionResult(
                    passed=False,
                    message=(
                        f"MAX STEPS EXCEEDED: Agent used "
                        f"{trajectory.step_count} steps "
                        f"(limit: {max_steps}).\n"
                        f"  What went wrong: The agent may be in an infinite loop.\n"
                        f"  Expected: Agent should complete within {max_steps} steps.\n"
                        f"  What happened: Agent reached the step limit — "
                        f"possible infinite loop.\n"
                        f"  Suggested fix: Increase max_steps in config or "
                        f"optimize the agent's decision loop."
                    ),
                    assertion_type="max_steps",
                    details={"steps": trajectory.step_count, "max_steps": max_steps},
                )
            )

        # Check for malformed steps
        for i, step in enumerate(trajectory.steps):
            if step.action not in ("tool_call", "llm_response", "error", "retry"):
                from agentbench.core.assertions import AssertionResult

                result.assertions.append(
                    AssertionResult(
                        passed=False,
                        message=(
                            f"MALFORMED TRAJECTORY: Step {i} has unknown action '{step.action}'.\n"
                            f"  What went wrong: Step data contains an unrecognized action type.\n"
                            f"  Expected: Action should be one of: "
                            f"tool_call, llm_response, error, retry.\n"
                            f"  What happened: Got '{step.action}'.\n"
                            f"  Suggested fix: Check the agent adapter to "
                            f"ensure it records valid action types."
                        ),
                        assertion_type="trajectory_validation",
                    )
                )

    def _run_test(self, instance: AgentTest, method_name: str, suite_name: str) -> TestResult:
        """Run a single test method and collect results (legacy, non-parametric).

        Kept for backwards compatibility; the new path is _run_single_test.
        """
        return self._run_single_test(instance, method_name, method_name, None, suite_name)

    def run(self, path: Path | str) -> RunResult:
        """Discover and run all test suites in the given path."""
        run_start = time.time()

        # Reset fixture registry for a fresh session
        FixtureRegistry.reset()
        self._fixture_registry = FixtureRegistry.get()

        suites = self.discover_suites(path)

        if not suites:
            print(f"No test suites found in {path}")
            return RunResult()

        run_result = RunResult()

        if self._parallel > 1 and len(suites) > 1:
            # Run suites in parallel using threads
            with ThreadPoolExecutor(max_workers=self._parallel) as executor:
                futures = {executor.submit(self.run_suite, s): s for s in suites}
                for future in as_completed(futures):
                    suite_result = future.result()
                    run_result.suite_results.append(suite_result)
            # Sort by original discovery order for deterministic output
            suite_order = {s: i for i, s in enumerate(suites)}
            run_result.suite_results.sort(
                key=lambda r: suite_order.get(
                    next((s for s in suites if s.__name__ == r.suite_name), suites[0]), 0
                )
            )
        else:
            for suite_class in suites:
                suite_result = self.run_suite(suite_class)
                run_result.suite_results.append(suite_result)

        # Teardown session-scoped fixtures
        self._fixture_registry.teardown_all()

        run_result.total_duration_ms = (time.time() - run_start) * 1000
        return run_result
