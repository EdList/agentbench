"""Tests for the adversarial test generation module."""

from __future__ import annotations

import copy

import pytest

from agentbench.adversarial.discovery import (
    AdversarialTestGenerator,
    _sanitize_identifier,
    adversarial_suite,
)
from agentbench.adversarial.mutator import (
    MutatorChain,
    PromptMutator,
    TrajectoryMutator,
    adversarial,
)
from agentbench.adversarial.strategies import (
    STRATEGY_REGISTRY,
    AdversarialStrategy,
    ContextOverflowStrategy,
    JailbreakStrategy,
    PIILeakStrategy,
    ToolConfusionStrategy,
    get_strategy,
    list_strategies,
)
from agentbench.core.test import AgentStep, AgentTest, AgentTrajectory

# =====================================================================
# Fixtures
# =====================================================================


def _make_trajectory(steps: int = 5) -> AgentTrajectory:
    """Create a simple test trajectory with tool calls."""
    t = AgentTrajectory(
        input_prompt="Test prompt",
        completed=True,
        final_response="Done",
    )
    for i in range(steps):
        t.steps.append(
            AgentStep(
                step_number=i,
                action="tool_call" if i % 2 == 0 else "llm_response",
                tool_name=f"tool_{i}" if i % 2 == 0 else None,
                tool_input={"arg": f"value_{i}"} if i % 2 == 0 else None,
                tool_output=f"result_{i}" if i % 2 == 0 else None,
                response=f"response_{i}" if i % 2 != 0 else None,
            )
        )
    return t


# =====================================================================
# PromptMutator tests
# =====================================================================


