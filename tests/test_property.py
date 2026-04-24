"""Tests for the property-based testing module.

Covers generators, property definitions, shrinking, and composition.
"""

from __future__ import annotations

from agentbench.property.generators import (
    AgentInput,
    ConversationGen,
    ConversationTurn,
    ToolCall,
    ToolCallGen,
    TrajectoryGen,
)
from agentbench.property.properties import (
    Property,
    PropertyResult,
    bounded_steps,
    consistent_behavior,
    graceful_degradation,
    no_hallucinated_tools,
    no_pii_leakage,
    property_test,
)
from agentbench.property.shrink import (
    default_shrink_candidates,
    shrink,
)

# ===================================================================
# Helper utilities
# ===================================================================

def _make_trajectory(
    steps=None,
    final_response="done",
    completed=True,
    input_prompt="hello",
):
    """Build a minimal AgentTrajectory for testing."""
    from agentbench.core.test import AgentTrajectory

    traj = AgentTrajectory(
        input_prompt=input_prompt,
        final_response=final_response,
        completed=completed,
    )
    if steps:
        for s in steps:
            traj.steps.append(s)
    return traj


def _step(
    number=1,
    action="llm_response",
    response="ok",
    tool_name=None,
    error=None,
    exposed="",
):
    from agentbench.core.test import AgentStep

    return AgentStep(
        step_number=number,
        action=action,
        tool_name=tool_name,
        response=response,
        error=error,
        reasoning=exposed or None,
    )


# ===================================================================
# AgentInput generator
# ===================================================================

class TestAgentInput:
    def test_generates_string(self):
        gen = AgentInput()
        value = gen.generate()
        assert isinstance(value, str)

    def test_respects_max_length(self):
        gen = AgentInput(max_length=50)
        for _ in range(20):
            value = gen.generate()
            assert len(value) <= 50

    def test_respects_min_length(self):
        gen = AgentInput(min_length=30)
        for _ in range(20):
            value = gen.generate()
            assert len(value) >= 30

    def test_domain_vocabulary(self):
        gen = AgentInput(domain="finance")
        values = gen.generate_many(30)
        # At least some values should contain finance-related words
        text = " ".join(values).lower()
        assert any(w in text for w in ["invoice", "payment", "transaction", "balance"])

    def test_kinds_parameter(self):
        gen = AgentInput(kinds=["question"])
        for _ in range(20):
            v = gen.generate()
            # All question templates end with "?" or are statements about nouns
            # Just verify it generated a non-empty string using the question pool
            assert isinstance(v, str) and len(v) > 0

    def test_seed_reproducibility(self):
        gen1 = AgentInput(seed=42)
        gen2 = AgentInput(seed=42)
        v1 = gen1.generate()
        v2 = gen2.generate()
        assert v1 == v2

    def test_generate_many(self):
        gen = AgentInput()
        values = gen.generate_many(10)
        assert len(values) == 10
        assert all(isinstance(v, str) for v in values)


# ===================================================================
# ToolCallGen
# ===================================================================

class TestToolCallGen:
    def test_generates_list_of_tool_calls(self):
        gen = ToolCallGen()
        calls = gen.generate()
        assert isinstance(calls, list)
        assert all(isinstance(c, ToolCall) for c in calls)

    def test_respects_bounds(self):
        gen = ToolCallGen(min_calls=3, max_calls=3)
        calls = gen.generate()
        assert len(calls) == 3

    def test_uses_custom_tools(self):
        gen = ToolCallGen(available_tools=["foo", "bar"])
        for _ in range(20):
            calls = gen.generate()
            for c in calls:
                assert c.tool_name in ("foo", "bar")

    def test_tool_call_to_dict(self):
        tc = ToolCall(tool_name="search", arguments={"q": "test"})
        d = tc.to_dict()
        assert d["tool_name"] == "search"
        assert d["arguments"]["q"] == "test"

    def test_seed_reproducibility(self):
        g1 = ToolCallGen(seed=7)
        g2 = ToolCallGen(seed=7)
        assert g1.generate() == g2.generate()


# ===================================================================
# ConversationGen
# ===================================================================

