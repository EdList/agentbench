"""Multi-agent conversation assertions — fluent expect_conversation() chain."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentbench.multiagent.test import ConversationResult


@dataclass
class ConversationAssertionResult:
    """Result of a single conversation-level assertion."""

    passed: bool
    message: str
    assertion_type: str
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        icon = "✓" if self.passed else "✗"
        return f"{icon} {self.message}"

    def __bool__(self) -> bool:
        return self.passed


class ConversationExpectation:
    """Fluent assertion builder for multi-agent conversation results.

    Usage::

        result = test.run_conversation("Hello")
        expect_conversation(result) \\
            .to_complete_within_turns(10) \\
            .to_have_agent_speak("Alice", min_times=1) \\
            .every_agent_responds()
    """

    def __init__(
        self,
        result: ConversationResult,
        *,
        expected_agents: list[str] | None = None,
    ) -> None:
        if result is None:
            raise ValueError(
                "expect_conversation() received None instead of a ConversationResult.\n"
                "  Expected: A valid ConversationResult object.\n"
                "  Suggested fix: Pass the return value of run_conversation()."
            )
        self._result = result
        self._expected_agents = expected_agents
        self._results: list[ConversationAssertionResult] = []

    @property
    def results(self) -> list[ConversationAssertionResult]:
        return self._results

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self._results)

    def _add_result(
        self,
        passed: bool,
        message: str,
        assertion_type: str,
        **details: Any,
    ) -> ConversationExpectation:
        """Record an assertion result and return self for chaining."""
        self._results.append(
            ConversationAssertionResult(
                passed=passed,
                message=message,
                assertion_type=assertion_type,
                details=details,
            )
        )
        return self

    # ------------------------------------------------------------------
    # Assertions
    # ------------------------------------------------------------------

    def to_complete_within_turns(self, n: int) -> ConversationExpectation:
        """Assert the conversation finished within N turns."""
        actual = self._result.turn_count
        passed = actual <= n

        if passed:
            message = f"Conversation completed in {actual} turns (limit: {n})"
        else:
            message = (
                f"Conversation exceeded turn limit.\n"
                f"  Expected: ≤ {n} turns.\n"
                f"  Actual: {actual} turns.\n"
                f"  Suggested fix: Increase max_turns or optimize agent responses."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="turn_limit",
            actual_turns=actual,
            max_turns=n,
        )

    def to_have_agent_speak(self, name: str, min_times: int = 1) -> ConversationExpectation:
        """Assert that a specific agent participated at least min_times."""
        agent_turns = self._result.turns_by_agent(name)
        count = len(agent_turns)
        passed = count >= min_times

        if passed:
            message = f"Agent '{name}' spoke {count} time(s) (min: {min_times})"
        else:
            message = (
                f"Agent '{name}' did not speak enough times.\n"
                f"  Expected: ≥ {min_times} turns from '{name}'.\n"
                f"  Actual: {count} turns.\n"
                f"  Agents that spoke: {self._result.agent_names}.\n"
                f"  Suggested fix: Ensure agent '{name}' is included in the turn order."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="agent_spoke",
            agent_name=name,
            actual_count=count,
            min_times=min_times,
        )

    def to_reach_consensus(self) -> ConversationExpectation:
        """Assert all agents reached consensus (final messages are similar)."""
        turns = self._result.turns
        if not turns:
            return self._add_result(
                passed=False,
                message="Cannot check consensus: conversation has no turns.",
                assertion_type="consensus",
            )

        # Check if the last message from each agent is essentially the same
        agents = self._result.agent_names
        if len(agents) < 2:
            return self._add_result(
                passed=True,
                message="Only one agent — consensus trivially reached.",
                assertion_type="consensus",
            )

        # Get the last message from each agent
        last_messages: dict[str, str] = {}
        for turn in reversed(turns):
            if turn.agent_name not in last_messages:
                last_messages[turn.agent_name] = turn.message.strip().lower()

        # Normalize and compare
        messages = list(last_messages.values())
        # Check if all final messages contain similar content
        # Simple heuristic: check if the last message from each agent agrees
        # We look for agreement keywords or exact match
        all_same = len(set(messages)) == 1

        # Also check for explicit agreement signals
        agreement_signals = ["agree", "consensus", "yes", "correct", "confirmed"]
        all_agree = all(any(signal in msg for signal in agreement_signals) for msg in messages)

        passed = all_same or all_agree

        if passed:
            message = "All agents reached consensus."
        else:
            message = (
                f"Agents did not reach consensus.\n"
                f"  Final messages per agent: {last_messages}.\n"
                f"  Suggested fix: Allow more turns or adjust agent prompts to converge."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="consensus",
            final_messages={k: v[:100] for k, v in last_messages.items()},
        )

    def to_not_loop(self, max_repeated: int = 3) -> ConversationExpectation:
        """Assert the conversation doesn't loop (no message repeated > max_repeated times)."""
        messages: dict[str, int] = {}
        loop_found = False
        loop_message = ""

        for turn in self._result.turns:
            key = turn.message.strip().lower()
            messages[key] = messages.get(key, 0) + 1
            if messages[key] > max_repeated:
                loop_found = True
                loop_message = turn.message[:100]
                break

        passed = not loop_found

        if passed:
            message = f"No looping detected (max repeated: {max_repeated})"
        else:
            message = (
                f"Conversation appears to be looping.\n"
                f"  A message was repeated more than {max_repeated} times.\n"
                f"  Repeated message: '{loop_message}...'\n"
                f"  Suggested fix: Add termination conditions or improve agent prompts."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="no_loop",
            max_repeated=max_repeated,
        )

    def to_follow_protocol(self, steps: list[str]) -> ConversationExpectation:
        """Assert the conversation follows an expected agent-speaking order.

        Args:
            steps: List of expected agent names in order.
                E.g., ["moderator", "agent_a", "agent_b", "moderator"]
        """
        actual_order = [t.agent_name for t in self._result.turns]

        # Check if the steps appear as a subsequence
        try:
            step_idx = 0
            for actual in actual_order:
                if step_idx < len(steps) and actual == steps[step_idx]:
                    step_idx += 1
            passed = step_idx == len(steps)
        except Exception:
            passed = False

        if passed:
            message = f"Conversation followed expected protocol: {' → '.join(steps)}"
        else:
            message = (
                f"Conversation did not follow expected protocol.\n"
                f"  Expected order: {' → '.join(steps)}\n"
                f"  Actual order: {' → '.join(actual_order[:20])}"
                f"{'...' if len(actual_order) > 20 else ''}\n"
                f"  Suggested fix: Ensure topology and patterns match the expected protocol."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="protocol",
            expected_steps=steps,
            actual_order=actual_order[:20],
        )

    def every_agent_responds(self) -> ConversationExpectation:
        """Assert every registered agent spoke at least once."""
        agents = (
            self._expected_agents
            if self._expected_agents is not None
            else self._result.agent_names
        )
        turns_per_agent: dict[str, int] = {}
        for turn in self._result.turns:
            turns_per_agent[turn.agent_name] = turns_per_agent.get(turn.agent_name, 0) + 1

        silent_agents = [name for name in agents if turns_per_agent.get(name, 0) == 0]

        # All agents that appear in the conversation should have spoken
        # This is trivially true since agent_names only includes agents that spoke
        # But we also check that no agent has 0 turns (shouldn't happen with proper impl)
        passed = len(silent_agents) == 0

        if passed:
            counts = ", ".join(f"{k}: {v}" for k, v in sorted(turns_per_agent.items()))
            message = f"Every agent responded. ({counts})"
        else:
            message = (
                f"Some agents did not respond: {silent_agents}.\n"
                f"  Turn counts: {turns_per_agent}\n"
                f"  Suggested fix: Ensure all agents are included in the turn order."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="every_agent_responds",
            silent_agents=silent_agents,
            turn_counts=turns_per_agent,
        )

    def no_agent_dominates(self, max_fraction: float = 0.5) -> ConversationExpectation:
        """Assert no single agent dominates the conversation.

        Args:
            max_fraction: Maximum fraction of turns any single agent can have.
                Default 0.5 means no agent can have more than 50% of all turns.
        """
        if not self._result.turns:
            return self._add_result(
                passed=True,
                message="No turns to analyze — trivially balanced.",
                assertion_type="no_domination",
            )

        total = self._result.turn_count
        turns_per_agent: dict[str, int] = {}
        for turn in self._result.turns:
            turns_per_agent[turn.agent_name] = turns_per_agent.get(turn.agent_name, 0) + 1

        dominating = [
            (name, count) for name, count in turns_per_agent.items() if count / total > max_fraction
        ]

        passed = len(dominating) == 0

        if passed:
            message = (
                f"No agent dominates (max_fraction={max_fraction}). Distribution: {turns_per_agent}"
            )
        else:
            names = [
                f"'{name}' ({count}/{total} = {count / total:.0%})" for name, count in dominating
            ]
            message = (
                f"Agent(s) dominate the conversation: {', '.join(names)}.\n"
                f"  Max allowed fraction: {max_fraction:.0%}\n"
                f"  Full distribution: {turns_per_agent}\n"
                f"  Suggested fix: Adjust turn order or reduce max_turns."
            )

        return self._add_result(
            passed=passed,
            message=message,
            assertion_type="no_domination",
            max_fraction=max_fraction,
            dominating=dominating,
            distribution=turns_per_agent,
        )


def expect_conversation(
    result: ConversationResult,
    *,
    expected_agents: list[str] | None = None,
) -> ConversationExpectation:
    """Create an expectation chain for asserting on a multi-agent conversation.

    Usage::

        result = test.run_conversation("Hello")
        expect_conversation(result, expected_agents=["Alice", "Bob"]) \\
            .to_complete_within_turns(10) \\
            .to_have_agent_speak("Alice") \\
            .every_agent_responds()
    """
    return ConversationExpectation(result, expected_agents=expected_agents)