class TestPromptMutator:
    """Tests for PromptMutator."""

    def test_generate_returns_variants(self):
        m = PromptMutator(seed=42)
        variants = m.generate("Hello world")
        assert len(variants) > 0
        assert all(isinstance(v, str) for v in variants)

    def test_mutate_typos(self):
        m = PromptMutator(seed=42)
        original = "Hello world this is a test"
        variants = m.mutate_typos(original, count=3)
        assert len(variants) == 3
        # At least some should differ from original
        assert any(v != original for v in variants)

    def test_mutate_typos_empty(self):
        m = PromptMutator(seed=42)
        assert m.mutate_typos("") == []

    def test_mutate_whitespace(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_whitespace("Hello world", count=3)
        assert len(variants) == 3
        # Should have extra whitespace
        assert any("\t" in v or "  " in v or "\n" in v for v in variants)

    def test_mutate_whitespace_empty(self):
        m = PromptMutator(seed=42)
        assert m.mutate_whitespace("") == []

    def test_mutate_unicode_lookalikes(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_unicode_lookalikes("Hello", count=3)
        assert len(variants) == 3
        # Should look similar but be different
        assert all(len(v) == 5 for v in variants)

    def test_mutate_zero_width(self):
        m = PromptMutator(seed=42)
        original = "Hello"
        variants = m.mutate_zero_width(original, count=3)
        assert len(variants) == 3
        # Should be longer due to invisible chars
        assert all(len(v) > len(original) for v in variants)

    def test_mutate_rtl_overrides(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_rtl_overrides("Hello world", count=3)
        assert len(variants) == 3
        # Should contain RTL chars
        rtl_chars = set("\u202e\u202b\u202d")
        assert any(any(c in rtl_chars for c in v) for v in variants)

    def test_mutate_homoglyphs(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_homoglyphs("Hello", count=3)
        assert len(variants) == 3

    def test_mutate_injections(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_injections("Search for cats")
        assert len(variants) > 0
        # Should contain injection patterns
        assert any("DROP TABLE" in v for v in variants)
        assert any("DAN" in v for v in variants)
        assert any("system prompt" in v.lower() for v in variants)

    def test_mutate_injections_empty(self):
        m = PromptMutator(seed=42)
        assert m.mutate_injections("") == []

    def test_mutate_boundary_cases(self):
        m = PromptMutator(seed=42)
        variants = m.mutate_boundary_cases("Test prompt")
        assert len(variants) > 0
        # Should contain empty string
        assert "" in variants
        # Should contain special chars
        assert any("!@#$" in v for v in variants)
        # Should contain emojis
        assert any("🔥" in v for v in variants)

    def test_seed_reproducibility(self):
        m1 = PromptMutator(seed=123)
        m2 = PromptMutator(seed=123)
        assert m1.generate("test") == m2.generate("test")

    def test_different_seeds_different_results(self):
        m1 = PromptMutator(seed=1)
        m2 = PromptMutator(seed=2)
        assert m1.mutate_typos("test prompt") != m2.mutate_typos("test prompt")


# =====================================================================
# TrajectoryMutator tests
# =====================================================================


class TestTrajectoryMutator:
    """Tests for TrajectoryMutator."""

    def test_generate_returns_variants(self):
        m = TrajectoryMutator(seed=42)
        t = _make_trajectory(5)
        variants = m.generate(t)
        assert len(variants) > 0
        assert all(isinstance(v, AgentTrajectory) for v in variants)

    def test_mutate_swap_tool_order(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        swapped = m.mutate_swap_tool_order(original)
        # Original should be unchanged
        assert original.steps[0].step_number == 0
        # Swapped is a new object
        assert swapped is not original

    def test_mutate_swap_tool_order_preserves_step_count(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        swapped = m.mutate_swap_tool_order(original)
        assert len(swapped.steps) == len(original.steps)

    def test_mutate_remove_steps(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        results = m.mutate_remove_steps(original, count=2)
        assert len(results) == 2
        assert all(len(r.steps) == len(original.steps) - 1 for r in results)

    def test_mutate_remove_steps_too_few(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(2)
        results = m.mutate_remove_steps(original)
        assert results == []

    def test_mutate_duplicate_steps(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        duped = m.mutate_duplicate_steps(original)
        assert len(duped.steps) == len(original.steps) + 1

    def test_mutate_tool_inputs(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        results = m.mutate_tool_inputs(original, count=2)
        assert len(results) == 2
        # At least one should have mutated input
        has_mutation = False
        for r in results:
            for s in r.steps:
                if s.tool_input:
                    for v in s.tool_input.values():
                        if isinstance(v, str) and "_mutated" in v:
                            has_mutation = True
        assert has_mutation

    def test_mutate_tool_inputs_toggles_booleans_instead_of_casting_to_int(self):
        m = TrajectoryMutator(seed=42)
        trajectory = AgentTrajectory()
        trajectory.steps.append(
            AgentStep(
                step_number=0,
                action="tool_call",
                tool_name="search",
                tool_input={"flag": True},
            )
        )

        results = m.mutate_tool_inputs(trajectory, count=1)

        assert results[0].steps[0].tool_input == {"flag": False}

    def test_does_not_mutate_original(self):
        m = TrajectoryMutator(seed=42)
        original = _make_trajectory(5)
        orig_steps_copy = copy.deepcopy(original.steps)
        m.generate(original)
        # Original should be unchanged
        for i, s in enumerate(original.steps):
            assert s.tool_input == orig_steps_copy[i].tool_input

    def test_seed_reproducibility(self):
        m1 = TrajectoryMutator(seed=42)
        m2 = TrajectoryMutator(seed=42)
        t = _make_trajectory(5)
        v1 = m1.generate(t)
        v2 = m2.generate(t)
        assert len(v1) == len(v2)


# =====================================================================
# MutatorChain tests
# =====================================================================


class TestMutatorChain:
    """Tests for MutatorChain."""

    def test_apply_prompt_empty_chain(self):
        chain = MutatorChain()
        result = chain.apply_prompt("test")
        assert result == ["test"]

    def test_apply_prompt_with_mutator(self):
        chain = MutatorChain(PromptMutator(seed=42))
        result = chain.apply_prompt("Hello world")
        assert len(result) > 1

    def test_apply_prompt_with_multiple_mutators(self):
        m1 = PromptMutator(seed=1)
        m2 = PromptMutator(seed=2)
        chain = MutatorChain(m1, m2)
        result = chain.apply_prompt("test")
        # Should have original + variants from m1 + variants from m2
        assert len(result) > 2

    def test_apply_trajectory_empty_chain(self):
        chain = MutatorChain()
        t = _make_trajectory()
        result = chain.apply_trajectory(t)
        assert len(result) == 1

    def test_apply_trajectory_with_mutator(self):
        chain = MutatorChain(TrajectoryMutator(seed=42))
        t = _make_trajectory(5)
        result = chain.apply_trajectory(t)
        assert len(result) > 1

    def test_add_method(self):
        chain = MutatorChain()
        result = chain.add(PromptMutator(seed=42))
        assert result is chain
        assert len(chain._mutators) == 1

    def test_mixed_mutators(self):
        chain = MutatorChain(
            PromptMutator(seed=42),
            TrajectoryMutator(seed=42),
        )
        # Should handle both types
        prompts = chain.apply_prompt("test")
        assert len(prompts) > 1
        t = _make_trajectory(5)
        trajectories = chain.apply_trajectory(t)
        assert len(trajectories) > 1


# =====================================================================
# @adversarial decorator tests
# =====================================================================


class TestAdversarialDecorator:
    """Tests for the @adversarial decorator."""

    def test_adds_metadata(self):
        @adversarial(count=10)
        class DummyTest(AgentTest):
            def test_foo(self):
                pass

        assert DummyTest._adversarial_enabled is True
        assert DummyTest._adversarial_config["count"] == 10
        assert "test_foo" in DummyTest._adversarial_original_methods

    def test_preserves_class(self):
        @adversarial()
        class DummyTest(AgentTest):
            agent = "test-agent"

            def test_bar(self):
                pass

        assert DummyTest.agent == "test-agent"
        assert issubclass(DummyTest, AgentTest)

    def test_custom_mutators(self):
        custom = PromptMutator(seed=99)

        @adversarial(mutators=[custom])
        class DummyTest(AgentTest):
            def test_baz(self):
                pass

        assert DummyTest._adversarial_config["mutators"] == [custom]


# =====================================================================
# Strategy tests
# =====================================================================


class TestJailbreakStrategy:
    """Tests for JailbreakStrategy."""

    def test_generate_returns_inputs(self):
        s = JailbreakStrategy(count=5, seed=42)
        results = s.generate()
        assert len(results) >= 5
        assert all(isinstance(r, str) for r in results)

    def test_contains_dan_patterns(self):
        s = JailbreakStrategy(count=10, seed=42)
        results = s.generate()
        assert any("DAN" in r for r in results)

    def test_intensity_affects_count(self):
        s1 = JailbreakStrategy(intensity=1, count=2, seed=42)
        s3 = JailbreakStrategy(intensity=3, count=2, seed=42)
        r1 = s1.generate()
        r3 = s3.generate()
        assert len(r3) >= len(r1)

    def test_base64_variants_at_high_intensity(self):
        s = JailbreakStrategy(intensity=3, count=1, seed=42)
        results = s.generate()
        assert any("base64" in r.lower() for r in results)

    def test_name_and_description(self):
        s = JailbreakStrategy()
        assert s.name == "jailbreak"
        assert s.description


class TestPIILeakStrategy:
    """Tests for PIILeakStrategy."""

    def test_generate_returns_inputs(self):
        s = PIILeakStrategy(count=5, seed=42)
        results = s.generate()
        assert len(results) >= 5

    def test_asks_for_pii(self):
        s = PIILeakStrategy(count=10, seed=42)
        results = s.generate()
        combined = " ".join(results).lower()
        assert any(word in combined for word in ["email", "phone", "address", "pii", "password"])

    def test_high_intensity_adds_sql(self):
        s = PIILeakStrategy(intensity=3, count=1, seed=42)
        results = s.generate()
        assert any("SELECT" in r for r in results)

    def test_very_high_intensity_adds_emergency(self):
        s = PIILeakStrategy(intensity=5, count=1, seed=42)
        results = s.generate()
        assert any("EMERGENCY" in r or "maintenance" in r.lower() for r in results)


class TestToolConfusionStrategy:
    """Tests for ToolConfusionStrategy."""

    def test_generate_returns_inputs(self):
        s = ToolConfusionStrategy(count=5, seed=42)
        results = s.generate()
        assert len(results) >= 5

    def test_contains_ambiguous_requests(self):
        s = ToolConfusionStrategy(count=10, seed=42)
        results = s.generate()
        assert any("the thing" in r.lower() or "the stuff" in r.lower() for r in results)

    def test_high_intensity_adds_tool_chains(self):
        s = ToolConfusionStrategy(intensity=4, count=1, seed=42)
        results = s.generate()
        assert any("→" in r for r in results)


class TestContextOverflowStrategy:
    """Tests for ContextOverflowStrategy."""

    def test_generate_returns_inputs(self):
        s = ContextOverflowStrategy(count=3, seed=42)
        results = s.generate()
        assert len(results) >= 3

    def test_generates_long_inputs(self):
        s = ContextOverflowStrategy(count=2, intensity=1, seed=42)
        results = s.generate()
        assert any(len(r) > 1000 for r in results)

    def test_intensity_scales_length(self):
        s1 = ContextOverflowStrategy(count=1, intensity=1, seed=42)
        s5 = ContextOverflowStrategy(count=1, intensity=5, seed=42)
        r1 = s1.generate()
        r5 = s5.generate()
        assert any(len(r) for r in r5)
        max_r5 = max(len(r) for r in r5)
        max_r1 = max(len(r) for r in r1)
        assert max_r5 >= max_r1

    def test_contains_nested_json_at_high_intensity(self):
        s = ContextOverflowStrategy(count=1, intensity=3, seed=42)
        results = s.generate()
        assert any("nested" in r for r in results)


class TestStrategyRegistry:
    """Tests for strategy registry and helpers."""

    def test_all_strategies_registered(self):
        assert "jailbreak" in STRATEGY_REGISTRY
        assert "pii_leak" in STRATEGY_REGISTRY
        assert "tool_confusion" in STRATEGY_REGISTRY
        assert "context_overflow" in STRATEGY_REGISTRY

    def test_get_strategy(self):
        s = get_strategy("jailbreak", count=5)
        assert isinstance(s, JailbreakStrategy)
        assert s.count == 5

    def test_get_strategy_unknown(self):
        with pytest.raises(ValueError, match="Unknown strategy"):
            get_strategy("nonexistent")

    def test_list_strategies(self):
        strategies = list_strategies()
        assert len(strategies) == 4
        assert all("name" in s and "description" in s for s in strategies)

    def test_invalid_intensity(self):
        with pytest.raises(ValueError, match="intensity"):
            JailbreakStrategy(intensity=0)
        with pytest.raises(ValueError, match="intensity"):
            JailbreakStrategy(intensity=6)

    def test_strategy_is_abstract(self):
        with pytest.raises(TypeError):
            AdversarialStrategy()


# =====================================================================
# AdversarialTestGenerator tests
# =====================================================================


class TestAdversarialTestGenerator:
    """Tests for AdversarialTestGenerator."""

    def test_generate_prompts(self):
        class SampleTest(AgentTest):
            def test_basic(self):
                self.run("Buy a shirt")

        gen = AdversarialTestGenerator(SampleTest, seed=42)
        variants = gen.generate_prompts("Buy a shirt")
        assert len(variants) > 0
        assert all("name" in v and "prompt" in v for v in variants)

    def test_generate_prompts_with_strategies(self):
        class SampleTest(AgentTest):
            def test_basic(self):
                pass

        gen = AdversarialTestGenerator(
            SampleTest,
            strategies=["jailbreak"],
            seed=42,
        )
        variants = gen.generate_prompts("test")
        # Should have both mutator and strategy variants
        assert any(v["name"].startswith("mutated_") for v in variants)
        assert any(v["name"].startswith("jailbreak_") for v in variants)

    def test_generate_class(self):
        class SampleTest(AgentTest):
            agent = "test"

            def test_basic(self):
                self.run("Buy a shirt")

        gen = AdversarialTestGenerator(SampleTest, seed=42)
        cls = gen.generate_class()
        assert issubclass(cls, SampleTest)
        assert cls.__name__ == "AdversarialSampleTest"

    def test_generate_class_has_test_methods(self):
        class SampleTest(AgentTest):
            def test_basic(self):
                self.run("Buy a shirt")

        gen = AdversarialTestGenerator(
            SampleTest,
            strategies=["jailbreak"],
            seed=42,
        )
        cls = gen.generate_class()
        test_methods = [
            name for name in dir(cls) if name.startswith("test_") and callable(getattr(cls, name))
        ]
        assert len(test_methods) > 0

    def test_generate_class_custom_name(self):
        class SampleTest(AgentTest):
            pass

        gen = AdversarialTestGenerator(SampleTest, seed=42)
        cls = gen.generate_class(class_name="CustomAdversarial")
        assert cls.__name__ == "CustomAdversarial"

    def test_generate_class_with_strategy_instances(self):
        class SampleTest(AgentTest):
            def test_x(self):
                pass

        strategy = JailbreakStrategy(count=3, seed=42)
        gen = AdversarialTestGenerator(
            SampleTest,
            strategies=[strategy],
            seed=42,
        )
        cls = gen.generate_class()
        assert cls is not None


# =====================================================================
# @adversarial_suite tests
# =====================================================================


class TestAdversarialSuiteDecorator:
    """Tests for the @adversarial_suite decorator."""

    def test_adds_suite_attribute(self):
        @adversarial_suite(strategies=["jailbreak"])
        class DummyTest(AgentTest):
            def test_basic(self):
                pass

        assert hasattr(DummyTest, "_adversarial_suite")
        assert DummyTest._adversarial_suite is not None

    def test_preserves_class(self):
        @adversarial_suite(strategies=["jailbreak"])
        class DummyTest(AgentTest):
            agent = "test-agent"

            def test_basic(self):
                pass

        assert DummyTest.agent == "test-agent"

    def test_config_metadata(self):
        @adversarial_suite(strategies=["jailbreak", "pii_leak"], seed=42)
        class DummyTest(AgentTest):
            pass

        config = DummyTest._adversarial_suite_config
        assert "jailbreak" in config["strategies"]
        assert "pii_leak" in config["strategies"]
        assert config["seed"] == 42


# =====================================================================
# Helper function tests
# =====================================================================


class TestSanitizeIdentifier:
    """Tests for _sanitize_identifier."""

    def test_basic(self):
        assert _sanitize_identifier("hello_world") == "hello_world"

    def test_special_chars(self):
        result = _sanitize_identifier("hello world!@#")
        assert result == "hello_world_"

    def test_starts_with_number(self):
        result = _sanitize_identifier("123test")
        assert result.startswith("_")

    def test_collapses_underscores(self):
        result = _sanitize_identifier("a---b")
        assert result == "a_b"
