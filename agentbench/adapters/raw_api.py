"""Raw API adapter — test any agent via HTTP endpoint or Python function."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import AgentTrajectory, ToolFailureInjection, ToolLatencyInjection


class RawAPIAdapter(AgentAdapter):
    """Adapter for agents accessible via HTTP API or Python callable.

    Supports two modes:
    1. HTTP mode: Agent is a web service that accepts POST requests
    2. Function mode: Agent is a Python callable

    Usage (HTTP):
        adapter = RawAPIAdapter(
            endpoint="http://localhost:8000/chat",
            headers={"Authorization": "Bearer xxx"},
        )

    Usage (Function):
        def my_agent(prompt: str) -> dict:
            return {"response": "...", "steps": [...]}

        adapter = RawAPIAdapter(func=my_agent)

    Usage (with tools):
        adapter = RawAPIAdapter(
            endpoint="http://localhost:8000/chat",
            tools=["search", "calculator", "database"],
        )
    """

    def __init__(
        self,
        endpoint: str | None = None,
        headers: dict[str, str] | None = None,
        func: Callable[[str, dict[str, Any] | None], dict[str, Any]] | None = None,
        tools: list[str] | None = None,
        timeout: float = 30.0,
    ):
        if not endpoint and not func:
            raise ValueError("Provide either 'endpoint' (URL) or 'func' (callable)")

        self._endpoint = endpoint
        self._headers = headers or {}
        self._func = func
        self._tools = tools or []
        self._timeout = timeout

    def get_available_tools(self) -> list[str]:
        return self._tools

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
        if self._func:
            return self._run_function(
                prompt, trajectory, context,
                failure_injections, latency_injections,
            )
        return self._run_http(
            prompt, trajectory, failure_injections, latency_injections,
            max_steps, timeout_seconds, context,
        )

    def _run_function(
        self,
        prompt: str,
        trajectory: AgentTrajectory,
        context: dict[str, Any] | None,
        failure_injections: list[ToolFailureInjection] | None = None,
        latency_injections: list[ToolLatencyInjection] | None = None,
    ) -> AgentTrajectory:
        """Run agent via Python callable."""
        start = time.time()
        try:
            result = self._func(prompt, context)

            # If function returns structured data with steps
            if isinstance(result, dict):
                if "steps" in result:
                    for step_data in result["steps"]:
                        # Apply failure injection to matching tool calls
                        tool_name = step_data.get("tool_name", "")
                        action = step_data.get("action", "")

                        # Check failure injection
                        if action == "tool_call" and failure_injections:
                            fail_msg = self._should_inject_failure(tool_name, failure_injections)
                            if fail_msg:
                                step_data = {**step_data, "action": "error", "error": fail_msg}

                        # Check latency injection
                        if action == "tool_call" and latency_injections:
                            for inj in latency_injections:
                                if inj.tool_name == tool_name:
                                    time.sleep(inj.delay_ms / 1000)

                        self._record_step(trajectory, **self._safe_step_kwargs(step_data))

                # If agent returned empty steps list but has a response, record it as a step
                if "steps" in result and not result["steps"] and result.get("response"):
                    self._record_step(
                        trajectory,
                        action="llm_response",
                        response=result["response"],
                        latency_ms=(time.time() - start) * 1000,
                    )
                elif "response" in result and "steps" not in result:
                    self._record_step(
                        trajectory,
                        action="llm_response",
                        response=result["response"],
                        latency_ms=(time.time() - start) * 1000,
                    )
                trajectory.final_response = str(result.get("response", str(result)))
                trajectory.completed = True
            else:
                self._record_step(
                    trajectory,
                    action="llm_response",
                    response=str(result),
                    latency_ms=(time.time() - start) * 1000,
                )
                trajectory.final_response = str(result)
                trajectory.completed = True

        except Exception as e:
            self._record_step(
                trajectory,
                action="error",
                error=str(e),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(e)

        return trajectory

    def _run_http(
        self,
        prompt: str,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection] | None,
        latency_injections: list[ToolLatencyInjection] | None,
        max_steps: int,
        timeout_seconds: float,
        context: dict[str, Any] | None,
    ) -> AgentTrajectory:
        """Run agent via HTTP API."""
        start = time.time()

        try:
            payload = {
                "prompt": prompt,
                "max_steps": max_steps,
                "timeout": timeout_seconds,
                "context": context or {},
                "inject_failures": [
                    {"tool": f.tool_name, "times": f.fail_times, "error": f.error_message}
                    for f in (failure_injections or [])
                ],
                "inject_latency": [
                    {"tool": li.tool_name, "delay_ms": li.delay_ms}
                    for li in (latency_injections or [])
                ],
            }

            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(
                    self._endpoint,
                    json=payload,
                    headers=self._headers,
                )
                response.raise_for_status()
                data = response.json()

            # Parse response into trajectory
            if "steps" in data:
                for step_data in data["steps"]:
                    self._record_step(trajectory, **self._safe_step_kwargs(step_data))

            trajectory.final_response = data.get("response", "")
            trajectory.completed = data.get("completed", True)
            trajectory.total_tokens = data.get("tokens", 0)
            trajectory.total_cost_usd = data.get("cost", 0.0)

        except Exception as e:
            self._record_step(
                trajectory,
                action="error",
                error=str(e),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(e)

        return trajectory
