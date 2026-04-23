"""Test runner — discovers, executes, and reports on agent test suites."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbench.core.test import AgentTest, AgentTrajectory
from agentbench.core.assertions import Expectation, AssertionResult, _set_active_test, _clear_active_test


@dataclass
class TestResult:
    """Result of a single test method."""

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

    def discover_suites(self, path: Path | str) -> list[type[AgentTest]]:
        """Discover AgentTest subclasses in the given path."""
        path = Path(path)
        suites: list[type[AgentTest]] = []

        if path.is_file() and path.suffix == ".py":
            suites.extend(self._find_suites_in_file(path))
        elif path.is_dir():
            for py_file in sorted(path.rglob("test_*.py")):
                suites.extend(self._find_suites_in_file(py_file))

        return suites

    def _find_suites_in_file(self, path: Path) -> list[type[AgentTest]]:
        """Find AgentTest subclasses in a Python file."""
        suites = []
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                for name, obj in inspect.getmembers(module, inspect.isclass):
                    if issubclass(obj, AgentTest) and obj is not AgentTest:
                        suites.append(obj)
        except Exception as e:
            import logging
            logging.warning("Could not load %s: %s", path, e)
        return suites

    def _discover_test_methods(self, suite_class: type[AgentTest]) -> list[tuple[str, dict | None]]:
        """Discover test methods and their parametrize metadata.

        Returns:
            List of (display_name, param_info) where param_info is None
            for a plain test or a dict with 'arg_name' and 'value' for
            a parametrized iteration.
        """
        temp_instance = suite_class()
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
                    expanded.append((method_name, display_name, {"arg_name": arg_name, "value": value}))
            else:
                expanded.append((method_name, method_name, None))

        return expanded

    def run_suite(self, suite_class: type[AgentTest]) -> TestSuiteResult:
        """Run all test methods in a suite."""
        suite_name = suite_class.__name__
        suite_result = TestSuiteResult(suite_name=suite_name)
        suite_start = time.time()

        # Discover test methods (with parametrize expansion)
        test_items = self._discover_test_methods(suite_class)

        if self._parallel > 1:
            # Parallel execution of individual tests within the suite
            # Create a shared class-level instance for setup_class / teardown_class
            class_instance = suite_class()

            # setup_class hook
            self._run_class_hook(class_instance, "setup_class")

            try:
                with ThreadPoolExecutor(max_workers=self._parallel) as executor:
                    futures = {}
                    for method_name, display_name, param_info in test_items:
                        instance = suite_class()
                        future = executor.submit(
                            self._run_single_test, instance, method_name, display_name, param_info, suite_name
                        )
                        futures[future] = display_name

                    for future in as_completed(futures):
                        result = future.result()
                        suite_result.results.append(result)
            finally:
                # teardown_class hook
                self._run_class_hook(class_instance, "teardown_class")
        else:
            # Sequential execution
            class_instance = suite_class()

            # setup_class hook
            self._run_class_hook(class_instance, "setup_class")

            try:
                for method_name, display_name, param_info in test_items:
                    # Create a fresh instance for each test to prevent state leakage
                    instance = suite_class()
                    result = self._run_single_test(instance, method_name, display_name, param_info, suite_name)
                    suite_result.results.append(result)
            finally:
                # teardown_class hook
                self._run_class_hook(class_instance, "teardown_class")

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
            method = getattr(instance, method_name)
            if param_info:
                method(**{param_info["arg_name"]: param_info["value"]})
            else:
                method()

            # Collect trajectory
            if instance.trajectory:
                result.trajectory = instance.trajectory
                result.trajectory.test_name = f"{suite_name}.{display_name}"
                result.trajectory.agent_name = instance.agent or suite_name

            # Collect assertion results
            _active_expectations = getattr(instance, '_expectations', [])
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
            result.error = str(e)
        except Exception as e:
            result.passed = False
            result.error = f"{type(e).__name__}: {e}"
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

        result.duration_ms = (time.time() - test_start) * 1000
        return result

    def _run_test(self, instance: AgentTest, method_name: str, suite_name: str) -> TestResult:
        """Run a single test method and collect results (legacy, non-parametric).

        Kept for backwards compatibility; the new path is _run_single_test.
        """
        return self._run_single_test(instance, method_name, method_name, None, suite_name)

    def run(self, path: Path | str) -> RunResult:
        """Discover and run all test suites in the given path."""
        run_start = time.time()
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
                    next((s for s in suites if s.__name__ == r.suite_name), suites[0]),
                    0
                )
            )
        else:
            for suite_class in suites:
                suite_result = self.run_suite(suite_class)
                run_result.suite_results.append(suite_result)

        run_result.total_duration_ms = (time.time() - run_start) * 1000
        return run_result
