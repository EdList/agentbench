"""Assertion API — fluent expectations for agent trajectories."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from agentbench.core.test import AgentTrajectory


@dataclass
class AssertionResult:
    """Result of a single assertion."""

    passed: bool
    message: str
    assertion_type: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        return f"{icon} {self.message}"

    def __bool__(self) -> bool:
        return self.passed


class StepAssertion:
    """Assertions about a single agent step."""

    def __init__(self, step_index: int, trajectory: AgentTrajectory):
        self._step_index = step_index
        self._trajectory = trajectory
        self._step = trajectory.steps[step_index]
        self._results: list[AssertionResult] = []

    @property
    def results(self) -> list[AssertionResult]:
        return self._results

    def used_tool(self, name: str) -> StepAssertion:
        """Assert this step called a specific tool."""
        passed = (
            self._step.action == "tool_call"
            and self._step.tool_name == name
        )
        self._results.append(AssertionResult(
            passed=passed,
            message=f"Step {self._step_index}: {'called' if passed else 'did not call'} tool '{name}'",
            assertion_type="tool_call",
            details={"step": self._step_index, "tool": name},
        ))
        return self

    def responded_with(self, text: str) -> StepAssertion:
        """Assert this step's response contains the given text."""
        response = self._step.response or ""
        passed = text.lower() in response.lower()
        self._results.append(AssertionResult(
            passed=passed,
            message=f"Step {self._step_index}: response {'contains' if passed else 'does not contain'} '{text}'",
            assertion_type="response_contains",
            details={"step": self._step_index, "expected": text},
        ))
        return self

    def has_no_error(self) -> StepAssertion:
        """Assert this step has no error."""
        passed = self._step.error is None
        self._results.append(AssertionResult(
            passed=passed,
            message=f"Step {self._step_index}: {'no error' if passed else f'error: {self._step.error}'}",
            assertion_type="no_error",
            details={"step": self._step_index, "error": self._step.error},
        ))
        return self


