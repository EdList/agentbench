"""Test runner — discovers, executes, and reports on agent test suites."""

from __future__ import annotations

import importlib
import inspect
import time
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agentbench.core.test import AgentTest, AgentTrajectory
from agentbench.core.assertions import Expectation, AssertionResult


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
    """Discovers and executes agent test suites."""

    def __init__(self, config: dict[str, Any] | None = None):
        self._config = config or {}

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
            print(f"Warning: Could not load {path}: {e}")
        return suites

    def run_suite(self, suite_class: type[AgentTest]) -> TestSuiteResult:
        """Run all test methods in a suite."""
        suite_name = suite_class.__name__
        suite_result = TestSuiteResult(suite_name=suite_name)
        suite_start = time.time()

        instance = suite_class()

        # Find all test methods
        test_methods = [
            name
            for name, method in inspect.getmembers(instance, predicate=inspect.ismethod)
            if name.startswith("test_")
        ]

        for method_name in test_methods:
            result = self._run_test(instance, method_name, suite_name)
            suite_result.results.append(result)

        suite_result.total_duration_ms = (time.time() - suite_start) * 1000
        return suite_result

    def _run_test(self, instance: AgentTest, method_name: str, suite_name: str) -> TestResult:
        """Run a single test method and collect results."""
        test_start = time.time()
        result = TestResult(test_name=method_name, suite_name=suite_name)

        try:
            method = getattr(instance, method_name)
            method()

            # Collect assertions from any expectations that were created
            if instance.trajectory:
                result.trajectory = instance.trajectory

            # If the test didn't raise, check if assertions were made
            # Assertions are collected by the expect() chains
            result.passed = True

        except AssertionError as e:
            result.passed = False
            result.error = str(e)
        except Exception as e:
            result.passed = False
            result.error = f"{type(e).__name__}: {e}"
            traceback.print_exc()

        result.duration_ms = (time.time() - test_start) * 1000
        return result

    def run(self, path: Path | str) -> RunResult:
        """Discover and run all test suites in the given path."""
        run_start = time.time()
        suites = self.discover_suites(path)

        if not suites:
            print(f"No test suites found in {path}")
            return RunResult()

        run_result = RunResult()

        for suite_class in suites:
            suite_result = self.run_suite(suite_class)
            run_result.suite_results.append(suite_result)

        run_result.total_duration_ms = (time.time() - run_start) * 1000
        return run_result
