"""Replay report — per-turn pass/fail with regression scoring.

A ReplayReport combines a DiffResult with human-readable details and
persistence.  This is what the ``agentbench replay`` command outputs.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentbench.replayer.diff import DiffResult

# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

_REPORTS_DIR = Path(".agentbench/reports")


# ---------------------------------------------------------------------------
# Per-turn result
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    """Pass/fail verdict for a single replayed turn."""

    turn_index: int
    user_message: str
    original_response: str
    replayed_response: str
    original_tools: list[str]
    replayed_tools: list[str]
    tool_sequence_match: float
    tool_args_score: float
    response_similarity: float
    score: float
    passed: bool
    notes: str = ""


# ---------------------------------------------------------------------------
# Full report
# ---------------------------------------------------------------------------


@dataclass
class ReplayReport:
    """Complete report for a replay run."""

    workflow_name: str
    replay_of: str
    threshold: float = 0.8
    created_at: str = ""
    turn_results: list[TurnResult] = field(default_factory=list)
    overall_score: float = 0.0
    passed: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    @property
    def turn_count(self) -> int:
        return len(self.turn_results)

    @property
    def pass_count(self) -> int:
        return sum(1 for t in self.turn_results if t.passed)

    @property
    def fail_count(self) -> int:
        return self.turn_count - self.pass_count

    # -- Build from diff ----------------------------------------------------

    @classmethod
    def from_diff(
        cls,
        workflow_name: str,
        replay_of: str,
        diff_result: DiffResult,
        original_responses: list[str],
        replayed_responses: list[str],
        original_tool_names: list[list[str]],
        replayed_tool_names: list[list[str]],
        user_messages: list[str],
        threshold: float = 0.8,
    ) -> ReplayReport:
        """Build a report from a DiffResult plus original/replayed data."""
        turn_results: list[TurnResult] = []
        for i, td in enumerate(diff_result.turn_diffs):
            orig_resp = original_responses[i] if i < len(original_responses) else ""
            replay_resp = replayed_responses[i] if i < len(replayed_responses) else ""
            orig_tools = original_tool_names[i] if i < len(original_tool_names) else []
            replay_tools = replayed_tool_names[i] if i < len(replayed_tool_names) else []
            user_msg = user_messages[i] if i < len(user_messages) else ""

            # Build notes
            notes_parts: list[str] = []
            if td.tool_sequence_match < 1.0:
                notes_parts.append(
                    f"tool sequence mismatch ({orig_tools} → {replay_tools})"
                )
            if td.response_similarity < 0.5:
                notes_parts.append("response diverged significantly")
            if td.tool_args_score < 0.5:
                notes_parts.append("tool arguments changed substantially")

            turn_results.append(
                TurnResult(
                    turn_index=i,
                    user_message=user_msg,
                    original_response=orig_resp,
                    replayed_response=replay_resp,
                    original_tools=orig_tools,
                    replayed_tools=replay_tools,
                    tool_sequence_match=td.tool_sequence_match,
                    tool_args_score=td.tool_args_score,
                    response_similarity=td.response_similarity,
                    score=td.score,
                    passed=td.score >= threshold,
                    notes="; ".join(notes_parts),
                )
            )

        overall = diff_result.overall_score
        return cls(
            workflow_name=workflow_name,
            replay_of=replay_of,
            threshold=threshold,
            turn_results=turn_results,
            overall_score=round(overall, 3),
            passed=overall >= threshold,
        )

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ReplayReport:
        turns = [
            TurnResult(**t) for t in data.get("turn_results", [])
        ]
        return cls(
            workflow_name=data["workflow_name"],
            replay_of=data["replay_of"],
            threshold=data.get("threshold", 0.8),
            created_at=data.get("created_at", ""),
            turn_results=turns,
            overall_score=data.get("overall_score", 0.0),
            passed=data.get("passed", False),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> ReplayReport:
        return cls.from_dict(json.loads(text))

    # -- Persistence --------------------------------------------------------

    def save(self, base_dir: Path | None = None) -> Path:
        """Save to ``.agentbench/reports/<name>-<timestamp>.json``."""
        reports_dir = (base_dir or Path.cwd()) / _REPORTS_DIR
        reports_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = reports_dir / f"{self.workflow_name}-{ts}.json"
        path.write_text(self.to_json())
        return path

    @classmethod
    def load(cls, path: str | Path) -> ReplayReport:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Report not found: {p}")
        return cls.from_json(p.read_text())
