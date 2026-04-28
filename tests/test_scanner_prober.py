"""Tests for agentbench.scanner.prober — AgentProber, ProbeResult, ProbeSession."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from agentbench.scanner.prober import (
    ALL_CATEGORIES,
    AgentProber,
    ProbeResult,
    ProbeSession,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _echo_agent(prompt: str) -> str:
    """Trivial agent that echoes the prompt back."""
    return f"ECHO: {prompt}"


def _failing_agent(prompt: str) -> str:
    """Agent that always raises."""
    raise RuntimeError("agent crashed")


def _selective_agent(prompt: str) -> str:
    """Agent that returns different answers based on keywords."""
    lower = prompt.lower()
    if "tool" in lower or "can you" in lower:
        return "I have access to search, browser, and code execution tools."
    if "instruction" in lower or "system" in lower:
        return "I'm sorry, I cannot share my instructions."
    if "capital" in lower:
        return "Paris"
    return "Generic response"


# ---------------------------------------------------------------------------
# ProbeResult tests
# ---------------------------------------------------------------------------


class TestProbeResult:
    def test_creation_defaults(self):
        pr = ProbeResult(category="capability", prompt="hi", response="hello")
        assert pr.category == "capability"
        assert pr.prompt == "hi"
        assert pr.response == "hello"
        assert pr.metadata == {}

    def test_probe_id_is_deterministic(self):
        first = ProbeResult(category="capability", prompt="hi", response="hello")
        second = ProbeResult(category="capability", prompt="hi", response="different")
        assert first.probe_id == second.probe_id
        assert first.probe_id.startswith("capability-")
        assert len(first.probe_id.split("-", 1)[1]) == 16

    def test_creation_with_metadata(self):
        meta = {"response_time": 0.5, "status": "ok"}
        pr = ProbeResult(category="safety", prompt="p", response="r", metadata=meta)
        assert pr.metadata["response_time"] == 0.5
        assert pr.metadata["status"] == "ok"

    def test_is_dataclass(self):
        pr = ProbeResult(category="x", prompt="p", response="r")
        assert hasattr(pr, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# ProbeSession tests
# ---------------------------------------------------------------------------


class TestProbeSession:
    def test_empty_session(self):
        s = ProbeSession()
        assert s.results == []
        assert s.agent_info == {}
        assert s.duration == 0.0

    def test_session_with_results(self):
        results = [ProbeResult("cap", "p", "r")]
        s = ProbeSession(results=results, duration=1.5)
        assert len(s.results) == 1
        assert s.duration == 1.5

    def test_session_is_dataclass(self):
        s = ProbeSession()
        assert hasattr(s, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# AgentProber init tests
# ---------------------------------------------------------------------------


class TestAgentProberInit:
    def test_default_categories(self):
        p = AgentProber(_echo_agent)
        assert p.categories == ALL_CATEGORIES

    def test_custom_categories(self):
        p = AgentProber(_echo_agent, categories=["safety", "edge_case"])
        assert p.categories == ["safety", "edge_case"]

    def test_invalid_category_raises(self):
        with pytest.raises(ValueError, match="Unknown probe category"):
            AgentProber(_echo_agent, categories=["nonexistent"])

    def test_categories_are_copied(self):
        cats = ["capability"]
        p = AgentProber(_echo_agent, categories=cats)
        cats.append("safety")
        assert p.categories == ["capability"]

    def test_none_categories_means_all(self):
        p = AgentProber(_echo_agent, categories=None)
        assert p.categories == ALL_CATEGORIES


# ---------------------------------------------------------------------------
# probe_capabilities
# ---------------------------------------------------------------------------


class TestProbeCapabilities:
    def test_returns_list_of_results(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert isinstance(results, list)
        assert all(isinstance(r, ProbeResult) for r in results)

    def test_category_label(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert all(r.category == "capability" for r in results)

    def test_prompt_echoed(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert results[0].response == f"ECHO: {results[0].prompt}"

    def test_metadata_has_response_time(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert "response_time" in results[0].metadata
        assert isinstance(results[0].metadata["response_time"], float)

    def test_prompt_count(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert len(results) >= 8  # at least the original prompts


# ---------------------------------------------------------------------------
# probe_safety
# ---------------------------------------------------------------------------


class TestProbeSafety:
    def test_returns_results(self):
        p = AgentProber(_echo_agent, categories=["safety"])
        results = p.probe_safety()
        assert isinstance(results, list)
        assert len(results) >= 5

    def test_category_label(self):
        p = AgentProber(_echo_agent, categories=["safety"])
        for r in p.probe_safety():
            assert r.category == "safety"

    def test_safety_with_failing_agent(self):
        p = AgentProber(_failing_agent, categories=["safety"])
        results = p.probe_safety()
        for r in results:
            assert "ERROR:" in r.response
            assert r.metadata["status"] == "error"


# ---------------------------------------------------------------------------
# probe_edge_cases
# ---------------------------------------------------------------------------


class TestProbeEdgeCases:
    def test_returns_results(self):
        p = AgentProber(_echo_agent, categories=["edge_case"])
        results = p.probe_edge_cases()
        assert len(results) >= 5

    def test_empty_string_prompt(self):
        p = AgentProber(_echo_agent, categories=["edge_case"])
        results = p.probe_edge_cases()
        prompts = [r.prompt for r in results]
        assert "" in prompts

    def test_long_input_prompt(self):
        p = AgentProber(_echo_agent, categories=["edge_case"])
        results = p.probe_edge_cases()
        long_prompts = [r for r in results if len(r.prompt) >= 5000]
        assert len(long_prompts) >= 1

    def test_unicode_handled(self):
        p = AgentProber(_echo_agent, categories=["edge_case"])
        results = p.probe_edge_cases()
        has_unicode = any(any(ord(c) > 127 for c in r.prompt) for r in results)
        assert has_unicode

    def test_category_label(self):
        p = AgentProber(_echo_agent, categories=["edge_case"])
        for r in p.probe_edge_cases():
            assert r.category == "edge_case"


# ---------------------------------------------------------------------------
# probe_persona
# ---------------------------------------------------------------------------


class TestProbePersona:
    def test_returns_results(self):
        p = AgentProber(_echo_agent, categories=["persona"])
        results = p.probe_persona()
        assert len(results) >= 5

    def test_category_label(self):
        p = AgentProber(_echo_agent, categories=["persona"])
        for r in p.probe_persona():
            assert r.category == "persona"

    def test_selective_agent_resists(self):
        p = AgentProber(_selective_agent, categories=["persona"])
        results = p.probe_persona()
        resistant = [
            r for r in results if any(kw in r.response.lower() for kw in ("sorry", "cannot"))
        ]
        assert len(resistant) >= 1


# ---------------------------------------------------------------------------
# probe_robustness
# ---------------------------------------------------------------------------


class TestProbeRobustness:
    def test_returns_results(self):
        p = AgentProber(_echo_agent, categories=["robustness"])
        results = p.probe_robustness()
        assert len(results) >= 5

    def test_category_label(self):
        p = AgentProber(_echo_agent, categories=["robustness"])
        for r in p.probe_robustness():
            assert r.category == "robustness"

    def test_repeated_prompts_present(self):
        p = AgentProber(_echo_agent, categories=["robustness"])
        results = p.probe_robustness()
        prompts = [r.prompt for r in results]
        # At least one prompt should appear more than once
        from collections import Counter

        counts = Counter(prompts)
        repeated = [p for p, c in counts.items() if c > 1]
        assert len(repeated) >= 1


# ---------------------------------------------------------------------------
# probe_all
# ---------------------------------------------------------------------------


class TestProbeAll:
    def test_full_session(self):
        p = AgentProber(_echo_agent)
        session = p.probe_all()
        assert isinstance(session, ProbeSession)
        assert len(session.results) > 0
        assert session.duration > 0

    def test_total_probe_count_under_300(self):
        p = AgentProber(_echo_agent)
        session = p.probe_all()
        assert len(session.results) <= 300

    def test_all_categories_present(self):
        p = AgentProber(_echo_agent)
        session = p.probe_all()
        cats_in_results = {r.category for r in session.results}
        assert cats_in_results == set(ALL_CATEGORIES)

    def test_partial_categories(self):
        p = AgentProber(_echo_agent, categories=["capability", "safety"])
        session = p.probe_all()
        cats = {r.category for r in session.results}
        assert cats == {"capability", "safety"}

    def test_agent_info_populated(self):
        p = AgentProber(_selective_agent)
        session = p.probe_all()
        info = session.agent_info
        assert "capabilities_detected" in info
        assert "tools_detected" in info
        assert "total_probes" in info
        assert info["total_probes"] == len(session.results)

    def test_agent_info_tools_detected(self):
        p = AgentProber(_selective_agent, categories=["capability"])
        session = p.probe_all()
        assert session.agent_info["tools_detected"] is True

    def test_agent_info_persona_resistant(self):
        p = AgentProber(_selective_agent, categories=["persona"])
        session = p.probe_all()
        assert session.agent_info["persona_compliance"] == "resistant"

    def test_errors_tracked(self):
        p = AgentProber(_failing_agent)
        session = p.probe_all()
        assert session.agent_info["errors"] == len(session.results)


# ---------------------------------------------------------------------------
# Integration / edge-case tests
# ---------------------------------------------------------------------------


class TestIntegration:
    def test_mock_agent_called_for_each_prompt(self):
        mock_fn = MagicMock(return_value="mocked")
        p = AgentProber(mock_fn, categories=["capability"])
        results = p.probe_capabilities()
        assert mock_fn.call_count == len(results)

    def test_status_ok_on_success(self):
        p = AgentProber(_echo_agent, categories=["capability"])
        results = p.probe_capabilities()
        assert all(r.metadata["status"] == "ok" for r in results)

    def test_timing_reasonable(self):
        p = AgentProber(_echo_agent)
        session = p.probe_all()
        assert session.duration < 10.0  # should be very fast with echo agent
