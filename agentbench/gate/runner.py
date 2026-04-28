"""Gate runner — replay all workflows and aggregate pass/fail.

The gate is the CI layer.  It loads every saved workflow, replays each
against the current agent, diffs the results, and produces a single
pass/fail verdict for the entire suite.

Usage::

    from agentbench.gate.runner import GateRunner

    runner = GateRunner(agent_url="https://my-agent.com/v1/chat/completions")
    result = runner.run()
    if not result.passed:
        sys.exit(1)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from agentbench.recorder.workflow import Workflow
from agentbench.replayer.diff import WorkflowDiffer
from agentbench.replayer.replayer import ReplayEngine
from agentbench.replayer.report import ReplayReport


@dataclass
class WorkflowGateResult:
    """Per-workflow gate result."""

    workflow_name: str
    passed: bool
    score: float
    turn_count: int
    pass_count: int
    fail_count: int
    error: str | None = None
    report: ReplayReport | None = None


@dataclass
class GateResult:
    """Aggregate gate result for all workflows."""

    passed: bool = True
    total_workflows: int = 0
    total_turns: int = 0
    passed_workflows: int = 0
    failed_workflows: int = 0
    workflow_results: list[WorkflowGateResult] = field(default_factory=list)
    started_at: str = ""
    finished_at: str = ""

    @property
    def pass_rate(self) -> float:
        """Fraction of workflows that passed (0-1)."""
        if self.total_workflows == 0:
            return 1.0
        return self.passed_workflows / self.total_workflows


class GateRunner:
    """Run the CI gate: replay all workflows and return an aggregate result.

    Loads workflows from ``.agentbench/workflows/``, replays each one,
    diffs against the original, and collects per-workflow pass/fail.
    """

    def __init__(
        self,
        agent_url: str | None = None,
        agent_format: str = "openai",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        threshold: float = 0.8,
        stop_on_error: bool = False,
        workflows_dir: Path | None = None,
    ) -> None:
        self.agent_url = agent_url
        self.agent_format = agent_format
        self.headers = headers or {}
        self.timeout = timeout
        self.threshold = threshold
        self.stop_on_error = stop_on_error
        self.workflows_dir = workflows_dir

    def run(
        self,
        workflow_names: list[str] | None = None,
    ) -> GateResult:
        """Execute the gate.

        Args:
            workflow_names: Optional list of workflow names to run.
                If None, runs all saved workflows.

        Returns:
            GateResult with aggregate pass/fail and per-workflow details.
        """
        result = GateResult(started_at=datetime.now(UTC).isoformat())

        # Discover workflows
        if workflow_names:
            names = workflow_names
        else:
            listed = Workflow.list_workflows(base_dir=self.workflows_dir)
            names = [n for n, _ in listed]

        result.total_workflows = len(names)

        if not names:
            # No workflows → trivially pass
            result.passed = True
            result.finished_at = datetime.now(UTC).isoformat()
            return result

        # Create engine and differ
        engine = ReplayEngine(
            agent_url=self.agent_url,
            agent_format=self.agent_format,
            headers=self.headers,
            timeout=self.timeout,
            stop_on_error=self.stop_on_error,
        )
        differ = WorkflowDiffer(threshold=self.threshold)

        for name in names:
            wf_result = self._run_workflow(engine, differ, name)
            result.workflow_results.append(wf_result)
            result.total_turns += wf_result.turn_count

            if wf_result.passed:
                result.passed_workflows += 1
            else:
                result.failed_workflows += 1
                result.passed = False

        result.finished_at = datetime.now(UTC).isoformat()
        return result

    def _run_workflow(
        self,
        engine: ReplayEngine,
        differ: WorkflowDiffer,
        workflow_name: str,
    ) -> WorkflowGateResult:
        """Run a single workflow through the gate."""
        try:
            baseline = Workflow.load(workflow_name, base_dir=self.workflows_dir)
        except FileNotFoundError:
            return WorkflowGateResult(
                workflow_name=workflow_name,
                passed=False,
                score=0.0,
                turn_count=0,
                pass_count=0,
                fail_count=0,
                error=f"Workflow '{workflow_name}' not found",
            )

        try:
            replayed = engine.replay(baseline)
        except Exception as exc:  # noqa: BLE001
            return WorkflowGateResult(
                workflow_name=workflow_name,
                passed=False,
                score=0.0,
                turn_count=baseline.turn_count,
                pass_count=0,
                fail_count=baseline.turn_count,
                error=f"Replay failed: {exc}",
            )

        diff_result = differ.diff_turns(baseline.turns, replayed.turns)

        report = ReplayReport.from_diff(
            workflow_name=replayed.name,
            replay_of=baseline.name,
            diff_result=diff_result,
            original_responses=[t.agent_response for t in baseline.turns],
            replayed_responses=[t.agent_response for t in replayed.turns],
            original_tool_names=[
                [tc.name for tc in t.tool_calls] for t in baseline.turns
            ],
            replayed_tool_names=[
                [tc.name for tc in t.tool_calls] for t in replayed.turns
            ],
            user_messages=baseline.user_messages,
            threshold=self.threshold,
        )

        return WorkflowGateResult(
            workflow_name=workflow_name,
            passed=report.passed,
            score=report.overall_score,
            turn_count=report.turn_count,
            pass_count=report.pass_count,
            fail_count=report.fail_count,
            report=report,
        )
