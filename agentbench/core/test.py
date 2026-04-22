"""Agent test base class — the foundation for writing behavioral agent tests."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from agentbench.core.assertions import Expectation, StepAssertion
    from agentbench.adapters.base import AgentAdapter

from agentbench.core.config import AgentBenchConfig


@dataclass
class AgentStep:
    """A single step in an agent's execution trajectory."""

    step_number: int
    action: str  # "tool_call", "llm_response", "error", "retry"
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_output: Any = None
    reasoning: str | None = None
    response: str | None = None
    latency_ms: float = 0.0
    error: str | None = None
    timestamp: float = field(default_factory=time.time)

    @property
    def exposed_data(self) -> str:
        """Return all text data exposed in this step (for PII checks)."""
        parts = []
        if self.response:
            parts.append(self.response)
        if self.tool_output:
            parts.append(str(self.tool_output))
        if self.tool_input:
            parts.append(str(self.tool_input))
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number": self.step_number,
            "action": self.action,
            "tool_name": self.tool_name,
            "tool_input": self.tool_input,
            "tool_output": str(self.tool_output) if self.tool_output else None,
            "reasoning": self.reasoning,
            "response": self.response,
            "latency_ms": self.latency_ms,
            "error": self.error,
            "timestamp": self.timestamp,
        }


@dataclass
class AgentTrajectory:
    """Complete execution trajectory of an agent run."""

    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    test_name: str = ""
    agent_name: str = ""
    input_prompt: str = ""
    steps: list[AgentStep] = field(default_factory=list)
    final_response: str = ""
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    completed: bool = False
    error: str | None = None
    config_overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def tool_calls(self) -> list[AgentStep]:
        return [s for s in self.steps if s.action == "tool_call"]

    def tool_calls_by_name(self, name: str) -> list[AgentStep]:
        return [s for s in self.tool_calls if s.tool_name == name]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "test_name": self.test_name,
            "agent_name": self.agent_name,
            "input_prompt": self.input_prompt,
            "steps": [s.to_dict() for s in self.steps],
            "final_response": self.final_response,
            "total_latency_ms": self.total_latency_ms,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "completed": self.completed,
            "error": self.error,
        }


@dataclass
class ToolFailureInjection:
    """Configuration for injecting tool failures during a test."""

    tool_name: str
    fail_times: int = 1
    error_message: str = "Tool unavailable"
    error_type: str = "connection_error"


@dataclass
class ToolLatencyInjection:
    """Configuration for injecting latency into tool calls."""

    tool_name: str
    delay_ms: int = 1000


class AgentTest:
    """Base class for writing behavioral agent tests.

    Usage:
        class CheckoutTest(AgentTest):
            agent = "my-checkout-agent"

            def test_completes_checkout(self):
                result = self.run("Buy me a blue shirt, size M")
                expect(result).to_complete_within(steps=10)
                expect(result).to_use_tool("payment_api", times=1)
    """

    # Subclasses override these
    agent: str = ""
    adapter: AgentAdapter | None = None
    config: AgentBenchConfig | None = None

    # Internal state
    _trajectory: AgentTrajectory | None = None
    _failure_injections: list[ToolFailureInjection] = []
    _latency_injections: list[ToolLatencyInjection] = []

    def run(
        self,
        prompt: str,
        *,
        inject_tool_failure: str | ToolFailureInjection | None = None,
        fail_times: int = 1,
        inject_latency: str | ToolLatencyInjection | None = None,
        max_steps: int | None = None,
        timeout_seconds: float | None = None,
        context: dict[str, Any] | None = None,
    ) -> AgentTrajectory:
        """Run the agent with a prompt and return the full trajectory.

        Args:
            prompt: The user input to send to the agent.
            inject_tool_failure: Tool name or failure config to inject failures.
            fail_times: How many times the tool should fail.
            inject_latency: Tool name or latency config to inject delays.
            max_steps: Maximum number of agent steps before stopping.
            timeout_seconds: Maximum wall time for the agent run.
            context: Additional context to pass to the agent.

        Returns:
            AgentTrajectory with every step recorded.
        """
        if self.adapter is None:
            raise RuntimeError(
                f"No adapter configured for agent '{self.agent}'. "
                "Set self.adapter or use agentbench init to scaffold tests."
            )

        # Configure failure injection
        self._failure_injections.clear()
        if inject_tool_failure:
            if isinstance(inject_tool_failure, str):
                self._failure_injections.append(
                    ToolFailureInjection(
                        tool_name=inject_tool_failure, fail_times=fail_times
                    )
                )
            else:
                self._failure_injections.append(inject_tool_failure)

        # Configure latency injection
        self._latency_injections.clear()
        if inject_latency:
            if isinstance(inject_latency, str):
                self._latency_injections.append(ToolLatencyInjection(tool_name=inject_latency))
            else:
                self._latency_injections.append(inject_latency)

        # Build trajectory
        self._trajectory = AgentTrajectory(
            agent_name=self.agent,
            input_prompt=prompt,
        )

        # Execute through adapter
        start_time = time.time()
        try:
            self._trajectory = self.adapter.run(
                prompt=prompt,
                trajectory=self._trajectory,
                failure_injections=self._failure_injections,
                latency_injections=self._latency_injections,
                max_steps=max_steps or self._get_config().max_steps,
                timeout_seconds=timeout_seconds or self._get_config().timeout_seconds,
                context=context,
            )
        except Exception as e:
            self._trajectory.completed = False
            self._trajectory.error = str(e)
        finally:
            self._trajectory.total_latency_ms = (time.time() - start_time) * 1000

        return self._trajectory

    def _get_config(self) -> AgentBenchConfig:
        """Get the test configuration."""
        if self.config:
            return self.config
        return AgentBenchConfig()

    @property
    def trajectory(self) -> AgentTrajectory | None:
        """The trajectory from the most recent run."""
        return self._trajectory
