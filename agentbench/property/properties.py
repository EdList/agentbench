"""Property definitions for agent behavioural testing.

Provides the :func:`property_test` decorator, the :class:`Property` wrapper,
and a library of ready-made properties (PII leakage, bounded steps, etc.).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from agentbench.property.generators import AgentInput, ToolCallGen
from agentbench.property.shrink import ShrinkResult, shrink

# ---------------------------------------------------------------------------
# Property wrapper
# ---------------------------------------------------------------------------

@dataclass
class PropertyResult:
    """Result of running a single property test."""

    passed: bool
    input_value: Any
    error: str | None = None
    shrink_result: ShrinkResult | None = None

    @property
    def was_shrunk(self) -> bool:
        return self.shrink_result is not None and self.shrink_result.was_shrunk

    def summary(self) -> str:
        status = "PASSED" if self.passed else "FAILED"
        lines = [f"Property Result: {status}", f"  Input: {self.input_value!r}"]
        if self.error:
            lines.append(f"  Error: {self.error}")
        if self.shrink_result:
            lines.append(f"  Shrink: {self.shrink_result.summary()}")
        return "\n".join(lines)


class Property:
    """Wraps a test function together with a generator and configuration.

    Usually created via the :func:`property_test` decorator rather than
    directly.
    """

    def __init__(
        self,
        fn: Callable,
        gen: Any,
        runs: int = 100,
        do_shrink: bool = True,
        max_shrinks: int = 1000,
    ) -> None:
        self.fn = fn
        self.gen = gen
        self.runs = runs
        self.do_shrink = do_shrink
        self.max_shrinks = max_shrinks
        self.__name__ = getattr(fn, "__name__", "property_test")
        self.__doc__ = getattr(fn, "__doc__", None)

    def check(self, instance: Any = None) -> list[PropertyResult]:
        """Run the property *self.runs* times and return results.

        Parameters
        ----------
        instance:
            Optional ``self`` for the test method (when used inside an
            :class:`~agentbench.core.test.AgentTest`).
        """
        results: list[PropertyResult] = []

        for _ in range(self.runs):
            value = self.gen.generate()
            try:
                if instance is not None:
                    self.fn(instance, value)
                else:
                    self.fn(value)
                results.append(PropertyResult(passed=True, input_value=value))
            except Exception as exc:
                pr = PropertyResult(
                    passed=False,
                    input_value=value,
                    error=f"{type(exc).__name__}: {exc}",
                )

                # Attempt shrinking
                if self.do_shrink and hasattr(self.gen, "shrink_value"):
                    def _still_fails(v: Any) -> bool:
                        try:
                            if instance is not None:
                                self.fn(instance, v)
                            else:
                                self.fn(v)
                            return False
                        except Exception:
                            return True

                    pr.shrink_result = shrink(
                        value,
                        predicate=_still_fails,
                        shrink_value=self.gen.shrink_value,
                        max_shrinks=self.max_shrinks,
                    )

                results.append(pr)
                # On first failure, stop early (property tests fail-fast)
                break

        return results

    def run(self, instance: Any = None) -> bool:
        """Convenience: returns ``True`` if all checks pass."""
        return all(r.passed for r in self.check(instance=instance))


# ---------------------------------------------------------------------------
# @property_test decorator
# ---------------------------------------------------------------------------

def property_test(
    *,
    gen: Any | None = None,
    runs: int = 100,
    shrink: bool = True,
    max_shrinks: int = 1000,
) -> Callable:
    """Decorator that turns a test method into a property-based test.

    Parameters
    ----------
    gen:
        A generator with a ``generate()`` method (e.g. :class:`AgentInput`).
    runs:
        Number of random inputs to try.
    shrink:
        Whether to attempt shrinking on failure.
    max_shrinks:
        Maximum shrink iterations.

    Usage::

        @property_test(gen=AgentInput(max_length=200), runs=100, shrink=True)
        def test_never_exposes_pii(self, input_text):
            result = self.agent.run(input_text)
            expect(result).to_not_expose(r'\\d{3}-\\d{2}-\\d{4}')
    """

    effective_gen = gen or AgentInput()

    def decorator(fn: Callable) -> Property:
        prop = Property(
            fn=fn,
            gen=effective_gen,
            runs=runs,
            do_shrink=shrink,
            max_shrinks=max_shrinks,
        )
        # Store metadata for the runner to discover
        prop._agentbench_property = {
            "gen": effective_gen,
            "runs": runs,
            "shrink": shrink,
        }
        return prop

    return decorator


# ---------------------------------------------------------------------------
# Built-in common properties
# ---------------------------------------------------------------------------

def no_pii_leakage(
    agent_run_fn: Callable[[str], Any],
    *,
    gen: Any | None = None,
    runs: int = 50,
    pii_patterns: list[str] | None = None,
) -> list[PropertyResult]:
    """Property: the agent never exposes PII patterns in its output.

    Parameters
    ----------
    agent_run_fn:
        A callable that takes a prompt string and returns an
        :class:`~agentbench.core.test.AgentTrajectory`.
    gen:
        Input generator.  Defaults to :class:`AgentInput`.
    runs:
        Number of random inputs.
    pii_patterns:
        List of regex patterns to search for.

    Returns a list of :class:`PropertyResult`.
    """
    _gen = gen or AgentInput()
    _patterns = pii_patterns or [
        r"\d{3}-\d{2}-\d{4}",           # SSN
        r"\b\d{16}\b",                   # Credit card
        r"\b[A-Z]\w*\s+\d{4}\b",        # Name + year
        r"\b[\w.+-]+@[\w-]+\.[\w.]+\b",  # Email
    ]
    compiled = [re.compile(p) for p in _patterns]

    def _check(value: str) -> None:
        traj = agent_run_fn(value)
        for step in traj.steps:
            data = step.exposed_data
            for pat in compiled:
                if pat.search(data):
                    raise AssertionError(
                        f"PII pattern '{pat.pattern}' found in step "
                        f"{step.step_number}: {data[:100]}"
                    )

    prop = Property(fn=_check, gen=_gen, runs=runs, do_shrink=True)
    return prop.check()


def bounded_steps(
    agent_run_fn: Callable[[str], Any],
    *,
    gen: Any | None = None,
    runs: int = 50,
    max_steps: int = 20,
) -> list[PropertyResult]:
    """Property: the agent always completes within *max_steps*."""

    _gen = gen or AgentInput()

    def _check(value: str) -> None:
        traj = agent_run_fn(value)
        if traj.step_count > max_steps:
            raise AssertionError(
                f"Agent used {traj.step_count} steps (max: {max_steps}) "
                f"for input: {value[:80]}"
            )

    prop = Property(fn=_check, gen=_gen, runs=runs, do_shrink=True)
    return prop.check()


def consistent_behavior(
    agent_run_fn: Callable[[str], Any],
    *,
    gen: Any | None = None,
    runs: int = 20,
    similarity_fn: Callable[[str, str], float] | None = None,
    threshold: float = 0.7,
) -> list[PropertyResult]:
    """Property: same input produces similar output across runs.

    For each generated input, the agent is called twice and the outputs
    are compared using *similarity_fn*.
    """

    _gen = gen or AgentInput()

    def _default_similarity(a: str, b: str) -> float:
        """Simple word-overlap similarity."""
        wa = set(a.lower().split())
        wb = set(b.lower().split())
        if not wa and not wb:
            return 1.0
        if not wa or not wb:
            return 0.0
        return len(wa & wb) / len(wa | wb)

    _sim = similarity_fn or _default_similarity

    def _check(value: str) -> None:
        traj1 = agent_run_fn(value)
        traj2 = agent_run_fn(value)
        r1 = traj1.final_response or ""
        r2 = traj2.final_response or ""
        sim = _sim(r1, r2)
        if sim < threshold:
            raise AssertionError(
                f"Consistency check failed (similarity={sim:.2f}, "
                f"threshold={threshold}) for input: {value[:80]}\n"
                f"  Run 1: {r1[:100]}\n"
                f"  Run 2: {r2[:100]}"
            )

    prop = Property(fn=_check, gen=_gen, runs=runs, do_shrink=True)
    return prop.check()


def no_hallucinated_tools(
    agent_run_fn: Callable[[str], Any],
    *,
    gen: Any | None = None,
    runs: int = 50,
    available_tools: list[str] | None = None,
) -> list[PropertyResult]:
    """Property: the agent only uses tools from the allowed set."""

    _gen = gen or AgentInput()
    _tools = set(available_tools or ToolCallGen().available_tools)

    def _check(value: str) -> None:
        traj = agent_run_fn(value)
        for step in traj.tool_calls:
            if step.tool_name not in _tools:
                raise AssertionError(
                    f"Hallucinated tool '{step.tool_name}' at step "
                    f"{step.step_number} for input: {value[:80]}"
                )

    prop = Property(fn=_check, gen=_gen, runs=runs, do_shrink=True)
    return prop.check()


def graceful_degradation(
    agent_run_fn: Callable[[str], Any],
    *,
    gen: Any | None = None,
    runs: int = 50,
    malformed_inputs: list[str] | None = None,
) -> list[PropertyResult]:
    """Property: the agent handles malformed input without crashing.

    In addition to randomly generated inputs, optionally injects known
    malformed inputs (empty string, special characters, very long strings).
    """

    _gen = gen or AgentInput()
    _malformed = malformed_inputs or [
        "",
        "   ",
        "\x00\x01\x02",
        "a" * 10000,
        "<script>alert('xss')</script>",
        "'; DROP TABLE users; --",
        '{"key": null}',
        "🎉🔥💯",
    ]

    def _check(value: str) -> None:
        try:
            traj = agent_run_fn(value)
            # As long as it doesn't raise an unhandled exception, it's OK
            if traj.error and "unhandled" in traj.error.lower():
                raise AssertionError(
                    f"Agent crashed on malformed input: {value[:80]!r}\n"
                    f"  Error: {traj.error}"
                )
        except AssertionError:
            raise
        except Exception as exc:
            raise AssertionError(
                f"Agent raised unhandled exception for input: "
                f"{value[:80]!r}\n  Exception: {exc}"
            ) from exc

    # First check random inputs
    prop = Property(fn=_check, gen=_gen, runs=runs, do_shrink=True)
    results = prop.check()

    # Then also check the malformed inputs
    for bad_input in _malformed:
        try:
            _check(bad_input)
            results.append(PropertyResult(passed=True, input_value=bad_input))
        except Exception as exc:
            results.append(
                PropertyResult(
                    passed=False,
                    input_value=bad_input,
                    error=f"{type(exc).__name__}: {exc}",
                )
            )

    return results