class Expectation:
    """Fluent assertion builder for agent trajectories.

    Usage:
        trajectory = test.run("Buy me a shirt")
        expect(trajectory).to_complete_within(steps=10)
        expect(trajectory).to_use_tool("payment_api", times=1)
        expect(trajectory).to_not_expose("credit_card")
    """

    def __init__(self, trajectory: AgentTrajectory):
        self._trajectory = trajectory
        self._results: list[AssertionResult] = []
        self._negated = False

    @property
    def results(self) -> list[AssertionResult]:
        return self._results

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self._results)

    def _add_result(self, passed: bool, message: str, assertion_type: str, **details: Any) -> None:
        # Apply negation
        if self._negated:
            passed = not passed
            message = f"NOT: {message}"
            self._negated = False
        self._results.append(AssertionResult(
            passed=passed,
            message=message,
            assertion_type=assertion_type,
            details=details,
        ))

    # --- Negation ---

    @property
    def to_not(self) -> Expectation:
        """Negate the next assertion."""
        self._negated = True
        return self

    def to_not_expose(self, pattern: str) -> Expectation:
        """Assert the agent never exposed a pattern in any step."""
        found_in = []
        for step in self._trajectory.steps:
            data = step.exposed_data
            if pattern.lower() in data.lower():
                found_in.append(step.step_number)

        passed = len(found_in) == 0
        self._add_result(
            passed=passed,
            message=f"Agent {'did not expose' if passed else 'exposed'} '{pattern}'"
            + (f" in steps {found_in}" if found_in else ""),
            assertion_type="no_expose",
            pattern=pattern,
            found_in_steps=found_in,
        )
        return self

    # --- Completion ---

    def to_complete(self) -> Expectation:
        """Assert the agent completed its run without error."""
        passed = self._trajectory.completed and self._trajectory.error is None
        self._add_result(
            passed=passed,
            message=f"Agent {'completed' if passed else 'did not complete'} successfully"
            + (f" (error: {self._trajectory.error})" if self._trajectory.error else ""),
            assertion_type="completion",
        )
        return self

    def to_complete_within(self, steps: int) -> Expectation:
        """Assert the agent completed within N steps."""
        actual = self._trajectory.step_count
        passed = self._trajectory.completed and actual <= steps
        self._add_result(
            passed=passed,
            message=f"Agent completed in {actual} steps (limit: {steps})",
            assertion_type="step_limit",
            actual_steps=actual,
            max_steps=steps,
        )
        return self

    # --- Tool Usage ---

    def to_use_tool(self, name: str, *, times: int | None = None) -> Expectation:
        """Assert the agent used a specific tool, optionally exact number of times."""
        calls = self._trajectory.tool_calls_by_name(name)
        call_count = len(calls)

        if times is not None:
            passed = call_count == times
            self._add_result(
                passed=passed,
                message=f"Agent called '{name}' {call_count} time(s) (expected: {times})",
                assertion_type="tool_count",
                tool=name,
                actual=call_count,
                expected=times,
            )
        else:
            passed = call_count > 0
            self._add_result(
                passed=passed,
                message=f"Agent {'called' if passed else 'did not call'} tool '{name}' ({call_count} times)",
                assertion_type="tool_used",
                tool=name,
                call_count=call_count,
            )
        return self

    def to_not_use_tool(self, name: str) -> Expectation:
        """Assert the agent never used a specific tool."""
        calls = self._trajectory.tool_calls_by_name(name)
        passed = len(calls) == 0
        self._add_result(
            passed=passed,
            message=f"Agent {'did not call' if passed else 'called'} tool '{name}'"
            + (f" ({len(calls)} times)" if calls else ""),
            assertion_type="tool_not_used",
            tool=name,
            call_count=len(calls),
        )
        return self

    # --- Response ---

    def to_respond_with(self, text: str) -> Expectation:
        """Assert the agent's final response contains the given text."""
        response = self._trajectory.final_response
        passed = text.lower() in response.lower()
        self._add_result(
            passed=passed,
            message=f"Agent response {'contains' if passed else 'does not contain'} '{text}'",
            assertion_type="response_contains",
            expected=text,
        )
        return self

    # --- Behavior ---

    def to_retry(self, *, max_attempts: int) -> Expectation:
        """Assert the agent retried after failure, within max attempts."""
        retries = [s for s in self._trajectory.steps if s.action == "retry"]
        retry_count = len(retries)
        passed = self._trajectory.completed and retry_count <= max_attempts
        self._add_result(
            passed=passed,
            message=f"Agent retried {retry_count} time(s) (max: {max_attempts})"
            + (" and completed" if self._trajectory.completed else " but did not complete"),
            assertion_type="retry_limit",
            retries=retry_count,
            max_attempts=max_attempts,
        )
        return self

    def to_follow_workflow(self, steps: list[str]) -> Expectation:
        """Assert the agent followed a specific workflow (ordered tool calls)."""
        actual_tools = [s.tool_name for s in self._trajectory.tool_calls]
        try:
            idx = 0
            for expected in steps:
                while idx < len(actual_tools) and actual_tools[idx] != expected:
                    idx += 1
                if idx >= len(actual_tools):
                    raise ValueError(f"Expected tool '{expected}' not found after position {idx}")
                idx += 1
            passed = True
            message = f"Agent followed workflow: {' → '.join(steps)}"
        except ValueError as e:
            passed = False
            message = f"Agent did not follow workflow: {e}"

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="workflow",
            expected_steps=steps,
            actual_tools=actual_tools,
        )
        return self

    def to_have_no_errors(self) -> Expectation:
        """Assert no step had an error."""
        error_steps = [s for s in self._trajectory.steps if s.error is not None]
        passed = len(error_steps) == 0
        self._add_result(
            passed=passed,
            message=f"Agent had {len(error_steps)} error(s)"
            + (f" in steps {[s.step_number for s in error_steps]}" if error_steps else ""),
            assertion_type="no_errors",
            error_count=len(error_steps),
        )
        return self

    def step(self, index: int) -> StepAssertion:
        """Get assertions for a specific step."""
        if index >= len(self._trajectory.steps):
            raise IndexError(f"Step {index} out of range (trajectory has {self._trajectory.step_count} steps)")
        return StepAssertion(index, self._trajectory)


def expect(trajectory: AgentTrajectory) -> Expectation:
    """Create an expectation chain for asserting on an agent trajectory.

    Usage:
        result = test.run("Buy me a shirt")
        expect(result).to_complete_within(steps=10)
        expect(result).to_use_tool("payment_api", times=1)
        expect(result).to_not_expose("credit_card_number")
    """
    return Expectation(trajectory)
