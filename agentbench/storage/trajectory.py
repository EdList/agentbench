"""Trajectory storage and diffing — record golden runs, detect behavioral drift."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table


@dataclass
class StepDiff:
    """Difference between two trajectory steps."""

    step_number: int
    severity: str  # "critical", "warning", "info", "match"
    field: str
    golden_value: Any
    current_value: Any
    message: str


@dataclass
class DiffResult:
    """Result of comparing two trajectories."""

    golden_name: str
    current_name: str
    step_diffs: list[StepDiff] = field(default_factory=list)
    summary: dict[str, int] = field(default_factory=dict)

    @property
    def has_critical(self) -> bool:
        return any(d.severity == "critical" for d in self.step_diffs)

    @property
    def has_warnings(self) -> bool:
        return any(d.severity == "warning" for d in self.step_diffs)

    def format_output(self) -> str:
        """Format diff result for console output."""
        from io import StringIO

        console = Console(file=StringIO(), force_terminal=True, width=100)

        console.print(
            f"\n[bold]Trajectory Diff: {self.golden_name} vs {self.current_name}[/bold]\n"
        )

        if not self.step_diffs:
            console.print("[green]✓ Trajectories match perfectly[/green]")
            return console.file.getvalue()

        # Summary
        summary_table = Table(title="Summary")
        summary_table.add_column("Severity", style="bold")
        summary_table.add_column("Count", justify="right")
        for severity in ["critical", "warning", "info", "match"]:
            count = self.summary.get(severity, 0)
            style = {"critical": "red", "warning": "yellow", "info": "blue", "match": "green"}[
                severity
            ]
            summary_table.add_row(severity, str(count), style=style)
        console.print(summary_table)

        # Details
        console.print("\n[bold]Step-by-step differences:[/bold]")
        for diff in self.step_diffs:
            if diff.severity == "match":
                continue
            style = {"critical": "red", "warning": "yellow", "info": "blue"}[diff.severity]
            icon = (
                "🔴" if diff.severity == "critical" else "🟡" if diff.severity == "warning" else "ℹ️"
            )
            console.print(
                f"  [{style}]{icon} "
                f"Step {diff.step_number} — {diff.field}: "
                f"{diff.message}[/{style}]"
            )

        return console.file.getvalue()


class TrajectoryStore:
    """Persist and load agent trajectories to/from disk."""

    def __init__(self, base_dir: Path | str = ".agentbench/trajectories"):
        self._base_dir = Path(base_dir)
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _sanitize_name(name: str) -> str:
        """Sanitize a trajectory name to prevent path traversal."""
        import re

        clean = re.sub(r"[^\w\-.]", "_", name)
        if not clean or clean == "_":
            raise ValueError(f"Invalid trajectory name: {name!r}")
        return clean

    def save(self, trajectory_data: dict[str, Any], name: str | None = None) -> Path:
        """Save a trajectory to disk."""
        name = name or trajectory_data.get(
            "name", f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
        )
        name = self._sanitize_name(name)
        path = self._base_dir / f"{name}.json"
        # Verify path hasn't escaped base_dir
        if not path.resolve().is_relative_to(self._base_dir.resolve()):
            raise ValueError(f"Trajectory name escapes base directory: {name!r}")
        path.write_text(json.dumps(trajectory_data, indent=2, default=str))
        return path

    def load(self, name: str) -> dict[str, Any]:
        """Load a trajectory from disk."""
        name = self._sanitize_name(name)
        path = self._base_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Trajectory not found: {path}")
        return json.loads(path.read_text())

    def list(self) -> list[str]:
        """List all saved trajectory names."""
        return [p.stem for p in self._base_dir.glob("*.json")]

    def delete(self, name: str) -> None:
        """Delete a saved trajectory."""
        name = self._sanitize_name(name)
        path = self._base_dir / f"{name}.json"
        if path.exists():
            path.unlink()


class TrajectoryDiff:
    """Compare two trajectories and identify behavioral drift."""

    def compare(
        self,
        golden: dict[str, Any],
        current: dict[str, Any],
    ) -> DiffResult:
        """Compare current trajectory against a golden baseline.

        Classifies differences by severity:
        - CRITICAL: Different tool called, different final action, PII exposure
        - WARNING: Different reasoning path, different latency profile
        - INFO: Different wording in intermediate steps
        - MATCH: Steps are equivalent
        """
        result = DiffResult(
            golden_name=golden.get("name", "golden"),
            current_name=current.get("name", "current"),
        )

        golden_steps = golden.get("steps", [])
        current_steps = current.get("steps", [])

        # Compare step counts
        if len(golden_steps) != len(current_steps):
            result.step_diffs.append(
                StepDiff(
                    step_number=0,
                    severity="warning",
                    field="step_count",
                    golden_value=len(golden_steps),
                    current_value=len(current_steps),
                    message=f"Different step count: {len(golden_steps)} → {len(current_steps)}",
                )
            )

        # Compare each step
        max_steps = max(len(golden_steps), len(current_steps))
        for i in range(max_steps):
            golden_step = golden_steps[i] if i < len(golden_steps) else None
            current_step = current_steps[i] if i < len(current_steps) else None

            if golden_step is None:
                result.step_diffs.append(
                    StepDiff(
                        step_number=i,
                        severity="warning",
                        field="extra_step",
                        golden_value=None,
                        current_value=current_step,
                        message="Extra step in current run",
                    )
                )
                continue

            if current_step is None:
                result.step_diffs.append(
                    StepDiff(
                        step_number=i,
                        severity="warning",
                        field="missing_step",
                        golden_value=golden_step,
                        current_value=None,
                        message="Step missing in current run",
                    )
                )
                continue

            self._compare_steps(i, golden_step, current_step, result)

        # Compare final responses
        golden_response = golden.get("response", golden.get("final_response", ""))
        current_response = current.get("response", current.get("final_response", ""))
        if golden_response != current_response:
            result.step_diffs.append(
                StepDiff(
                    step_number=max_steps,
                    severity="info",
                    field="final_response",
                    golden_value=(golden_response or "")[:100],
                    current_value=(current_response or "")[:100],
                    message="Final response differs",
                )
            )

        # Build summary
        result.summary = {
            "critical": sum(1 for d in result.step_diffs if d.severity == "critical"),
            "warning": sum(1 for d in result.step_diffs if d.severity == "warning"),
            "info": sum(1 for d in result.step_diffs if d.severity == "info"),
            "match": sum(1 for d in result.step_diffs if d.severity == "match"),
        }

        return result

    def _compare_steps(self, index: int, golden: dict, current: dict, result: DiffResult) -> None:
        """Compare two individual steps."""
        # Check action type
        if golden.get("action") != current.get("action"):
            result.step_diffs.append(
                StepDiff(
                    step_number=index,
                    severity="critical",
                    field="action",
                    golden_value=golden.get("action"),
                    current_value=current.get("action"),
                    message=f"Action changed: {golden.get('action')} → {current.get('action')}",
                )
            )
            return  # Different action types, skip further comparison

        # Check tool name (for tool_call steps)
        if golden.get("action") == "tool_call":
            if golden.get("tool_name") != current.get("tool_name"):
                result.step_diffs.append(
                    StepDiff(
                        step_number=index,
                        severity="critical",
                        field="tool_name",
                        golden_value=golden.get("tool_name"),
                        current_value=current.get("tool_name"),
                        message=(
                            f"Different tool: "
                            f"{golden.get('tool_name')} → "
                            f"{current.get('tool_name')}"
                        ),
                    )
                )
            elif golden.get("tool_input") != current.get("tool_input"):
                result.step_diffs.append(
                    StepDiff(
                        step_number=index,
                        severity="warning",
                        field="tool_input",
                        golden_value=golden.get("tool_input"),
                        current_value=current.get("tool_input"),
                        message=f"Tool '{golden.get('tool_name')}' called with different inputs",
                    )
                )
            elif golden.get("tool_output") != current.get("tool_output"):
                result.step_diffs.append(
                    StepDiff(
                        step_number=index,
                        severity="warning",
                        field="tool_output",
                        golden_value=golden.get("tool_output"),
                        current_value=current.get("tool_output"),
                        message=f"Tool '{golden.get('tool_name')}' returned different output",
                    )
                )
            else:
                result.step_diffs.append(
                    StepDiff(
                        step_number=index,
                        severity="match",
                        field="tool_call",
                        golden_value=golden.get("tool_name"),
                        current_value=current.get("tool_name"),
                        message="Tool call matches",
                    )
                )

        # Check for errors
        if current.get("error") and not golden.get("error"):
            result.step_diffs.append(
                StepDiff(
                    step_number=index,
                    severity="critical",
                    field="error",
                    golden_value=None,
                    current_value=current.get("error"),
                    message=f"New error: {current.get('error')}",
                )
            )

        # Check response content (info-level)
        golden_resp = (golden.get("response") or golden.get("reasoning") or "").lower()
        current_resp = (current.get("response") or current.get("reasoning") or "").lower()
        if golden_resp and current_resp and golden_resp != current_resp:
            result.step_diffs.append(
                StepDiff(
                    step_number=index,
                    severity="info",
                    field="response",
                    golden_value=golden_resp[:80],
                    current_value=current_resp[:80],
                    message="Response content differs",
                )
            )
