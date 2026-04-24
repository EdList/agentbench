"""Shrinking engine for property-based tests.

When a property test fails, the shrinking engine attempts to reduce the
failing input to the *smallest* value that still triggers the failure.
This makes it far easier to understand and debug property violations.

Strategies
----------
- **String shrinking**: shorten, remove words, truncate words
- **Numeric shrinking**: simplify towards 0
- **List shrinking**: remove elements (from end, then individually)
- **Generic**: delegate to the generator's ``shrink_value`` method
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class ShrinkResult:
    """The result of a shrinking pass.

    Attributes
    ----------
    original:
        The input that first triggered the failure.
    minimal:
        The smallest input found that still triggers the failure.
    shrinks_tried:
        How many candidate shrinks were evaluated.
    """

    original: Any
    minimal: Any
    shrinks_tried: int = 0

    @property
    def was_shrunk(self) -> bool:
        """True if the minimal value is different from the original."""
        return self.original != self.minimal

    def summary(self) -> str:
        lines = [
            "Shrink Result",
            f"  Original:  {self.original!r}",
            f"  Minimal:   {self.minimal!r}",
            f"  Shrinks tried: {self.shrinks_tried}",
            f"  Was shrunk: {self.was_shrunk}",
        ]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Core shrink function
# ---------------------------------------------------------------------------

def shrink(
    value: Any,
    *,
    predicate: Callable[[Any], bool],
    shrink_value: Callable[[Any], list[Any]] | None = None,
    max_shrinks: int = 1000,
) -> ShrinkResult:
    """Shrink *value* to the smallest input that still fails *predicate*.

    Parameters
    ----------
    value:
        The original failing input.
    predicate:
        A callable that returns ``True`` when the value **still fails**.
        The shrinker tries to find the smallest *value* for which this is
        still true.
    shrink_value:
        Optional function that returns a list of candidate shrinks for a
        given value.  If not provided, :func:`default_shrink_candidates` is
        used.
    max_shrinks:
        Safety limit on the number of shrink attempts.

    Returns
    -------
    ShrinkResult
    """
    _shrink_fn = shrink_value or default_shrink_candidates
    current = value
    attempts = 0

    changed = True
    while changed and attempts < max_shrinks:
        changed = False
        candidates = _shrink_fn(current)
        for candidate in candidates:
            attempts += 1
            if attempts > max_shrinks:
                changed = False
                break
            try:
                if predicate(candidate):
                    current = candidate
                    changed = True
                    break  # restart with smaller value
            except Exception:
                # If the candidate itself raises, it's not a valid shrink
                continue

    return ShrinkResult(
        original=value,
        minimal=current,
        shrinks_tried=attempts,
    )


# ---------------------------------------------------------------------------
# Default shrink strategies
# ---------------------------------------------------------------------------

def default_shrink_candidates(value: Any) -> list[Any]:
    """Return candidate shrinks for *value* based on its type."""
    if isinstance(value, str):
        return _shrink_string(value)
    elif isinstance(value, int):
        return _shrink_int(value)
    elif isinstance(value, float):
        return _shrink_float(value)
    elif isinstance(value, list):
        return _shrink_list(value)
    elif isinstance(value, dict):
        return _shrink_dict(value)
    return []


def _shrink_string(value: str) -> list[str]:
    """Shrink a string by shortening and removing words."""
    candidates: list[str] = []

    # Empty string is the ultimate shrink
    if value:
        candidates.append("")

    # Halve the string
    mid = len(value) // 2
    if mid > 0:
        candidates.append(value[:mid])

    # Remove substrings from the end
    for i in range(len(value), 0, -1):
        candidates.append(value[:i])

    # Remove individual words
    words = value.split()
    if len(words) > 1:
        # Remove words from the end
        for i in range(len(words) - 1, 0, -1):
            candidates.append(" ".join(words[:i]))
        # Remove each word individually
        for i in range(len(words)):
            shorter = words[:i] + words[i + 1 :]
            if shorter:
                candidates.append(" ".join(shorter))

    # Truncate each word to 3 chars
    for i, w in enumerate(words):
        if len(w) > 3:
            shorter = words[:i] + [w[:3]] + words[i + 1 :]
            candidates.append(" ".join(shorter))

    # Deduplicate preserving order
    seen: set[str] = set()
    unique: list[str] = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            unique.append(c)
    return unique


def _shrink_int(value: int) -> list[int]:
    """Shrink an integer towards 0."""
    candidates: list[int] = [0]
    if value > 0:
        candidates.append(value - 1)
        candidates.append(value // 2)
    elif value < 0:
        candidates.append(value + 1)
        candidates.append(value // 2)
    return candidates


def _shrink_float(value: float) -> list[float]:
    """Shrink a float towards 0.0."""
    candidates = [0.0]
    if value != 0.0:
        candidates.append(value / 2)
        candidates.append(round(value / 2, 4))
    return candidates


def _shrink_list(value: list) -> list[list]:
    """Shrink a list by removing elements."""
    candidates: list[list] = []
    # Empty list
    if value:
        candidates.append([])
    # Remove from end
    for i in range(len(value) - 1, 0, -1):
        candidates.append(value[:i])
    # Remove individual elements
    for i in range(len(value)):
        candidates.append(value[:i] + value[i + 1 :])
    return candidates


def _shrink_dict(value: dict) -> list[dict]:
    """Shrink a dict by removing keys."""
    candidates: list[dict] = [{}]
    keys = list(value.keys())
    for key in keys:
        smaller = dict(value)
        smaller.pop(key, None)
        if smaller:
            candidates.append(smaller)
    return candidates
