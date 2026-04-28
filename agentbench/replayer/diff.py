"""Diff engine — compare original vs replayed workflow turns.

Three comparison dimensions:

1. **Tool-call sequence** — ordered list of tool names must match exactly.
2. **Tool-call arguments** — structural key-level comparison (same keys,
   values loosely match via string containment or numeric tolerance).
3. **Response semantics** — string similarity (Levenshtein ratio) for
   fast local comparison.  LLM-as-judge integration is optional.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher

from agentbench.recorder.workflow import ToolCall, Turn

# ---------------------------------------------------------------------------
# Scoring helpers
# ---------------------------------------------------------------------------

def _string_similarity(a: str, b: str) -> float:
    """Return 0-1 similarity ratio between two strings."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _args_similarity(
    original_args: str, replayed_args: str,
) -> float:
    """Compare two JSON argument strings structurally.

    Parses both as JSON dicts, then checks:
    - Same keys present → +0.5
    - Values match (string containment or numeric within 10%) → per-key score
    Returns 0-1.
    """
    import json

    try:
        orig = json.loads(original_args) if original_args else {}
        replay = json.loads(replayed_args) if replayed_args else {}
    except (json.JSONDecodeError, TypeError):
        # Fall back to string similarity for non-JSON args
        return _string_similarity(original_args, replayed_args)

    if not isinstance(orig, dict) or not isinstance(replay, dict):
        return _string_similarity(str(orig), str(replay))

    orig_keys = set(orig.keys())
    replay_keys = set(replay.keys())

    if not orig_keys and not replay_keys:
        return 1.0

    # Key overlap score
    key_score = len(orig_keys & replay_keys) / max(len(orig_keys | replay_keys), 1)

    # Value match for shared keys
    shared = orig_keys & replay_keys
    if not shared:
        return key_score * 0.5  # No shared keys → partial credit for having args

    value_scores: list[float] = []
    for k in shared:
        ov, rv = orig[k], replay[k]
        if ov == rv:
            value_scores.append(1.0)
        elif isinstance(ov, (int, float)) and isinstance(rv, (int, float)):
            denom = max(abs(ov), abs(rv), 1e-9)
            value_scores.append(max(0.0, 1.0 - abs(ov - rv) / denom))
        elif isinstance(ov, str) and isinstance(rv, str):
            value_scores.append(_string_similarity(ov, rv))
        else:
            value_scores.append(0.0)

    avg_value = sum(value_scores) / len(value_scores)
    return key_score * 0.5 + avg_value * 0.5


# ---------------------------------------------------------------------------
# Per-turn diff result
# ---------------------------------------------------------------------------


@dataclass
class TurnDiff:
    """Comparison result for a single turn."""

    turn_index: int
    tool_sequence_match: float  # 0-1
    tool_args_score: float  # 0-1, average across tool calls
    response_similarity: float  # 0-1
    weight: float = 1.0  # higher for turns with tool calls

    @property
    def score(self) -> float:
        """Weighted composite score for this turn."""
        return (
            self.tool_sequence_match * 0.4
            + self.tool_args_score * 0.3
            + self.response_similarity * 0.3
        )


@dataclass
class DiffResult:
    """Full comparison result between original and replayed workflow."""

    turn_diffs: list[TurnDiff] = field(default_factory=list)

    @property
    def overall_score(self) -> float:
        """Weighted average of all turn scores."""
        if not self.turn_diffs:
            return 1.0
        total_weight = sum(td.weight for td in self.turn_diffs)
        if total_weight == 0:
            return 1.0
        return sum(td.score * td.weight for td in self.turn_diffs) / total_weight

    @property
    def passed(self) -> bool:
        """True if overall score >= 0.8 (default threshold)."""
        return self.overall_score >= 0.8


# ---------------------------------------------------------------------------
# Differ
# ---------------------------------------------------------------------------


class WorkflowDiffer:
    """Compare original workflow turns against replayed turns."""

    def __init__(self, threshold: float = 0.8) -> None:
        self.threshold = threshold

    def diff_turns(
        self,
        original: list[Turn],
        replayed: list[Turn],
    ) -> DiffResult:
        """Compare two lists of turns and return a DiffResult."""
        result = DiffResult()
        max_turns = max(len(original), len(replayed))

        for i in range(max_turns):
            if i >= len(original):
                # Extra turns in replay → score 0
                result.turn_diffs.append(
                    TurnDiff(
                        turn_index=i,
                        tool_sequence_match=0.0,
                        tool_args_score=0.0,
                        response_similarity=0.0,
                        weight=1.0,
                    )
                )
                continue

            if i >= len(replayed):
                # Missing turns in replay → score 0
                result.turn_diffs.append(
                    TurnDiff(
                        turn_index=i,
                        tool_sequence_match=0.0,
                        tool_args_score=0.0,
                        response_similarity=0.0,
                        weight=1.0,
                    )
                )
                continue

            orig_turn = original[i]
            replay_turn = replayed[i]

            # Tool sequence match
            orig_tools = [tc.name for tc in orig_turn.tool_calls]
            replay_tools = [tc.name for tc in replay_turn.tool_calls]
            tool_seq = self._compare_sequences(orig_tools, replay_tools)

            # Tool args score
            tool_args = self._compare_tool_args(
                orig_turn.tool_calls, replay_turn.tool_calls,
            )

            # Response similarity
            resp_sim = _string_similarity(
                orig_turn.agent_response, replay_turn.agent_response,
            )

            # Weight: turns with tool calls are more important
            weight = 2.0 if orig_turn.tool_calls else 1.0

            result.turn_diffs.append(
                TurnDiff(
                    turn_index=i,
                    tool_sequence_match=tool_seq,
                    tool_args_score=tool_args,
                    response_similarity=resp_sim,
                    weight=weight,
                )
            )

        return result

    def _compare_sequences(
        self, original: list[str], replayed: list[str],
    ) -> float:
        """Compare ordered tool name sequences."""
        if not original and not replayed:
            return 1.0
        if not original or not replayed:
            return 0.0

        # Exact match
        if original == replayed:
            return 1.0

        # Use sequence matcher for partial credit
        return SequenceMatcher(None, original, replayed).ratio()

    def _compare_tool_args(
        self,
        original: list[ToolCall],
        replayed: list[ToolCall],
    ) -> float:
        """Compare tool call arguments between matched turns."""
        if not original and not replayed:
            return 1.0
        if not original or not replayed:
            return 0.0

        # Match by tool name, then compare args
        scores: list[float] = []
        replay_by_name: dict[str, list[ToolCall]] = {}
        for tc in replayed:
            replay_by_name.setdefault(tc.name, []).append(tc)

        for orig_tc in original:
            candidates = replay_by_name.get(orig_tc.name, [])
            if not candidates:
                scores.append(0.0)
                continue

            # Best matching candidate
            best = max(
                _args_similarity(orig_tc.arguments, c.arguments)
                for c in candidates
            )
            scores.append(best)

        return sum(scores) / len(scores) if scores else 1.0
