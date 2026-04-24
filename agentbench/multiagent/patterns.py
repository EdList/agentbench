"""Conversation patterns — reusable multi-agent conversation strategies."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable

from agentbench.multiagent.test import (
    ConversationResult,
    ConversationTurn,
    _AgentEntry,
)


class ConversationPattern(ABC):
    """Abstract base for conversation patterns."""

    @abstractmethod
    def run(
        self,
        agents: list[_AgentEntry],
        initial_message: str,
        max_turns: int = 20,
    ) -> ConversationResult:
        """Execute the pattern and return a ConversationResult."""
        ...


def _invoke_agent(agent: _AgentEntry, message: str, history: list[ConversationTurn]) -> str:
    """Safely invoke an agent, returning its response string."""
    return agent.invoke(message, history)


class RoundRobin(ConversationPattern):
    """Agents take turns in strict order, round after round.

    Example with agents [A, B, C]::

        Turn 1: A speaks
        Turn 2: B speaks
        Turn 3: C speaks
        Turn 4: A speaks again
        ...

    Args:
        stop_condition: Optional callable receiving ConversationResult,
            returning True to stop early.
    """

    def __init__(
        self,
        stop_condition: Callable[[ConversationResult], bool] | None = None,
    ) -> None:
        self._stop_condition = stop_condition

    def run(
        self,
        agents: list[_AgentEntry],
        initial_message: str,
        max_turns: int = 20,
    ) -> ConversationResult:
        if not agents:
            return ConversationResult(completed=False, error="No agents provided")

        start_time = time.time()
        result = ConversationResult()
        current_message = initial_message
        turn_count = 0

        while turn_count < max_turns:
            for agent in agents:
                if turn_count >= max_turns:
                    break

                response = _invoke_agent(agent, current_message, result.turns)
                turn = ConversationTurn(agent_name=agent.name, message=response)
                result.turns.append(turn)
                current_message = response
                turn_count += 1

                if self._stop_condition and self._stop_condition(result):
                    result.duration = time.time() - start_time
                    result.completed = True
                    return result

        result.duration = time.time() - start_time
        result.completed = True
        return result


class Moderated(ConversationPattern):
    """One agent moderates the conversation, routing to others.

    The moderator (first agent) decides who speaks next by mentioning
    their name. If the moderator's response contains another agent's name,
    that agent responds next. Otherwise the moderator speaks again.

    Args:
        moderator_index: Index of the moderator agent (default 0).
        max_turns: Maximum total turns.
    """

    def __init__(self, moderator_index: int = 0) -> None:
        self._moderator_index = moderator_index

    def run(
        self,
        agents: list[_AgentEntry],
        initial_message: str,
        max_turns: int = 20,
    ) -> ConversationResult:
        if not agents:
            return ConversationResult(completed=False, error="No agents provided")

        if len(agents) < 2:
            return ConversationResult(
                completed=False, error="Need at least 2 agents for moderated pattern"
            )

        start_time = time.time()
        result = ConversationResult()
        current_message = initial_message
        turn_count = 0

        # Build name -> agent mapping (excluding moderator)
        agent_map: dict[str, _AgentEntry] = {}
        for i, agent in enumerate(agents):
            if i != self._moderator_index:
                agent_map[agent.name.lower()] = agent

        moderator = agents[self._moderator_index]

        while turn_count < max_turns:
            # Moderator always speaks
            mod_response = _invoke_agent(moderator, current_message, result.turns)
            result.turns.append(ConversationTurn(agent_name=moderator.name, message=mod_response))
            turn_count += 1
            current_message = mod_response

            # Check if moderator mentions another agent
            response_lower = mod_response.lower()
            next_agent: _AgentEntry | None = None
            for name, agent in agent_map.items():
                if name in response_lower:
                    next_agent = agent
                    break

            if next_agent is not None and turn_count < max_turns:
                agent_response = _invoke_agent(next_agent, current_message, result.turns)
                result.turns.append(
                    ConversationTurn(agent_name=next_agent.name, message=agent_response)
                )
                turn_count += 1
                current_message = agent_response

        result.duration = time.time() - start_time
        result.completed = True
        return result


class Debate(ConversationPattern):
    """Two agents argue about a topic, with a third judging.

    Agents at indices 0 and 1 debate back and forth. Agent at index 2
    (or last agent if fewer than 3) delivers the final judgment.

    Args:
        max_rounds: Number of back-and-forth rounds before the judge speaks.
    """

    def __init__(self, max_rounds: int = 3) -> None:
        self._max_rounds = max_rounds

    def run(
        self,
        agents: list[_AgentEntry],
        initial_message: str,
        max_turns: int = 20,
    ) -> ConversationResult:
        if len(agents) < 2:
            return ConversationResult(
                completed=False, error="Need at least 2 agents for debate pattern"
            )

        start_time = time.time()
        result = ConversationResult()
        current_message = initial_message
        turn_count = 0

        debater_a = agents[0]
        debater_b = agents[1]
        judge = agents[2] if len(agents) > 2 else agents[-1]

        # Debate rounds
        for round_num in range(self._max_rounds):
            if turn_count >= max_turns:
                break

            # Debater A responds
            resp_a = _invoke_agent(debater_a, current_message, result.turns)
            result.turns.append(ConversationTurn(agent_name=debater_a.name, message=resp_a))
            turn_count += 1
            current_message = resp_a

            if turn_count >= max_turns:
                break

            # Debater B responds
            resp_b = _invoke_agent(debater_b, current_message, result.turns)
            result.turns.append(ConversationTurn(agent_name=debater_b.name, message=resp_b))
            turn_count += 1
            current_message = resp_b

        # Judge delivers final verdict
        if turn_count < max_turns:
            # Provide full debate context to judge
            debate_summary = " | ".join(f"{t.agent_name}: {t.message}" for t in result.turns)
            judge_prompt = (
                f"Here is the debate so far: [{debate_summary}]\n\n"
                f"Please provide your final judgment."
            )
            judge_response = _invoke_agent(judge, judge_prompt, result.turns)
            result.turns.append(ConversationTurn(agent_name=judge.name, message=judge_response))
            result.final_state["judgment"] = judge_response
            turn_count += 1

        result.duration = time.time() - start_time
        result.completed = True
        return result


class Pipeline(ConversationPattern):
    """Sequential pipeline: A processes, then B, then C, etc.

    Each agent receives the output of the previous agent. This is useful
    for testing multi-stage processing workflows.

    Args:
        rounds: Number of times to run through the full pipeline.
    """

    def __init__(self, rounds: int = 1) -> None:
        self._rounds = rounds

    def run(
        self,
        agents: list[_AgentEntry],
        initial_message: str,
        max_turns: int = 20,
    ) -> ConversationResult:
        if not agents:
            return ConversationResult(completed=False, error="No agents provided")

        start_time = time.time()
        result = ConversationResult()
        current_message = initial_message
        turn_count = 0

        for _round in range(self._rounds):
            for agent in agents:
                if turn_count >= max_turns:
                    break

                response = _invoke_agent(agent, current_message, result.turns)
                result.turns.append(ConversationTurn(agent_name=agent.name, message=response))
                current_message = response
                turn_count += 1

        result.duration = time.time() - start_time
        result.completed = True
        return result
