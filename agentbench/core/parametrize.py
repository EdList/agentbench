"""Parametric test support — run the same test with multiple inputs."""

from __future__ import annotations

from typing import Any, Callable


def parametrize(
    arg_name: str,
    arg_values: list[Any] | tuple[Any, ...],
) -> Callable:
    """Decorator to run a test method once per parameter value.

    Usage:
        @agentbench.parametrize("query", ["Buy shirt", "Return order"])
        def test_handles_queries(self, query):
            result = self.run(query)
            expect(result).to_complete_within(steps=8)

    The runner will detect ``_agentbench_parametrize`` on the decorated
    method and invoke it once per value, naming each iteration like
    ``test_handles_queries[Buy shirt]``.

    Args:
        arg_name: The parameter name injected into the test method call.
        arg_values: Iterable of values to iterate over.

    Returns:
        The decorated function with ``_agentbench_parametrize`` metadata.
    """
    def decorator(func: Callable) -> Callable:
        func._agentbench_parametrize = {
            "arg_name": arg_name,
            "arg_values": list(arg_values),
        }
        return func
    return decorator
