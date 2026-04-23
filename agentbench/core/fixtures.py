"""Fixtures — reusable, injectable components for AgentBench tests."""

from __future__ import annotations

from typing import Any, Callable


class Fixture:
    """A reusable test fixture.

    Use the ``@fixture`` decorator to create fixtures that can be
    shared across test suites.  Fixtures are plain callables that
    return a value (or perform setup side-effects).

    Usage::

        @agentbench.fixture
        def authenticated_client():
            client = create_client()
            client.login("test-user")
            yield client          # the value injected into tests
            client.logout()       # teardown after yield

        class MyTest(AgentTest):
            def test_something(self, authenticated_client):
                ...
    """

    def __init__(self, func: Callable, *, scope: str = "test") -> None:
        self._func = func
        self._scope = scope
        self.__name__ = getattr(func, "__name__", "fixture")
        self.__doc__ = getattr(func, "__doc__", None)

    @property
    def scope(self) -> str:
        """Fixture scope: 'test' (default), 'suite', or 'session'."""
        return self._scope

    def setup(self) -> Any:
        """Execute the fixture and return its value.

        Supports generator fixtures (with ``yield``): everything before
        the yield is setup, everything after is teardown.
        """
        import inspect
        if inspect.isgeneratorfunction(self._func):
            gen = self._func()
            value = next(gen)
            # Store generator for later teardown
            self._gen = gen
            return value
        return self._func()

    def teardown(self) -> None:
        """Run teardown for generator fixtures."""
        gen = getattr(self, "_gen", None)
        if gen is not None:
            try:
                next(gen)
            except StopIteration:
                pass
            self._gen = None


def fixture(func: Callable | None = None, *, scope: str = "test") -> Fixture:
    """Decorator to create a reusable test fixture.

    Can be used with or without arguments::

        @agentbench.fixture
        def db_connection():
            conn = connect()
            yield conn
            conn.close()

        @agentbench.fixture(scope="suite")
        def test_data():
            return load_test_data()

    Args:
        func: The fixture function (when used without parentheses).
        scope: Fixture lifetime — ``'test'``, ``'suite'``, or ``'session'``.
    """
    if func is not None:
        # Called as @fixture without arguments
        return Fixture(func, scope=scope)
    # Called as @fixture(scope=...) with arguments
    def decorator(f: Callable) -> Fixture:
        return Fixture(f, scope=scope)
    return decorator
