"""Workflow data model for recorded agent interactions.

A Workflow captures a complete multi-turn conversation with an agent,
including all tool calls, responses, timing, and metadata.  This is the
primary artifact for behavioral regression testing — the ``record`` command
creates one, the ``replay`` command compares a new session against it, and
the ``gate`` CI command blocks deploys when a workflow regresses.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Storage path
# ---------------------------------------------------------------------------

_WORKFLOWS_DIR = Path(".agentbench/workflows")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class ToolCall:
    """A tool call made by the agent during a turn."""

    id: str
    name: str
    arguments: str  # JSON string
    result: str | None = None

    def parsed_arguments(self) -> dict[str, Any]:
        """Parse the JSON arguments string into a dict."""
        try:
            return json.loads(self.arguments)
        except (json.JSONDecodeError, TypeError):
            return {}


@dataclass
class Turn:
    """A single exchange (user message → agent response) in a workflow."""

    index: int
    user_message: str
    agent_response: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    latency_ms: float = 0.0
    timestamp: str = ""
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.timestamp:
            self.timestamp = datetime.now(UTC).isoformat()


@dataclass
class Workflow:
    """A complete recorded agent interaction.

    Persists to ``.agentbench/workflows/<name>.json`` and can be loaded
    for replay / regression testing.
    """

    name: str
    agent_url: str
    agent_format: str  # "openai" | "raw"
    created_at: str = ""
    turns: list[Turn] = field(default_factory=list)
    total_duration_ms: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(UTC).isoformat()

    # -- Computed properties ------------------------------------------------

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def total_tool_calls(self) -> int:
        return sum(len(t.tool_calls) for t in self.turns)

    @property
    def tool_call_sequence(self) -> list[str]:
        """Ordered list of all tool names called across all turns."""
        return [tc.name for t in self.turns for tc in t.tool_calls]

    @property
    def user_messages(self) -> list[str]:
        """All user messages in order (inputs for replay)."""
        return [t.user_message for t in self.turns]

    # -- Mutation -----------------------------------------------------------

    def add_turn(self, turn: Turn) -> None:
        """Append a turn and recalculate total duration."""
        self.turns.append(turn)
        self.total_duration_ms = sum(t.latency_ms for t in self.turns)

    # -- Serialization ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Workflow:
        turns: list[Turn] = []
        for t in data.get("turns", []):
            tool_calls = [ToolCall(**tc) for tc in t.get("tool_calls", [])]
            turns.append(
                Turn(
                    index=t["index"],
                    user_message=t["user_message"],
                    agent_response=t["agent_response"],
                    tool_calls=tool_calls,
                    latency_ms=t.get("latency_ms", 0.0),
                    timestamp=t.get("timestamp", ""),
                    error=t.get("error"),
                    metadata=t.get("metadata", {}),
                )
            )
        return cls(
            name=data["name"],
            agent_url=data["agent_url"],
            agent_format=data.get("agent_format", "openai"),
            created_at=data.get("created_at", ""),
            turns=turns,
            total_duration_ms=data.get("total_duration_ms", 0.0),
            metadata=data.get("metadata", {}),
        )

    @classmethod
    def from_json(cls, text: str) -> Workflow:
        return cls.from_dict(json.loads(text))

    # -- Persistence --------------------------------------------------------

    def save(self, base_dir: Path | None = None) -> Path:
        """Save to ``.agentbench/workflows/<name>.json``."""
        wf_dir = (base_dir or Path.cwd()) / _WORKFLOWS_DIR
        wf_dir.mkdir(parents=True, exist_ok=True)
        path = wf_dir / f"{self.name}.json"
        path.write_text(self.to_json())
        return path

    @classmethod
    def load(cls, name: str, base_dir: Path | None = None) -> Workflow:
        """Load a workflow by name."""
        wf_dir = (base_dir or Path.cwd()) / _WORKFLOWS_DIR
        path = wf_dir / f"{name}.json"
        if not path.exists():
            raise FileNotFoundError(f"Workflow '{name}' not found at {path}")
        return cls.from_json(path.read_text())

    @classmethod
    def list_workflows(cls, base_dir: Path | None = None) -> list[tuple[str, str]]:
        """Return ``(name, created_at)`` pairs for all saved workflows."""
        wf_dir = (base_dir or Path.cwd()) / _WORKFLOWS_DIR
        if not wf_dir.exists():
            return []
        results: list[tuple[str, str]] = []
        for path in sorted(wf_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text())
                results.append((data["name"], data.get("created_at", "")))
            except (json.JSONDecodeError, KeyError):
                continue
        return results

    @classmethod
    def delete(cls, name: str, base_dir: Path | None = None) -> bool:
        """Delete a workflow.  Returns ``True`` if it existed and was removed."""
        wf_dir = (base_dir or Path.cwd()) / _WORKFLOWS_DIR
        path = wf_dir / f"{name}.json"
        if path.exists():
            path.unlink()
            return True
        return False
