"""Fixtures — reusable, injectable components for AgentBench tests."""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any


class FixtureRegistry:
    """Singleton registry that manages fixture caching by scope.

    Scope levels:
      - ``'test'``    (default): fresh instance every time — never cached.
      - ``'suite'``   : one instance per test class/suite, cached by suite name.
      - ``'session'``  : one instance for the entire test run, cached globally.
    """

    _instance: FixtureRegistry | None = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self._session_cache: dict[str, tuple[Any, Fixture | None]] = {}
        self._suite_cache: dict[str, dict[str, tuple[Any, Fixture | None]]] = {}
        # Track test-scoped generator fixtures that need teardown after each test
        self._pending_teardowns: list[Fixture] = []

    @classmethod
    def get(cls) -> FixtureRegistry:
        """Return the global singleton (creates it lazily)."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset the global singleton (useful between test runs / in tests)."""
        with cls._lock:
            if cls._instance is not None:
                cls._instance.teardown_all()
                cls._instance = None

    # ── Public API ──

    def get_fixture_value(
        self,
        fixture: Fixture,
        suite_name: str | None = None,
    ) -> Any:
        """Return the fixture value, respecting its scope.

        Args:
            fixture: The Fixture object to resolve.
            suite_name: The current suite name (required for suite-scoped fixtures).
        """
        key = fixture.__name__
        scope = fixture.scope

        if scope == "test":
            # Fresh instance every time
            value = fixture.setup()
            # If this is a generator fixture, track it so teardown runs after the test
            if hasattr(fixture, "_gen") and fixture._gen is not None:
                self._pending_teardowns.append(fixture)
            return value

        if scope == "session":
            if key not in self._session_cache:
                value = fixture.setup()
                self._session_cache[key] = (value, fixture)
            return self._session_cache[key][0]

        if scope == "suite":
            if suite_name is None:
                suite_name = "__default__"
            if suite_name not in self._suite_cache:
                self._suite_cache[suite_name] = {}
            suite = self._suite_cache[suite_name]
            if key not in suite:
                value = fixture.setup()
                suite[key] = (value, fixture)
            return suite[key][0]

        # Unknown scope — fall back to fresh
        return fixture.setup()

    def teardown_suite(self, suite_name: str) -> None:
        """Tear down all suite-scoped fixtures for the given suite."""
        if suite_name in self._suite_cache:
            for _key, (_value, fixture) in self._suite_cache[suite_name].items():
                if fixture is not None:
                    fixture.teardown()
            del self._suite_cache[suite_name]

    def teardown_test_fixtures(self) -> None:
        """Tear down all pending test-scoped generator fixtures.

        Should be called by the runner after each individual test completes
        to ensure generator fixture teardown code (after yield) runs.
        """
        for fixture in self._pending_teardowns:
            fixture.teardown()
        self._pending_teardowns.clear()

    def teardown_all(self) -> None:
        """Tear down all cached fixtures (session + all suites)."""
        # Session fixtures
        for _key, (_value, fixture) in self._session_cache.items():
            if fixture is not None:
                fixture.teardown()
        self._session_cache.clear()

        # All suite fixtures
        for suite_name in list(self._suite_cache.keys()):
            self.teardown_suite(suite_name)


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
        if scope not in ("test", "suite", "session"):
            raise ValueError(
                f"Invalid fixture scope '{scope}'. Must be one of: 'test', 'suite', 'session'."
            )
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

    def __call__(self) -> Any:
        """Shortcut for setup() — makes fixtures directly callable."""
        return self.setup()


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
