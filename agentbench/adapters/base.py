"""Base agent adapter interface — all framework adapters implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from agentbench.core.test import (
    AgentTrajectory,
    AgentStep,
    ToolFailureInjection,
    ToolLatencyInjection,
)


class AgentAdapter(ABC):
    """Abstract base class for agent adapters.

    Each supported framework (LangChain, CrewAI, OpenAI, etc.) implements
    this interface to translate framework-specific calls into AgentBench
    trajectories.
    """

    @abstractmethod
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
        """Execute the agent and record the full trajectory.

        Args:
            prompt: User input to send to the agent.
            trajectory: Trajectory object to populate with steps.
            failure_injections: Tools to simulate failures for.
            latency_injections: Tools to add artificial delays to.
            max_steps: Maximum number of agent steps.
            timeout_seconds: Maximum execution time.
            context: Additional context for the agent.

        Returns:
            The populated AgentTrajectory with all steps recorded.
        """
        ...

    @abstractmethod
    def get_available_tools(self) -> list[str]:
        """Return list of tool names available to this agent."""
        ...

    def _record_step(
        self,
        trajectory: AgentTrajectory,
        action: str,
        *,
        tool_name: str | None = None,
        tool_input: dict[str, Any] | None = None,
        tool_output: Any = None,
        reasoning: str | None = None,
        response: str | None = None,
        latency_ms: float = 0.0,
        error: str | None = None,
    ) -> AgentStep:
        """Record a step in the trajectory."""
        step = AgentStep(
            step_number=trajectory.step_count,
            action=action,
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=tool_output,
            reasoning=reasoning,
            response=response,
            latency_ms=latency_ms,
            error=error,
        )
        trajectory.steps.append(step)
        return step

    def _should_inject_failure(
        self,
        tool_name: str,
        failure_injections: list[ToolFailureInjection] | None,
    ) -> str | None:
        """Check if a failure should be injected for this tool call."""
        if not failure_injections:
            return None
        for injection in failure_injections:
            if injection.tool_name == tool_name and injection.fail_times > 0:
                injection.fail_times -= 1
                return injection.error_message
        return None