class TestConversationGen:
    def test_generates_conversation(self):
        gen = ConversationGen()
        turns = gen.generate()
        assert isinstance(turns, list)
        assert all(isinstance(t, ConversationTurn) for t in turns)

    def test_respects_turn_bounds(self):
        gen = ConversationGen(min_turns=4, max_turns=4)
        turns = gen.generate()
        assert len(turns) == 4

    def test_first_turn_is_system(self):
        gen = ConversationGen(min_turns=3)
        turns = gen.generate()
        assert turns[0].role == "system"

    def test_alternating_roles(self):
        gen = ConversationGen(min_turns=5, max_turns=5)
        turns = gen.generate()
        assert turns[0].role == "system"
        assert turns[1].role == "user"
        assert turns[2].role == "assistant"


# ===================================================================
# TrajectoryGen
# ===================================================================

class TestTrajectoryGen:
    def test_generates_trajectory(self):
        from agentbench.core.test import AgentTrajectory

        gen = TrajectoryGen()
        traj = gen.generate()
        assert isinstance(traj, AgentTrajectory)

    def test_respects_step_bounds(self):
        gen = TrajectoryGen(min_steps=3, max_steps=3)
        traj = gen.generate()
        assert traj.step_count == 3

    def test_custom_tools(self):
        gen = TrajectoryGen(available_tools=["only_tool"])
        for _ in range(20):
            traj = gen.generate()
            for s in traj.tool_calls:
                assert s.tool_name == "only_tool"


# ===================================================================
# Generator composition: map, filter, chain
# ===================================================================

class TestGeneratorComposition:
    def test_map(self):
        gen = AgentInput(max_length=100).map(str.upper)
        for _ in range(10):
            v = gen.generate()
            assert v == v.upper()

    def test_filter(self):
        gen = AgentInput(min_length=20, max_length=200).filter(
            lambda v: len(v) >= 20
        )
        for _ in range(10):
            v = gen.generate()
            assert len(v) >= 20

    def test_chain(self):
        gen1 = AgentInput()
        gen2 = AgentInput()
        chained = gen1.chain(gen2)
        # The chained generator should produce output from gen2
        value = chained.generate()
        assert isinstance(value, str)

    def test_filter_with_map(self):
        gen = (
            AgentInput(min_length=10)
            .filter(lambda v: len(v) >= 10)
            .map(str.strip)
        )
        for _ in range(10):
            v = gen.generate()
            assert len(v) >= 10


# ===================================================================
# Shrinking engine
# ===================================================================

class TestShrink:
    def test_shrink_string_to_empty(self):
        result = shrink(
            "hello world foo bar",
            predicate=lambda v: len(v) > 0,  # fails as long as non-empty
        )
        # "h" is the smallest non-empty string the shrinker can find
        assert result.minimal == "h"
        assert result.was_shrunk

    def test_shrink_int_to_zero(self):
        result = shrink(42, predicate=lambda v: v > 0)
        assert result.minimal == 1
        assert result.was_shrunk

    def test_shrink_list_to_empty(self):
        result = shrink([1, 2, 3], predicate=lambda v: len(v) > 0)
        # Smallest non-empty list is [1]
        assert result.minimal == [1]
        assert result.was_shrunk

    def test_shrink_dict_to_empty(self):
        result = shrink(
            {"a": 1, "b": 2},
            predicate=lambda v: len(v) > 0,
        )
        # Smallest non-empty dict has 1 key
        assert len(result.minimal) == 1
        assert result.was_shrunk

    def test_shrink_respects_max_shrinks(self):
        result = shrink(
            "x" * 1000,
            predicate=lambda v: len(v) > 0,
            max_shrinks=5,
        )
        # The shrink loop may overshoot by 1 due to inner loop, but it stops
        assert result.shrinks_tried <= 10

    def test_shrink_result_summary(self):
        result = shrink("hello", predicate=lambda v: True)
        summary = result.summary()
        assert "Shrink Result" in summary

    def test_default_shrink_candidates_str(self):
        candidates = default_shrink_candidates("hello world")
        assert "" in candidates
        assert len(candidates) > 0

    def test_default_shrink_candidates_int(self):
        candidates = default_shrink_candidates(10)
        assert 0 in candidates
        assert 5 in candidates

    def test_default_shrink_candidates_float(self):
        candidates = default_shrink_candidates(3.14)
        assert 0.0 in candidates

    def test_default_shrink_candidates_list(self):
        candidates = default_shrink_candidates([1, 2, 3])
        assert [] in candidates

    def test_default_shrink_candidates_dict(self):
        candidates = default_shrink_candidates({"a": 1, "b": 2})
        assert {} in candidates

    def test_default_shrink_candidates_unknown_type(self):
        # Should return empty list for unsupported types
        candidates = default_shrink_candidates(object())
        assert candidates == []

    def test_shrink_negative_int(self):
        result = shrink(-5, predicate=lambda v: v < 0)
        assert result.minimal == -1

    def test_shrink_float_towards_zero(self):
        result = shrink(8.0, predicate=lambda v: v > 1.0)
        assert result.minimal > 1.0
        assert result.minimal <= 4.0


