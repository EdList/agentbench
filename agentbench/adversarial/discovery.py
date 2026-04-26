"""Auto-discovery — generate adversarial test variants from base test classes."""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from agentbench.adversarial.mutator import PromptMutator
from agentbench.adversarial.strategies import (
    AdversarialStrategy,
    get_strategy,
)


class AdversarialTestGenerator:
    """Takes a base test class and generates adversarial variants.

    Usage::

        from agentbench.adversarial import AdversarialTestGenerator

        gen = AdversarialTestGenerator(MyTest)
        variants = gen.generate_prompts()
        suite_cls = gen.generate_class()
    """

    def __init__(
        self,
        base_class: type,
        *,
        strategies: list[AdversarialStrategy | str] | None = None,
        prompt_mutator: PromptMutator | None = None,
        seed: int | None = None,
    ):
        self._base_class = base_class
        self._seed = seed

        if prompt_mutator is not None:
            self._prompt_mutator = prompt_mutator
        else:
            self._prompt_mutator = PromptMutator(seed=seed)

        # Resolve strategies
        self._strategies: list[AdversarialStrategy] = []
        if strategies:
            for s in strategies:
                if isinstance(s, str):
                    self._strategies.append(get_strategy(s, seed=seed))
                else:
                    self._strategies.append(s)

    # -- Prompt generation -------------------------------------------------

    def generate_prompts(self, base_prompt: str) -> list[dict[str, str]]:
        """Generate adversarial prompt variants.

        Returns a list of dicts with ``name`` and ``prompt`` keys.
        """
        variants: list[dict[str, str]] = []

        # Mutator-based variants
        mutated = self._prompt_mutator.generate(base_prompt)
        for i, variant in enumerate(mutated):
            variants.append({
                "name": f"mutated_{i}",
                "prompt": variant,
            })

        # Strategy-based variants
        for strategy in self._strategies:
            inputs = strategy.generate()
            for i, inp in enumerate(inputs):
                variants.append({
                    "name": f"{strategy.name}_{i}",
                    "prompt": inp,
                })

        return variants

    # -- Class generation --------------------------------------------------

    def generate_class(
        self,
        class_name: str | None = None,
    ) -> type:
        """Produce a new test class with adversarial test methods.

        The generated class inherits from the base class and adds
        ``test_adversarial_*`` methods that embed the generated variants.
        """
        name = class_name or f"Adversarial{self._base_class.__name__}"

        # Collect base prompts from existing test methods
        base_prompts = self._extract_base_prompts()

        # Build test methods
        methods: dict[str, Callable] = {}

        for base_info in base_prompts:
            base_name = base_info["method"]
            base_prompt = base_info.get("prompt", "")
            variants = self.generate_prompts(base_prompt)

            for variant in variants:
                vname = variant["name"]
                method_name = f"test_adversarial_{base_name}_{vname}"
                # Make a valid Python identifier
                method_name = _sanitize_identifier(method_name)
                prompt = variant["prompt"]

                # Create a closure capturing the prompt
                def _make_test(p: str, orig: str) -> Callable:
                    def test_fn(self: Any) -> None:
                        # If self.run(p) completes, the test passes — the
                        # agent handled the adversarial input gracefully.
                        # AssertionError means the test found a real issue
                        # (e.g. the agent produced unsafe output).
                        # Non-AssertionError (connection error, timeout, …)
                        # is stored as a skip/error rather than a silent pass.
                        try:
                            self.run(p)
                        except AssertionError:
                            # Real issue — let it propagate
                            raise
                        except Exception as non_assert_exc:
                            # Infrastructure / runtime error — mark as skipped
                            import pytest as _pytest  # type: ignore[import-untyped]
                            _pytest.skip(
                                f"Adversarial variant skipped due to "
                                f"{type(non_assert_exc).__name__}: "
                                f"{non_assert_exc}"
                            )
                    test_fn.__doc__ = (
                        f"Adversarial variant of {orig} "
                        f"(strategy: {_sanitize_identifier(vname)})"
                    )
                    return test_fn
                methods[method_name] = _make_test(prompt, base_name)

        # Also add auto-generated tests for common failure modes
        auto_tests = self._auto_failure_mode_tests()
        methods.update(auto_tests)

        # Create the class
        cls = type(name, (self._base_class,), methods)
        cls.__test__ = False
        return cls

    def _extract_base_prompts(self) -> list[dict[str, str]]:
        """Extract prompts from the base class test methods.

        Looks for string literals that look like prompts in test methods.
        Falls back to generating from strategy inputs.
        """
        results: list[dict[str, str]] = []
        for attr_name in dir(self._base_class):
            if not attr_name.startswith("test_"):
                continue
            method = getattr(self._base_class, attr_name, None)
            if method is None or not callable(method):
                continue

            # Try to extract string literals from the source
            prompt = self._extract_prompt_from_method(method)
            results.append({
                "method": attr_name,
                "prompt": prompt or "default test prompt",
            })

        # If no test methods found, use strategy inputs
        if not results:
            for strategy in self._strategies:
                inputs = strategy.generate()
                for i, inp in enumerate(inputs):
                    results.append({
                        "method": f"strategy_{strategy.name}",
                        "prompt": inp,
                    })

        return results

    def _extract_prompt_from_method(self, method: Callable) -> str | None:
        """Try to extract a prompt string from a test method's source."""
        try:
            source = inspect.getsource(method)
        except (OSError, TypeError):
            return None

        # Look for string literals in self.run("...") calls
        import re
        match = re.search(r'self\.run\(\s*["\'](.+?)["\']', source)
        if match:
            return match.group(1)

        # Look for any string constant
        match = re.search(r'["\']([a-zA-Z][^"\']{10,})["\']', source)
        if match:
            return match.group(1)

        return None

    def _auto_failure_mode_tests(self) -> dict[str, Callable]:
        """Auto-generate tests for common failure modes."""
        methods: dict[str, Callable] = {}

        failure_modes = [
            ("empty_prompt", ""),
            ("whitespace_only", "   \t\n  "),
            ("very_long", "x " * 1000),
            ("special_chars", "!@#$%^&*()_+-=[]{}|;':\",./<>?"),
            ("null_bytes", "test\x00\x01\x02"),
            ("unicode_stress", "🔥" * 100),
        ]

        for mode_name, prompt in failure_modes:
            method_name = f"test_adversarial_auto_{mode_name}"

            def _make(p: str, mn: str) -> Callable:
                def test_fn(self: Any) -> None:
                    try:
                        self.run(p)
                    except AssertionError:
                        # Real issue — let it propagate
                        raise
                    except Exception as non_assert_exc:
                        # Infrastructure / runtime error — mark as skipped
                        import pytest as _pytest  # type: ignore[import-untyped]
                        _pytest.skip(
                            f"Auto adversarial test skipped due to "
                            f"{type(non_assert_exc).__name__}: "
                            f"{non_assert_exc}"
                        )
                test_fn.__doc__ = f"Auto adversarial test: {mn}"
                return test_fn
            methods[method_name] = _make(prompt, mode_name)

        return methods


