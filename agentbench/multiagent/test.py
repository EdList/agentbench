"""Multi-agent test base — ConversationTurn, ConversationResult, Topology, MultiAgentTest."""

from __future__ import annotations

import enum
import inspect
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ConversationTurn:
    """A single turn in a multi-agent conversation."""

    agent_name: str
    message: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "message": self.message,
            "tool_calls": self.tool_calls,
            "timestamp": self.timestamp,
        }


@dataclass
class ConversationResult:
    """Complete result of a multi-agent conversation."""

    turns: list[ConversationTurn] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)
    total_cost: float = 0.0
    duration: float = 0.0
    completed: bool = True
    error: str | None = None

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def agent_names(self) -> list[str]:
        """Return unique agent names in order of first appearance."""
        seen: set[str] = set()
        names: list[str] = []
        for turn in self.turns:
            if turn.agent_name not in seen:
                seen.add(turn.agent_name)
                names.append(turn.agent_name)
        return names

    def turns_by_agent(self, name: str) -> list[ConversationTurn]:
        """Return all turns by a specific agent."""
        return [t for t in self.turns if t.agent_name == name]

    def messages_by_agent(self, name: str) -> list[str]:
        """Return all messages by a specific agent."""
        return [t.message for t in self.turns if t.agent_name == name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "turns": [t.to_dict() for t in self.turns],
            "final_state": self.final_state,
            "total_cost": self.total_cost,
            "duration": self.duration,
            "completed": self.completed,
            "error": self.error,
        }


class Topology(enum.Enum):
    """Defines how agents communicate with each other."""

    MESH = "mesh"  # Every agent can talk to every other agent
    STAR = "star"  # One central agent talks to all others
    RING = "ring"  # Agents pass messages in a ring
    SEQUENTIAL = "sequential"  # Agents speak in fixed order
    CUSTOM = "custom"  # Custom routing defined by user


@dataclass
class _AgentEntry:
    """Internal representation of a registered agent."""

    name: str
    adapter_or_fn: Callable[..., str] | Any
    config: dict[str, Any] | None = None

    def invoke(self, message: str, history: list[ConversationTurn]) -> str:
        """Invoke the agent with a message and conversation history.

        The adapter_or_fn can be:
        - A callable (function) that accepts (message, history) and returns str
        - An object with a .run() method returning a response string
        - An object with a .respond() method returning a response string
        """
        if callable(self.adapter_or_fn) and not hasattr(self.adapter_or_fn, "run"):
            return _call_with_optional_history(self.adapter_or_fn, message, history)
        # Object-style adapter
        if hasattr(self.adapter_or_fn, "run"):
            return self.adapter_or_fn.run(message, history=history)
        if hasattr(self.adapter_or_fn, "respond"):
            return self.adapter_or_fn.respond(message, history=history)
        # Fallback: call it directly
        return str(_call_with_optional_history(self.adapter_or_fn, message, history))