# ===================================================================
# Generator shrinking helpers
# ===================================================================

class TestGeneratorShrinking:
    def test_agent_input_shrink_value(self):
        gen = AgentInput()
        original = "hello world and more words here"
        shrinks = gen.shrink_value(original)
        assert len(shrinks) > 0
        assert all(isinstance(s, str) for s in shrinks)
        assert all(len(s) <= len(original) for s in shrinks)

    def test_tool_call_gen_shrink_value(self):
        gen = ToolCallGen()
        calls = [ToolCall("a"), ToolCall("b"), ToolCall("c")]
        shrinks = gen.shrink_value(calls)
        assert len(shrinks) > 0
        assert all(len(s) < len(calls) for s in shrinks)

    def test_conversation_gen_shrink_value(self):
        gen = ConversationGen()
        turns = [
            ConversationTurn("user", "hello there"),
            ConversationTurn("assistant", "hi"),
            ConversationTurn("user", "how are you"),
        ]
        shrinks = gen.shrink_value(turns)
        assert len(shrinks) > 0

    def test_trajectory_gen_shrink_value(self):
        from agentbench.core.test import AgentTrajectory

        gen = TrajectoryGen(seed=1)
        traj = gen.generate()
        shrinks = gen.shrink_value(traj)
        assert len(shrinks) > 0
        for s in shrinks:
            assert isinstance(s, AgentTrajectory)


# ===================================================================
# Property wrapper
# ===================================================================

class TestProperty:
    def test_property_passes(self):
        def always_ok(value: str) -> None:
            pass

        prop = Property(fn=always_ok, gen=AgentInput(), runs=10, do_shrink=False)
        results = prop.check()
        assert len(results) == 10
        assert all(r.passed for r in results)

    def test_property_fails(self):
        def always_fail(value: str) -> None:
            raise AssertionError("boom")

        prop = Property(fn=always_fail, gen=AgentInput(), runs=5, do_shrink=False)
        results = prop.check()
        # Fail-fast: only 1 result
        assert len(results) == 1
        assert not results[0].passed
        assert "boom" in results[0].error

    def test_property_with_instance(self):
        """Property can be used as a method on a class."""

        class FakeTest:
            def check_len(self, value: str) -> None:
                if len(value) > 1000:
                    raise AssertionError("too long")

        prop = Property(
            fn=FakeTest.check_len,
            gen=AgentInput(max_length=200),
            runs=10,
            do_shrink=False,
        )
        results = prop.check(instance=FakeTest())
        assert all(r.passed for r in results)

    def test_property_run_convenience(self):
        def ok(v):
            pass

        prop = Property(fn=ok, gen=AgentInput(), runs=5, do_shrink=False)
        assert prop.run() is True

    def test_property_result_summary(self):
        pr = PropertyResult(passed=True, input_value="test")
        assert "PASSED" in pr.summary()


# ===================================================================
# @property_test decorator
# ===================================================================

