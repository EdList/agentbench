"""Metrics collection — cost, latency, token tracking for test runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agentbench.core.test import AgentTrajectory


@dataclass
class RunMetrics:
    """Metrics for a single agent test run."""

    total_steps: int = 0
    total_tool_calls: int = 0
    tool_calls_by_name: dict[str, int] = field(default_factory=dict)
    total_latency_ms: float = 0.0
    avg_step_latency_ms: float = 0.0
    max_step_latency_ms: float = 0.0
    total_tokens: int = 0
    estimated_cost_usd: float = 0.0
    errors: int = 0
    retries: int = 0
    completed: bool = False

    def summary(self) -> str:
        """Human-readable summary."""
        return (
            f"Steps: {self.total_steps} | "
            f"Tools: {self.total_tool_calls} | "
            f"Latency: {self.total_latency_ms / 1000:.1f}s | "
            f"Tokens: {self.total_tokens} | "
            f"Cost: ${self.estimated_cost_usd:.4f} | "
            f"Errors: {self.errors}"
        )


class MetricsCollector:
    """Collects and aggregates metrics from agent trajectories."""

    def __init__(self) -> None:
        self._runs: list[RunMetrics] = []

    def collect(self, trajectory: AgentTrajectory) -> RunMetrics:
        """Extract metrics from a trajectory."""
        metrics = RunMetrics()

        # Steps
        metrics.total_steps = trajectory.step_count
        metrics.completed = trajectory.completed

        # Tool calls
        tool_calls = trajectory.tool_calls
        metrics.total_tool_calls = len(tool_calls)
        for call in tool_calls:
            name = call.tool_name or "unknown"
            metrics.tool_calls_by_name[name] = metrics.tool_calls_by_name.get(name, 0) + 1

        # Latency
        if trajectory.steps:
            latencies = [s.latency_ms for s in trajectory.steps if s.latency_ms > 0]
            metrics.total_latency_ms = trajectory.total_latency_ms or sum(latencies)
            metrics.avg_step_latency_ms = (
                sum(latencies) / len(latencies) if latencies else 0
            )
            metrics.max_step_latency_ms = max(latencies) if latencies else 0

        # Tokens and cost
        metrics.total_tokens = trajectory.total_tokens
        metrics.estimated_cost_usd = trajectory.total_cost_usd

        # Errors and retries
        metrics.errors = sum(1 for s in trajectory.steps if s.action == "error")
        metrics.retries = sum(1 for s in trajectory.steps if s.action == "retry")

        self._runs.append(metrics)
        return metrics

    def aggregate(self) -> dict[str, Any]:
        """Aggregate metrics across all collected runs."""
        if not self._runs:
            return {}

        return {
            "total_runs": len(self._runs),
            "total_steps": sum(r.total_steps for r in self._runs),
            "avg_steps": sum(r.total_steps for r in self._runs) / len(self._runs),
            "total_latency_ms": sum(r.total_latency_ms for r in self._runs),
            "avg_latency_ms": sum(r.total_latency_ms for r in self._runs) / len(self._runs),
            "total_tokens": sum(r.total_tokens for r in self._runs),
            "total_cost_usd": sum(r.estimated_cost_usd for r in self._runs),
            "total_errors": sum(r.errors for r in self._runs),
            "success_rate": sum(1 for r in self._runs if r.completed) / len(self._runs),
        }