class MultiAgentTest:
    """Base class for writing multi-agent conversation tests.

    Usage::

        class SupportConversationTest(MultiAgentTest):
            def test_customer_gets_help(self):
                self.add_agent("customer", customer_fn)
                self.add_agent("support", support_fn)
                self.set_topology(Topology.SEQUENTIAL)
                result = self.run_conversation("I need help with my order")
                expect_conversation(result).to_complete_within_turns(10)
    """

    # Prevent pytest from trying to collect this class as a test
    __test__ = False

    def __init__(self) -> None:
        self._agents: list[_AgentEntry] = []
        self._topology: Topology = Topology.SEQUENTIAL
        self._custom_routes: dict[str, list[str]] | None = None

    def add_agent(
        self,
        name: str,
        adapter_or_fn: Callable[..., str] | Any,
        config: dict[str, Any] | None = None,
    ) -> MultiAgentTest:
        """Register an agent for the conversation.

        Args:
            name: Unique name for the agent in this conversation.
            adapter_or_fn: A callable, adapter object, or function.
            config: Optional configuration dict for the agent.

        Returns:
            self, for chaining.

        Raises:
            ValueError: If an agent with the same name already exists.
        """
        if any(a.name == name for a in self._agents):
            raise ValueError(f"Agent '{name}' is already registered")
        self._agents.append(_AgentEntry(name=name, adapter_or_fn=adapter_or_fn, config=config))
        return self

    def set_topology(
        self,
        topology: Topology,
        routes: dict[str, list[str]] | None = None,
    ) -> MultiAgentTest:
        """Define the communication topology.

        Args:
            topology: The topology type.
            routes: For CUSTOM topology, a dict mapping agent name to
                list of agent names it can send messages to.

        Returns:
            self, for chaining.
        """
        self._topology = topology
        if topology == Topology.CUSTOM:
            self._custom_routes = routes or {}
        return self

    def run_conversation(
        self,
        initial_message: str,
        max_turns: int = 20,
        stop_condition: Callable[[ConversationResult], bool] | None = None,
    ) -> ConversationResult:
        """Execute a multi-agent conversation.

        Args:
            initial_message: The first message to start the conversation.
            max_turns: Maximum number of turns before stopping.
            stop_condition: Optional callable that returns True when the
                conversation should stop early.

        Returns:
            ConversationResult with all turns and metadata.

        Raises:
            RuntimeError: If no agents are registered.
        """
        if not self._agents:
            raise RuntimeError("No agents registered. Call add_agent() first.")

        start_time = time.time()
        result = ConversationResult()
        result.final_state["registered_agents"] = [agent.name for agent in self._agents]
        current_message = initial_message

        # Determine turn order based on topology
        turn_order = self._get_turn_order()

        turn_count = 0
        while turn_count < max_turns:
            for agent_entry in turn_order:
                if turn_count >= max_turns:
                    break

                try:
                    response = agent_entry.invoke(current_message, result.turns)
                except Exception as exc:
                    result.completed = False
                    result.error = f"Agent '{agent_entry.name}' raised: {exc}"
                    result.duration = time.time() - start_time
                    return result

                turn = ConversationTurn(
                    agent_name=agent_entry.name,
                    message=response,
                )
                result.turns.append(turn)
                current_message = response
                turn_count += 1

                if stop_condition and stop_condition(result):
                    result.duration = time.time() - start_time
                    return result

            # Check if we should stop after a full round (consensus / termination)
            if self._should_stop(result):
                break

        result.duration = time.time() - start_time
        result.completed = True
        return result

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_turn_order(self) -> list[_AgentEntry]:
        """Determine the order in which agents take turns.

        For SEQUENTIAL/RING/STAR/MESH, agents take turns in registration order.
        The topology mainly affects routing (which messages go to whom),
        but the turn order is always the registration order for simplicity.
        """
        if self._topology == Topology.RING:
            return list(self._agents)
        elif self._topology == Topology.STAR:
            # In STAR, the first agent is the hub — it goes first, then others
            if len(self._agents) <= 1:
                return list(self._agents)
            hub = self._agents[0]
            spokes = self._agents[1:]
            return [hub] + spokes
        else:
            # SEQUENTIAL, MESH, CUSTOM — all in registration order
            return list(self._agents)

    def _should_stop(self, result: ConversationResult) -> bool:
        """Heuristic: check if the conversation has naturally concluded.

        Detects when the last few messages from different agents are
        essentially the same (consensus reached).
        """
        if len(result.turns) < 2:
            return False

        # Check last N messages for consensus
        last_msgs = [t.message.strip().lower() for t in result.turns[-3:]]
        if len(last_msgs) >= 2 and len(set(last_msgs)) == 1:
            return True

        # Check for explicit termination signals
        last_msg = result.turns[-1].message.strip().lower()
        termination_signals = [
            "conversation ended",
            "end of discussion",
            "task completed",
            "[end]",
            "[done]",
        ]
        if any(sig in last_msg for sig in termination_signals):
            return True

        return False


def _call_with_optional_history(
    func: Callable[..., str] | Any,
    message: str,
    history: list[ConversationTurn],
) -> str:
    """Call multi-agent functions that accept either (message) or (message, history)."""
    try:
        signature = inspect.signature(func)
    except (TypeError, ValueError):
        return func(message, history)

    params = list(signature.parameters.values())
    if any(param.kind is inspect.Parameter.VAR_POSITIONAL for param in params):
        return func(message, history)

    positional = [
        param
        for param in params
        if param.kind
        in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
    ]
    if len(positional) >= 2:
        return func(message, history)

    keyword_only = {
        param.name
        for param in params
        if param.kind is inspect.Parameter.KEYWORD_ONLY
    }
    if "history" in keyword_only:
        return func(message, history=history)

    return func(message)