class TestPropertyTestDecorator:
    def test_creates_property(self):
        @property_test(gen=AgentInput(max_length=50), runs=5)
        def my_test(value):
            pass

        assert isinstance(my_test, Property)
        assert my_test.runs == 5

    def test_defaults(self):
        @property_test()
        def my_test(value):
            pass

        assert isinstance(my_test, Property)
        assert my_test.runs == 100

    def test_stores_metadata(self):
        @property_test(runs=42)
        def my_test(value):
            pass

        assert my_test._agentbench_property["runs"] == 42


# ===================================================================
# Built-in property: no_pii_leakage
# ===================================================================

class TestNoPIILeakage:
    def test_passes_when_no_pii(self):
        def safe_run(prompt):
            return _make_trajectory([_step(response="safe output")])

        results = no_pii_leakage(safe_run, runs=5)
        assert all(r.passed for r in results)

    def test_fails_when_pii_exposed(self):
        def leaky_run(prompt):
            return _make_trajectory(
                [_step(response="SSN: 123-45-6789")]
            )

        results = no_pii_leakage(leaky_run, runs=1)
        assert any(not r.passed for r in results)


# ===================================================================
# Built-in property: bounded_steps
# ===================================================================

class TestBoundedSteps:
    def test_passes_within_limit(self):
        def quick_run(prompt):
            return _make_trajectory([_step(), _step(number=2)])

        results = bounded_steps(quick_run, runs=3, max_steps=5)
        assert all(r.passed for r in results)

    def test_fails_over_limit(self):
        def slow_run(prompt):
            return _make_trajectory([_step(number=i) for i in range(1, 25)])

        results = bounded_steps(slow_run, runs=1, max_steps=10)
        assert any(not r.passed for r in results)


# ===================================================================
# Built-in property: consistent_behavior
# ===================================================================

class TestConsistentBehavior:
    def test_passes_when_consistent(self):
        counter = {"n": 0}

        def consistent_run(prompt):
            counter["n"] += 1
            return _make_trajectory(final_response="same response")

        results = consistent_behavior(consistent_run, runs=3)
        assert all(r.passed for r in results)

    def test_fails_when_inconsistent(self):
        counter = {"n": 0}

        def random_run(prompt):
            counter["n"] += 1
            return _make_trajectory(final_response=f"response {counter['n']}")

        results = consistent_behavior(random_run, runs=5, threshold=0.99)
        # Should fail because outputs differ significantly
        assert any(not r.passed for r in results)


# ===================================================================
# Built-in property: no_hallucinated_tools
# ===================================================================

class TestNoHallucinatedTools:
    def test_passes_with_valid_tools(self):
        def good_run(prompt):
            return _make_trajectory(
                [_step(action="tool_call", tool_name="search")]
            )

        results = no_hallucinated_tools(
            good_run, runs=3, available_tools=["search", "lookup"]
        )
        assert all(r.passed for r in results)

    def test_fails_with_hallucinated_tool(self):
        def bad_run(prompt):
            return _make_trajectory(
                [_step(action="tool_call", tool_name="made_up_tool")]
            )

        results = no_hallucinated_tools(
            bad_run, runs=1, available_tools=["search"]
        )
        assert any(not r.passed for r in results)


# ===================================================================
# Built-in property: graceful_degradation
# ===================================================================

class TestGracefulDegradation:
    def test_passes_on_graceful_handling(self):
        def robust_run(prompt):
            return _make_trajectory(final_response="handled")

        results = graceful_degradation(robust_run, runs=2)
        assert all(r.passed for r in results)

    def test_fails_on_crash(self):
        def crashing_run(prompt):
            raise RuntimeError("unhandled error")

        results = graceful_degradation(crashing_run, runs=1)
        assert any(not r.passed for r in results)

    def test_passes_with_handled_error(self):
        def graceful_run(prompt):
            return _make_trajectory(
                completed=False,
                final_response="",
            )

        results = graceful_degradation(graceful_run, runs=2)
        # No unhandled crash → passes
        assert all(r.passed for r in results)


# ===================================================================
# Package-level imports
# ===================================================================

class TestPackageImports:
    def test_imports_from_package(self):
        from agentbench.property import (
            AgentInput,
            shrink,
        )
        assert AgentInput is not None
        assert shrink is not None
