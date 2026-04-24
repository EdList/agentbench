"""Tests for the multi-agent test harness module."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentbench.multiagent import (
    ConversationResult,
    ConversationTurn,
    Customer,
    Debate,
    Expert,
    Manager,
    Moderated,
    MultiAgentTest,
    Pipeline,
    Role,
    RolePlay,
    RoundRobin,
    Skeptic,
    SupportAgent,
    Topology,
    expect_conversation,
)
from agentbench.multiagent.test import _AgentEntry

# ── ConversationTurn ──────────────────────────────────────────────────


class TestConversationTurn:
    def test_creation(self):
        turn = ConversationTurn(agent_name="Alice", message="Hello")
        assert turn.agent_name == "Alice"
        assert turn.message == "Hello"
        assert turn.tool_calls == []
        assert isinstance(turn.timestamp, float)

    def test_with_tool_calls(self):
        turn = ConversationTurn(
            agent_name="Bot",
            message="I searched",
            tool_calls=[{"name": "search", "args": {"q": "test"}}],
        )
        assert len(turn.tool_calls) == 1
        assert turn.tool_calls[0]["name"] == "search"

    def test_to_dict(self):
        turn = ConversationTurn(agent_name="A", message="hi")
        d = turn.to_dict()
        assert d["agent_name"] == "A"
        assert d["message"] == "hi"
        assert "timestamp" in d


# ── ConversationResult ────────────────────────────────────────────────


class TestConversationResult:
    def test_defaults(self):
        result = ConversationResult()
        assert result.turns == []
        assert result.completed is True
        assert result.error is None
        assert result.total_cost == 0.0

    def test_turn_count_property(self):
        r = ConversationResult(turns=[
            ConversationTurn("A", "hi"),
            ConversationTurn("B", "hey"),
            ConversationTurn("A", "bye"),
        ])
        assert r.turn_count == 3

    def test_agent_names_property(self):
        r = ConversationResult(turns=[
            ConversationTurn("Alice", "hi"),
            ConversationTurn("Bob", "hey"),
            ConversationTurn("Alice", "bye"),
        ])
        names = r.agent_names
        assert "Alice" in names
        assert "Bob" in names

    def test_turns_by_agent(self):
        r = ConversationResult(turns=[
            ConversationTurn("A", "1"),
            ConversationTurn("B", "2"),
            ConversationTurn("A", "3"),
        ])
        a_turns = r.turns_by_agent("A")
        assert len(a_turns) == 2

    def test_messages_by_agent(self):
        r = ConversationResult(turns=[
            ConversationTurn("A", "hello"),
            ConversationTurn("B", "world"),
        ])
        assert r.messages_by_agent("A") == ["hello"]

    def test_to_dict(self):
        r = ConversationResult(turns=[ConversationTurn("X", "y")])
        d = r.to_dict()
        assert "turns" in d
        assert len(d["turns"]) == 1

    def test_with_error(self):
        r = ConversationResult(completed=False, error="timeout")
        assert r.completed is False
        assert r.error == "timeout"


# ── Topology ──────────────────────────────────────────────────────────


class TestTopology:
    def test_topology_values(self):
        assert Topology.MESH is not None
        assert Topology.STAR is not None
        assert Topology.RING is not None
        assert Topology.SEQUENTIAL is not None
        assert Topology.CUSTOM is not None

    def test_topology_iteration(self):
        names = [t.name for t in Topology]
        assert "MESH" in names
        assert len(names) == 5


# ── MultiAgentTest ────────────────────────────────────────────────────


class TestMultiAgentTest:
    def test_init(self):
        t = MultiAgentTest()
        assert t._agents == []

    def test_add_agent(self):
        t = MultiAgentTest()
        fn = MagicMock(return_value="response")
        t.add_agent("Bot", fn)
        assert len(t._agents) == 1

    def test_add_agent_duplicate_raises(self):
        t = MultiAgentTest()
        t.add_agent("Bot", MagicMock(return_value="r"))
        with pytest.raises(ValueError, match="already registered"):
            t.add_agent("Bot", MagicMock(return_value="r2"))

    def test_set_topology(self):
        t = MultiAgentTest()
        t.set_topology(Topology.RING)
        assert t._topology == Topology.RING

    def test_run_conversation_no_agents_raises(self):
        t = MultiAgentTest()
        with pytest.raises(RuntimeError, match="No agents"):
            t.run_conversation("start")

    def test_run_conversation_basic(self):
        t = MultiAgentTest()
        t.add_agent("A", MagicMock(return_value="hi"))
        t.add_agent("B", MagicMock(return_value="hey"))
        result = t.run_conversation("start", max_turns=4)
        assert isinstance(result, ConversationResult)
        assert result.turn_count > 0

    def test_run_conversation_with_topology(self):
        t = MultiAgentTest()
        t.add_agent("A", MagicMock(return_value="ok"))
        t.add_agent("B", MagicMock(return_value="done"))
        t.set_topology(Topology.SEQUENTIAL)
        result = t.run_conversation("go", max_turns=2)
        assert isinstance(result, ConversationResult)

    def test_add_agent_chaining(self):
        t = MultiAgentTest()
        result = t.add_agent("A", MagicMock(return_value="x"))
        assert result is t


# ── RoundRobin pattern ────────────────────────────────────────────────


class TestRoundRobin:
    def test_basic(self):
        entries = [
            _AgentEntry("A", MagicMock(return_value="hello from A")),
            _AgentEntry("B", MagicMock(return_value="hello from B")),
        ]
        rr = RoundRobin()
        result = rr.run(entries, "start", max_turns=4)
        assert isinstance(result, ConversationResult)
        assert result.turn_count == 4

    def test_single_agent(self):
        entries = [_AgentEntry("Solo", MagicMock(return_value="reply"))]
        rr = RoundRobin()
        result = rr.run(entries, "start", max_turns=2)
        assert result.turn_count == 2

    def test_empty_agents(self):
        rr = RoundRobin()
        result = rr.run([], "start", max_turns=5)
        assert result.completed is False
        assert result.error is not None

    def test_with_stop_condition(self):
        entries = [
            _AgentEntry("A", MagicMock(return_value="stop now")),
            _AgentEntry("B", MagicMock(return_value="continue")),
        ]
        rr = RoundRobin(stop_condition=lambda r: r.turn_count >= 2)
        result = rr.run(entries, "start", max_turns=10)
        assert result.turn_count == 2


# ── Moderated pattern ─────────────────────────────────────────────────


class TestModerated:
    def test_basic(self):
        entries = [
            _AgentEntry("Mod", MagicMock(return_value="moderator says")),
            _AgentEntry("A", MagicMock(return_value="agent says")),
        ]
        m = Moderated(moderator_index=0)
        result = m.run(entries, "topic", max_turns=6)
        assert isinstance(result, ConversationResult)
        assert result.turn_count > 0


# ── Debate pattern ────────────────────────────────────────────────────


class TestDebate:
    def test_basic(self):
        entries = [
            _AgentEntry("Pro", MagicMock(return_value="pro argument")),
            _AgentEntry("Con", MagicMock(return_value="con argument")),
        ]
        d = Debate(max_rounds=2)
        result = d.run(entries, "topic", max_turns=10)
        assert isinstance(result, ConversationResult)
        assert result.turn_count > 0


# ── Pipeline pattern ──────────────────────────────────────────────────


class TestPipeline:
    def test_basic(self):
        entries = [
            _AgentEntry("Step1", MagicMock(return_value="step1 output")),
            _AgentEntry("Step2", MagicMock(return_value="step2 output")),
        ]
        p = Pipeline(rounds=1)
        result = p.run(entries, "input", max_turns=10)
        assert isinstance(result, ConversationResult)
        assert result.turn_count >= 1


# ── Assertions ────────────────────────────────────────────────────────


def _make_result(turns, completed=True, error=None):
    return ConversationResult(
        turns=turns, completed=completed, error=error
    )


class TestExpectConversation:
    def test_to_complete_within_turns_pass(self):
        r = _make_result([ConversationTurn("A", "hi")] * 3)
        result = expect_conversation(r).to_complete_within_turns(5)
        assert result.all_passed is True

    def test_to_complete_within_turns_fail(self):
        r = _make_result([ConversationTurn("A", "hi")] * 10)
        result = expect_conversation(r).to_complete_within_turns(5)
        assert result.all_passed is False

    def test_to_have_agent_speak(self):
        r = _make_result([
            ConversationTurn("Alice", "hello"),
            ConversationTurn("Bob", "hey"),
        ])
        result = expect_conversation(r).to_have_agent_speak("Alice", min_times=1)
        assert result.all_passed is True

    def test_to_have_agent_speak_fail(self):
        r = _make_result([ConversationTurn("Bob", "hey")])
        result = expect_conversation(r).to_have_agent_speak("Alice", min_times=1)
        assert result.all_passed is False

    def test_to_reach_consensus(self):
        r = _make_result([
            ConversationTurn("A", "I agree with B"),
            ConversationTurn("B", "consensus reached"),
        ])
        result = expect_conversation(r).to_reach_consensus()
        assert isinstance(result.all_passed, bool)

    def test_to_not_loop_pass(self):
        msgs = [ConversationTurn("A", f"msg{i}") for i in range(5)]
        r = _make_result(msgs)
        result = expect_conversation(r).to_not_loop(max_repeated=3)
        assert result.all_passed is True

    def test_to_not_loop_fail(self):
        msgs = [ConversationTurn("A", "same")] * 5
        r = _make_result(msgs)
        result = expect_conversation(r).to_not_loop(max_repeated=3)
        assert result.all_passed is False

    def test_to_follow_protocol(self):
        r = _make_result([
            ConversationTurn("A", "first step greeting"),
            ConversationTurn("B", "second step analysis"),
        ])
        result = expect_conversation(r).to_follow_protocol(["greeting", "analysis"])
        assert isinstance(result.all_passed, bool)

    def test_every_agent_responds(self):
        r = _make_result([
            ConversationTurn("Alice", "hi"),
            ConversationTurn("Bob", "hey"),
        ])
        result = expect_conversation(r).every_agent_responds()
        assert result.all_passed is True

    def test_every_agent_responds_single_agent_passes(self):
        """With only 1 unique agent that spoke, it trivially passes."""
        r = _make_result([
            ConversationTurn("Alice", "hi"),
            ConversationTurn("Alice", "hello again"),
        ])
        result = expect_conversation(r).every_agent_responds()
        assert result.all_passed is True

    def test_no_agent_dominates_pass(self):
        r = _make_result([
            ConversationTurn("A", "1"),
            ConversationTurn("B", "2"),
            ConversationTurn("A", "3"),
            ConversationTurn("B", "4"),
        ])
        result = expect_conversation(r).no_agent_dominates(max_fraction=0.5)
        assert result.all_passed is True

    def test_no_agent_dominates_fail(self):
        r = _make_result([
            ConversationTurn("A", "1"),
            ConversationTurn("A", "2"),
            ConversationTurn("A", "3"),
            ConversationTurn("B", "4"),
        ])
        result = expect_conversation(r).no_agent_dominates(max_fraction=0.5)
        assert result.all_passed is False

    def test_chained_assertions(self):
        r = _make_result([
            ConversationTurn("A", "hello"),
            ConversationTurn("B", "world"),
        ])
        result = (
            expect_conversation(r)
            .to_complete_within_turns(5)
            .every_agent_responds()
            .to_not_loop()
        )
        assert result.all_passed is True

    def test_none_raises(self):
        with pytest.raises(ValueError, match="None"):
            expect_conversation(None)


# ── Role ──────────────────────────────────────────────────────────────


class TestRole:
    def test_creation(self):
        role = Role(name="Bot", system_prompt="You are a bot")
        assert role.name == "Bot"
        assert role.system_prompt == "You are a bot"
        assert role.personality_traits == []
        assert role.temperature == 0.7

    def test_with_traits(self):
        role = Role(name="X", system_prompt="prompt")
        role2 = role.with_traits("friendly", "helpful")
        assert "friendly" in role2.personality_traits
        assert "helpful" in role2.personality_traits
        assert role.personality_traits == []

    def test_with_prompt(self):
        role = Role(name="X", system_prompt="old")
        role2 = role.with_prompt("new prompt")
        assert role2.system_prompt == "new prompt"
        assert role.system_prompt == "old"

    def test_with_tools(self):
        role = Role(name="X", system_prompt="p")
        role2 = role.with_tools("search", "calculator")
        assert "search" in role2.tools

    def test_to_dict(self):
        role = Role(name="Bot", system_prompt="hi", personality_traits=["kind"])
        d = role.to_dict()
        assert d["name"] == "Bot"
        assert d["personality_traits"] == ["kind"]


# ── RolePlay ──────────────────────────────────────────────────────────


class TestRolePlay:
    def test_agent_config(self):
        role = Role(name="A", system_prompt="You are A")
        config = RolePlay.agent_config(role)
        assert config["name"] == "A"
        assert "system_prompt" in config

    def test_create_configs(self):
        roles = [
            Role(name="A", system_prompt="a"),
            Role(name="B", system_prompt="b"),
        ]
        configs = RolePlay.create_configs(roles)
        assert len(configs) == 2

    def test_create_function(self):
        role = Role(name="Greeter", system_prompt="Greet people")
        fn = RolePlay.create_function(role)
        assert callable(fn)
        result = fn("hello")
        assert isinstance(result, str)


# ── Pre-built Roles ───────────────────────────────────────────────────


class TestPrebuiltRoles:
    def test_customer(self):
        assert Customer.name == "Customer"
        assert len(Customer.system_prompt) > 0

    def test_support_agent(self):
        assert SupportAgent.name == "SupportAgent"
        assert len(SupportAgent.system_prompt) > 0

    def test_manager(self):
        assert Manager.name == "Manager"

    def test_expert(self):
        assert Expert.name == "Expert"

    def test_skeptic(self):
        assert Skeptic.name == "Skeptic"

    def test_all_are_roles(self):
        for role in [Customer, SupportAgent, Manager, Expert, Skeptic]:
            assert isinstance(role, Role)
