"""LangChain agent adapter — test LangChain agents with AgentBench."""

from __future__ import annotations

import time
from typing import Any

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import (
    AgentStep,
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)

# Try to import BaseCallbackHandler for proper LangChain integration.
# If not installed, we use a plain class as fallback.
try:
    from langchain_core.callbacks import BaseCallbackHandler as _LangChainBase
except ImportError:
    _LangChainBase = object  # type: ignore[assignment, misc]


class _TrajectoryCallback(_LangChainBase):  # type: ignore[misc]
    """LangChain callback handler that records agent steps into a trajectory.

    Inherits from langchain_core.callbacks.BaseCallbackHandler when available,
    so LangChain's callback dispatch system recognizes and calls it properly.
    """

    def __init__(  # type: ignore[no-untyped-def]
        self,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        max_steps: int,
        **kwargs: Any,
    ):
        # Call super().__init__() if we have a real base class
        if hasattr(super(), "__init__"):
            super().__init__(**kwargs)
        self._trajectory = trajectory
        self._failure_injections = failure_injections
        self._latency_injections = latency_injections
        self._max_steps = max_steps
        self._step_start: float | None = None

    def on_llm_start(self, serialized: dict, prompts: list[str], **kwargs: Any) -> None:
        self._step_start = time.time()

    def on_llm_end(self, response: Any, **kwargs: Any) -> None:
        if len(self._trajectory.steps) >= self._max_steps:
            return
        latency = (time.time() - self._step_start) * 1000 if self._step_start else 0
        try:
            content = response.generations[0][0].text
        except (IndexError, AttributeError):
            content = str(response)

        step = AgentStep(
            step_number=len(self._trajectory.steps),
            action="llm_response",
            reasoning=content,
            response=content,
            latency_ms=latency,
        )
        self._trajectory.steps.append(step)

    def on_tool_start(self, serialized: dict, input_str: str, **kwargs: Any) -> None:
        self._step_start = time.time()
        tool_name = serialized.get("name", "unknown")
        # Store tool_input for use in on_tool_end
        self._current_tool_name = tool_name
        self._current_tool_input = kwargs.get("tool_input", None) or (
            {"input": input_str} if input_str else {}
        )
        # Don't reset _injected_failure here — it may have been set by on_agent_action
        # which fires before on_tool_start in LangChain's callback order

        # Apply latency injection
        for inj in self._latency_injections:
            if inj.tool_name == tool_name:
                time.sleep(inj.delay_ms / 1000)

    def on_tool_end(self, output: str, **kwargs: Any) -> None:
        if len(self._trajectory.steps) >= self._max_steps:
            return
        # Skip if failure was injected — error step already recorded in on_agent_action
        if getattr(self, "_injected_failure", False):
            self._injected_failure = False
            return

        latency = (time.time() - self._step_start) * 1000 if self._step_start else 0
        tool_name = getattr(self, "_current_tool_name", None) or kwargs.get("serialized", {}).get(
            "name", "unknown"
        )
        tool_input = getattr(self, "_current_tool_input", None)

        step = AgentStep(
            step_number=len(self._trajectory.steps),
            action="tool_call",
            tool_name=tool_name,
            tool_input=tool_input,
            tool_output=output,
            latency_ms=latency,
        )
        self._trajectory.steps.append(step)

    def on_tool_error(self, error: Exception | str, **kwargs: Any) -> None:
        latency = (time.time() - self._step_start) * 1000 if self._step_start else 0
        serialized = kwargs.get("serialized", {})
        tool_name = serialized.get("name", "unknown")

        step = AgentStep(
            step_number=len(self._trajectory.steps),
            action="error",
            tool_name=tool_name,
            error=str(error),
            latency_ms=latency,
        )
        self._trajectory.steps.append(step)

    def on_agent_action(self, action: Any, **kwargs: Any) -> None:
        """Record agent's decision to use a tool."""
        if len(self._trajectory.steps) >= self._max_steps:
            return
        # Reset injection flag for this tool cycle
        self._injected_failure = False

        try:
            tool_name = action.tool
            tool_input = action.tool_input if hasattr(action, "tool_input") else {}
        except AttributeError:
            return

        # Check failure injection
        error_msg = None
        for inj in self._failure_injections:
            if inj.tool_name == tool_name and inj.fail_times > 0:
                inj.fail_times -= 1
                error_msg = inj.error_message
                break

        if error_msg:
            self._injected_failure = True
            step = AgentStep(
                step_number=len(self._trajectory.steps),
                action="error",
                tool_name=tool_name,
                tool_input=(
                    tool_input if isinstance(tool_input, dict) else {"input": str(tool_input)}
                ),
                error=error_msg,
            )
            self._trajectory.steps.append(step)


class LangChainAdapter(AgentAdapter):
    """Adapter for LangChain agents.

    Wraps a LangChain AgentExecutor and records every step of its execution
    as an AgentBench trajectory.

    Usage:
        from langchain_openai import ChatOpenAI
        from langchain.agents import create_openai_tools_agent

        llm = ChatOpenAI(model="gpt-4o-mini")
        agent = create_openai_tools_agent(llm, tools, prompt)
        agent_executor = AgentExecutor(agent=agent, tools=tools)

        adapter = LangChainAdapter(agent_executor)
        trajectory = adapter.run("What's the weather?", trajectory)
    """

    def __init__(
        self,
        agent_executor: Any,
        tools: list[str] | None = None,
    ):
        self._executor = agent_executor
        self._tools = tools or []

    def get_available_tools(self) -> list[str]:
        """Return tool names from the LangChain agent."""
        if self._tools:
            return self._tools
        try:
            return [t.name for t in self._executor.tools]
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
        """Execute the LangChain agent and record its trajectory."""
        start = time.time()

        try:
            callback = _TrajectoryCallback(
                trajectory=trajectory,
                failure_injections=failure_injections or [],
                latency_injections=latency_injections or [],
                max_steps=max_steps,
            )

            result = self._executor.invoke(
                {"input": prompt},
                config={"callbacks": [callback]},
            )

            trajectory.final_response = result.get("output", str(result))
            trajectory.completed = True
            trajectory.total_latency_ms = (time.time() - start) * 1000

        except Exception as e:
            self._record_step(
                trajectory,
                action="error",
                error=str(e),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(e)
            trajectory.total_latency_ms = (time.time() - start) * 1000

        return trajectory
