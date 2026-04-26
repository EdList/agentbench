"""Assertion API — fluent expectations for agent trajectories."""

from __future__ import annotations

import re
import threading
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

    def __init__(
        self,
        step_index: int,
        trajectory: AgentTrajectory,
        collector: list[AssertionResult] | None = None,
    ):
        self._step_index = step_index
        self._trajectory = trajectory
        self._step = trajectory.steps[step_index]
        self._results: list[AssertionResult] = []
        self._collector = collector

    @property
    def results(self) -> list[AssertionResult]:
        return self._results

    def _store_result(self, result: AssertionResult) -> None:
        self._results.append(result)
        if self._collector is not None:
            self._collector.append(result)

    def used_tool(self, name: str) -> StepAssertion:
        """Assert this step called a specific tool."""
        actual_action = self._step.action
        actual_tool = self._step.tool_name
        passed = actual_action == "tool_call" and actual_tool == name

        if passed:
            message = f"Step {self._step_index}: called tool '{name}'"
        else:
            message = (
                f"Step {self._step_index}: expected tool call to '{name}', but did not find it.\n"
                f"  What went wrong: Step action or tool name does not match.\n"
                f"  Expected: action='tool_call', tool_name='{name}'.\n"
                f"  What happened: action='{actual_action}', tool_name='{actual_tool}'.\n"
                f"  Suggested fix: Ensure the agent calls tool "
                f"'{name}' at this step, or check step index."
            )

        self._store_result(
            AssertionResult(
                passed=passed,
                message=message,
                assertion_type="tool_call",
                details={"step": self._step_index, "tool": name},
            )
        )
        return self

    def responded_with(self, text: str) -> StepAssertion:
        """Assert this step's response contains the given text."""
        response = self._step.response or ""
        passed = text.lower() in response.lower()

        if passed:
            message = f"Step {self._step_index}: response contains '{text}'"
        else:
            message = (
                f"Step {self._step_index}: response does not contain '{text}'.\n"
                f"  What went wrong: Expected text was not found in the step response.\n"
                f"  Expected: Response containing '{text}'.\n"
                f"  What happened: Response was: "
                f'"{response[:200]}{"..." if len(response) > 200 else ""}".\n'
                f"  Suggested fix: Check the agent's output at this step, "
                f"or adjust the expected text."
            )

        self._store_result(
            AssertionResult(
                passed=passed,
                message=message,
                assertion_type="response_contains",
                details={"step": self._step_index, "expected": text},
            )
        )
        return self

    def has_no_error(self) -> StepAssertion:
        """Assert this step has no error."""
        error = self._step.error
        passed = error is None

        if passed:
            message = f"Step {self._step_index}: no error"
        else:
            message = (
                f"Step {self._step_index}: has error: '{error}'.\n"
                f"  What went wrong: This step recorded an error.\n"
                f"  Expected: No error (error should be None).\n"
                f"  What happened: Error = '{error}'.\n"
                f"  Suggested fix: Investigate why this step failed — "
                f"check agent logic, tool availability, or network issues."
            )

        self._store_result(
            AssertionResult(
                passed=passed,
                message=message,
                assertion_type="no_error",
                details={"step": self._step_index, "error": error},
            )
        )
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
        self._results.append(
            AssertionResult(
                passed=passed,
                message=message,
                assertion_type=assertion_type,
                details=details,
            )
        )

    # --- Negation ---

    @property
    def to_not(self) -> Expectation:
        """Negate the next assertion."""
        self._negated = True
        return self

    def to_not_expose(self, pattern: str) -> Expectation:
        """Assert the agent never exposed a pattern in any step."""
        if self._negated:
            raise ValueError(
                "Cannot use to_not with to_not_expose. Use expect(t).to_expose(...) instead."
            )
        found_in = []
        for step in self._trajectory.steps:
            data = step.exposed_data
            if pattern.lower() in data.lower():
                found_in.append(step.step_number)

        passed = len(found_in) == 0
        if passed:
            message = f"Agent did not expose '{pattern}'"
        else:
            message = (
                f"Agent exposed sensitive pattern '{pattern}' in steps {found_in}.\n"
                f"  What went wrong: The pattern was found in agent output data.\n"
                f"  Expected: Pattern '{pattern}' should never appear in any step.\n"
                f"  What happened: Found in steps {found_in}.\n"
                f"  Suggested fix: Add PII redaction or filtering to prevent exposing '{pattern}'."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="no_expose",
            pattern=pattern,
            found_in_steps=found_in,
        )
        return self

    # --- Completion ---

    def to_complete(self) -> Expectation:
        """Assert the agent completed its run without error."""
        completed = self._trajectory.completed
        error = self._trajectory.error
        passed = completed and error is None

        if passed:
            message = "Agent completed successfully"
        else:
            message = (
                f"Agent did not complete successfully.\n"
                f"  What went wrong: Agent completion check failed.\n"
                f"  Expected: completed=True, error=None.\n"
                f"  What happened: completed={completed}, error={repr(error)}.\n"
                f"  Suggested fix: Ensure the agent finishes its task without raising errors."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="completion",
        )
        return self

    def to_complete_within(self, steps: int) -> Expectation:
        """Assert the agent completed within N steps."""
        actual = self._trajectory.step_count
        completed = self._trajectory.completed and self._trajectory.error is None
        passed = completed and actual <= steps

        if passed:
            msg = f"Agent completed in {actual} steps (limit: {steps})"
        elif not completed:
            msg = (
                f"Agent did not complete within step limit.\n"
                f"  What went wrong: Agent did not finish — it may have crashed or hung.\n"
                f"  Expected: completed=True within {steps} steps.\n"
                f"  What happened: completed="
                f"{self._trajectory.completed}, {actual} steps taken, "
                f"error={repr(self._trajectory.error)}.\n"
                f"  Suggested fix: Check agent error logs, "
                f"increase step limit, or debug agent logic."
            )
        else:
            msg = (
                f"Agent completed but exceeded step limit.\n"
                f"  What went wrong: Too many steps were needed.\n"
                f"  Expected: ≤ {steps} steps.\n"
                f"  What happened: {actual} steps were used.\n"
                f"  Suggested fix: Optimize the agent to use fewer "
                f"steps, or increase the step limit."
            )

        self._add_result(
            passed=passed,
            message=msg,
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
            if passed:
                message = f"Agent called '{name}' {call_count} time(s) (expected: {times})"
            else:
                message = (
                    f"Agent called '{name}' {call_count} time(s), expected exactly {times}.\n"
                    f"  What went wrong: Tool call count does not match expected.\n"
                    f"  Expected: {times} call(s) to '{name}'.\n"
                    f"  What happened: {call_count} call(s) to '{name}'.\n"
                    f"  Suggested fix: Review the agent logic to call "
                    f"'{name}' exactly {times} time(s), or adjust "
                    f"the expected count."
                )
            self._add_result(
                passed=passed,
                message=message,
                assertion_type="tool_count",
                tool=name,
                actual=call_count,
                expected=times,
            )
        else:
            passed = call_count > 0
            if passed:
                message = f"Agent called tool '{name}' ({call_count} times)"
            else:
                message = (
                    f"Agent never called tool '{name}'.\n"
                    f"  What went wrong: Expected at least one call to '{name}', but got zero.\n"
                    f"  Expected: ≥ 1 call to '{name}'.\n"
                    f"  What happened: 0 calls to '{name}'.\n"
                    f"  Suggested fix: Ensure the agent has access to "
                    f"'{name}' and the test scenario requires its use."
                )
            self._add_result(
                passed=passed,
                message=message,
                assertion_type="tool_used",
                tool=name,
                call_count=call_count,
            )
        return self

    def to_not_use_tool(self, name: str) -> Expectation:
        """Assert the agent never used a specific tool."""
        if self._negated:
            raise ValueError(
                "Cannot use to_not with to_not_use_tool. Use expect(t).to_use_tool(...) instead."
            )
        calls = self._trajectory.tool_calls_by_name(name)
        passed = len(calls) == 0

        if passed:
            message = f"Agent did not call tool '{name}'"
        else:
            message = (
                f"Agent called forbidden tool '{name}' {len(calls)} time(s).\n"
                f"  What went wrong: Tool '{name}' was called but should not have been.\n"
                f"  Expected: 0 calls to '{name}'.\n"
                f"  What happened: {len(calls)} call(s) to '{name}'.\n"
                f"  Suggested fix: Remove '{name}' from agent's "
                f"available tools or adjust agent logic to avoid it."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="tool_not_used",
            tool=name,
            call_count=len(calls),
        )
        return self

    # --- Response ---

    def to_respond_with(self, text: str) -> Expectation:
        """Assert the agent's final response contains the given text."""
        response = self._trajectory.final_response or ""
        passed = text.lower() in response.lower()

        if passed:
            message = f"Agent response contains '{text}'"
        else:
            preview = response[:200] + ("..." if len(response) > 200 else "")
            message = (
                f"Agent response does not contain '{text}'.\n"
                f"  What went wrong: Expected text not found in final response.\n"
                f"  Expected: Response containing '{text}'.\n"
                f'  What happened: Response was: "{preview}".\n'
                f"  Suggested fix: Check agent output or adjust the expected text pattern."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="response_contains",
            expected=text,
            actual_response=response[:200],
        )
        return self

    def to_refuse(self) -> Expectation:
        """Assert the final response is a refusal rather than a substantive answer."""
        response = (self._trajectory.final_response or "").strip()
        refusal_patterns = [
            re.compile(r"\bsorry\b", re.IGNORECASE),
            re.compile(r"\bcannot\b", re.IGNORECASE),
            re.compile(r"\bcan'?t\b", re.IGNORECASE),
            re.compile(r"\bunable\b", re.IGNORECASE),
            re.compile(r"\bwon'?t\b", re.IGNORECASE),
            re.compile(r"\bdo not have the ability\b", re.IGNORECASE),
        ]
        passed = any(pattern.search(response) for pattern in refusal_patterns)

        if passed:
            message = "Agent refused the request"
        else:
            preview = response[:200] + ("..." if len(response) > 200 else "")
            message = (
                "Agent did not refuse the request.\n"
                "  What went wrong: Expected a refusal-style response but "
                "the final answer looked substantive.\n"
                f'  What happened: Response was: "{preview}".\n'
                "  Suggested fix: Add or strengthen refusal/guardrail "
                "instructions for unsafe requests."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="refusal",
            actual_response=response[:200],
        )
        return self

    # --- Behavior ---

    def to_retry(self, *, max_attempts: int) -> Expectation:
        """Assert the agent retried after failure, within max attempts."""
        retries = [s for s in self._trajectory.steps if s.action == "retry"]
        retry_count = len(retries)
        passed = self._trajectory.completed and retry_count > 0 and retry_count <= max_attempts

        if passed:
            message = f"Agent retried {retry_count} time(s) (max: {max_attempts}) and completed"
        else:
            retry_reason = (
                "Agent did not complete after retries."
                if not self._trajectory.completed
                else "Agent exceeded max retry attempts."
                if retry_count > max_attempts
                else "Agent never retried."
            )
            message = (
                f"Agent retry check failed.\n"
                f"  What went wrong: {retry_reason}\n"
                f"  Expected: ≤ {max_attempts} retries and agent completes.\n"
                f"  What happened: {retry_count} retries, completed={self._trajectory.completed}.\n"
                f"  Suggested fix: Improve agent's error handling "
                f"to reduce retries, or increase max_attempts."
            )

        self._add_result(
            passed=passed,
            message=message,
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
            message = (
                f"Agent did not follow expected workflow.\n"
                f"  What went wrong: {e}\n"
                f"  Expected workflow: {' → '.join(steps)}\n"
                f"  What happened: Tool call sequence was: "
                f"{' → '.join(actual_tools) if actual_tools else '(none)'}\n"
                f"  Suggested fix: Ensure the agent calls tools in the required order."
            )

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

        if passed:
            message = "Agent had no errors"
        else:
            step_nums = [s.step_number for s in error_steps]
            errors = [(s.step_number, s.error) for s in error_steps[:5]]
            error_detail = "; ".join(f"step {n}: {e}" for n, e in errors)
            message = (
                f"Agent had {len(error_steps)} error(s) in steps {step_nums}.\n"
                f"  What went wrong: One or more steps recorded errors.\n"
                f"  Expected: No errors (all steps should have error=None).\n"
                f"  What happened: {error_detail}\n"
                f"  Suggested fix: Investigate each error — check tool "
                f"availability, network connectivity, or agent logic."
            )

        self._add_result(
            passed=passed,
            message=message,
            assertion_type="no_errors",
            error_count=len(error_steps),
        )
        return self

    def step(self, index: int) -> StepAssertion:
        """Get assertions for a specific step."""
        if index < 0 or index >= len(self._trajectory.steps):
            raise IndexError(
                f"Step index {index} is out of range.\n"
                f"  What went wrong: Requested step {index}, but "
                f"trajectory has {self._trajectory.step_count} steps "
                f"(indices 0-{self._trajectory.step_count - 1}).\n"
                f"  Suggested fix: Use a valid step index "
                f"(0 to {max(0, self._trajectory.step_count - 1)})."
            )
        return StepAssertion(index, self._trajectory, collector=self._results)


# Thread-local storage for tracking which AgentTest instance is active.
# This lets expect() register itself with the running test so the runner
# can collect assertion results.

_active_test: threading.local = threading.local()


def _set_active_test(test_instance: Any) -> None:
    """Set the currently running AgentTest instance (called by the runner)."""
    _active_test.instance = test_instance
    # Initialize per-test expectations list
    if not hasattr(test_instance, "_expectations"):
        test_instance._expectations = []


def _clear_active_test() -> None:
    """Clear the active test reference."""
    _active_test.instance = None


def expect(trajectory: AgentTrajectory) -> Expectation:
    """Create an expectation chain for asserting on an agent trajectory.

    Usage:
        result = test.run("Buy me a shirt")
        expect(result).to_complete_within(steps=10)
        expect(result).to_use_tool("payment_api", times=1)
        expect(result).to_not_expose("credit_card_number")
    """
    # Validate trajectory is not None
    if trajectory is None:
        raise ValueError(
            "expect() received None instead of a trajectory.\n"
            "  What went wrong: The agent did not return a trajectory "
            "(likely crashed or was not configured).\n"
            "  Expected: A valid AgentTrajectory object.\n"
            "  What happened: Got None.\n"
            "  Suggested fix: Ensure self.run() returns a trajectory — check adapter configuration."
        )

    # Validate trajectory has steps list
    if not hasattr(trajectory, "steps"):
        raise ValueError(
            f"expect() received an invalid object of type {type(trajectory).__name__}.\n"
            "  Expected: An AgentTrajectory object.\n"
            "  Suggested fix: Pass the return value of test.run() to expect()."
        )

    exp = Expectation(trajectory)
    # Register this expectation with the active test (if any) so the
    # runner can collect assertion results after the test method returns.
    test_instance = getattr(_active_test, "instance", None)
    if test_instance is not None and hasattr(test_instance, "_expectations"):
        test_instance._expectations.append(exp)
    return exp