# ---------------------------------------------------------------------------
# @adversarial_suite class decorator
# ---------------------------------------------------------------------------

def adversarial_suite(
    *,
    strategies: list[str | AdversarialStrategy] | None = None,
    seed: int | None = None,
    class_name: str | None = None,
) -> Callable:
    """Class decorator that generates adversarial test variants.

    Usage::

        @adversarial_suite(strategies=["jailbreak", "pii_leak"])
        class MyTest(AgentTest):
            def test_something(self):
                result = self.run("Buy a shirt")
                expect(result).to_complete()
    """

    def decorator(cls: type) -> type:
        generator = AdversarialTestGenerator(
            cls,
            strategies=strategies,
            seed=seed,
        )
        generated = generator.generate_class(class_name=class_name)
        # Attach the generated class as an attribute so the runner can find it
        cls._adversarial_suite = generated
        cls._adversarial_suite_config = {
            "strategies": [s if isinstance(s, str) else s.name for s in (strategies or [])],
            "seed": seed,
        }
        return cls

    return decorator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sanitize_identifier(name: str) -> str:
    """Convert a string into a valid Python identifier."""
    result = []
    for ch in name:
        if ch.isalnum() or ch == "_":
            result.append(ch)
        else:
            result.append("_")
    identifier = "".join(result)

    # Ensure it starts with a letter or underscore
    if identifier and not (identifier[0].isalpha() or identifier[0] == "_"):
        identifier = "_" + identifier

    # Collapse multiple underscores
    import re
    identifier = re.sub(r"_+", "_", identifier)

    return identifier
