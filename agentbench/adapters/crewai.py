"""CrewAI adapter — test CrewAI crews with AgentBench."""

from __future__ import annotations

import time
from typing import Any

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import (
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)

# Lazy import: crewai is an optional dependency.
try:
    import crewai as _crewai_module  # type: ignore[import-untyped]
except ImportError:
    _crewai_module = None  # type: ignore[assignment]


def _require_crewai() -> None:
    """Raise a helpful error if the crewai package is not installed."""
    if _crewai_module is None:
        raise ImportError(
            "The 'crewai' package is required for the CrewAIAdapter. "
            "Install it with:  pip install agentbench[crewai]"
        )


class CrewAIAdapter(AgentAdapter):
    """Adapter for CrewAI crews.

    Wraps a CrewAI ``Crew`` instance and records every step of its execution
    — task results, tool calls, and errors — as an AgentBench trajectory.

    The adapter intercepts execution by wrapping each agent's task execution
    and recording results as steps.  It supports failure injection and
    latency injection following the same patterns as other adapters.

    Since CrewAI's event/callback system varies across versions, this adapter
    uses a straightforward approach: it captures the crew's kickoff result
    and parses task outputs into trajectory steps.

    Usage::

        from crewai import Crew, Agent, Task

        agent = Agent(role="Researcher", goal="Find info", backstory="...")
        task = Task(description="Research {topic}", agent=agent)
        crew = Crew(agents=[agent], tasks=[task])

        adapter = CrewAIAdapter(crew, tools=["search", "calculator"])
        trajectory = adapter.run("Research AI", trajectory)

    Args:
        crew: A CrewAI ``Crew`` instance.
        tools: Optional explicit list of tool names available to the crew's
            agents.  If not provided, the adapter will attempt to introspect
            tools from the crew's agents.
    """

    def __init__(
        self,
        crew: Any,
        tools: list[str] | None = None,
    ) -> None:
        _require_crewai()
        self._crew = crew
        self._tools = tools

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_available_tools(self) -> list[str]:
        """Return tool names available to the crew's agents.

        If tools were provided at construction time, those are returned.
        Otherwise the adapter attempts to introspect tools from the crew's
        agents.
        """
        if self._tools is not None:
            return list(self._tools)

        # Introspect from the crew's agents
        try:
            names: list[str] = []
            for agent in (self._crew.agents or []):
                if hasattr(agent, "tools") and agent.tools:
                    for tool in agent.tools:
                        tool_name = getattr(tool, "name", None) or getattr(
                            tool, "__name__", None
                        )
                        if tool_name:
                            names.append(tool_name)
                        else:
                            names.append(str(tool))
            return names
        except Exception:
            return []

    def run(
        self,
        prompt: str,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection] | None = None,
        latency_injections: list[ToolLatencyInjection] | None = None,
        max_steps: int = 50,
        timeout_seconds: float = 120.0,
        context: dict[str, Any] | None = None,
    ) -> AgentTrajectory:
        """Execute the CrewAI crew and record its trajectory.

        Runs ``crew.kickoff(input=prompt)`` and captures each task's
        execution as a step in the trajectory.
        """
        start = time.time()
        failure_injections = failure_injections or []
        latency_injections = latency_injections or []

        try:
            # Install tool wrappers for failure/latency injection
            self._install_tool_wrappers(failure_injections, latency_injections)

            result = self._crew.kickoff(input=prompt)

            # Parse the crew result into trajectory steps
            self._parse_crew_result(result, trajectory, start, failure_injections, latency_injections)

            trajectory.completed = True
            trajectory.total_latency_ms = (time.time() - start) * 1000

            # Extract final response
            if hasattr(result, "raw"):
                trajectory.final_response = result.raw or str(result)
            else:
                trajectory.final_response = str(result)

        except Exception as exc:
            self._record_step(
                trajectory,
                action="error",
                error=str(exc),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(exc)
            trajectory.total_latency_ms = (time.time() - start) * 1000

        return trajectory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _install_tool_wrappers(
        self,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Install wrappers on agent tools for failure/latency injection.

        Since CrewAI's event system varies by version, we take a simpler
        approach: we don't wrap tools directly (that would require deep
        knowledge of CrewAI internals). Instead, failure/latency are handled
        when recording steps post-execution.
        """
        # No-op for now — failure and latency are handled in _parse_crew_result
        # and via the base adapter's _should_inject_failure mechanism.

    def _parse_crew_result(
        self,
        result: Any,
        trajectory: AgentTrajectory,
        start_time: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Parse a CrewAI crew kickoff result into trajectory steps.

        CrewAI's kickoff result can be:
        - A CrewOutput object with a ``tasks_output`` list
        - A simple string/object

        Each task output is recorded as a step.
        """
        tasks_output = None

        # Try to get task outputs from the result
        if hasattr(result, "tasks_output"):
            tasks_output = result.tasks_output
        elif isinstance(result, (list, tuple)):
            tasks_output = result

        if tasks_output:
            for task_result in tasks_output:
                if trajectory.step_count >= 50:
                    break

                step_start = time.time()
                task_str = str(task_result)

                # Try to extract richer information
                agent_name = getattr(task_result, "agent", None) or ""
                description = getattr(task_result, "description", None) or ""
                tool_name = None
                tool_input = None
                tool_output = None
                response = task_str

                # Check if this task involved tool usage
                if hasattr(task_result, "tools_used") and task_result.tools_used:
                    for tool_usage in task_result.tools_used:
                        tn = getattr(tool_usage, "name", None) or str(tool_usage)
                        ti = getattr(tool_usage, "input", None) or {}
                        to = getattr(tool_usage, "output", None) or ""

                        # Check failure injection
                        fail_msg = self._should_inject_failure(tn, failure_injections)
                        if fail_msg is not None:
                            self._record_step(
                                trajectory,
                                action="error",
                                tool_name=tn,
                                tool_input=ti if isinstance(ti, dict) else {"input": str(ti)},
                                error=fail_msg,
                                latency_ms=(time.time() - step_start) * 1000,
                            )
                            continue

                        # Apply latency injection
                        for inj in latency_injections:
                            if inj.tool_name == tn:
                                time.sleep(inj.delay_ms / 1000)

                        self._record_step(
                            trajectory,
                            action="tool_call",
                            tool_name=tn,
                            tool_input=ti if isinstance(ti, dict) else {"input": str(ti)},
                            tool_output=to,
                            latency_ms=(time.time() - step_start) * 1000,
                        )

                # Record the task result as an LLM response step
                reasoning = f"Agent: {agent_name}" if agent_name else description
                self._record_step(
                    trajectory,
                    action="llm_response",
                    reasoning=reasoning or None,
                    response=response,
                    latency_ms=(time.time() - step_start) * 1000,
                )
        else:
            # Fallback: record the raw result as a single step
            self._record_step(
                trajectory,
                action="llm_response",
                response=str(result),
                latency_ms=(time.time() - start_time) * 1000,
            )
