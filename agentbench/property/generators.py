"""Value generators for property-based testing.

Each generator produces random values of a specific type and supports
functional composition via ``map()``, ``filter()``, and ``chain()``.
When a test fails, generators also provide *shrinking* helpers so the
shrinking engine can reduce the failing input to a minimal example.
"""

from __future__ import annotations

import random
import string
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Internal word / template pools
# ---------------------------------------------------------------------------

_QUESTION_TEMPLATES = [
    "What is {noun}?",
    "How does {noun} work?",
    "Can you explain {noun}?",
    "Why is {noun} important?",
    "When should I use {noun}?",
    "Tell me about {noun}.",
    "What are the benefits of {noun}?",
    "How can I improve {noun}?",
    "Compare {noun} and {adj} things.",
    "Summarize the key points of {noun}.",
]

_COMMAND_TEMPLATES = [
    "Find {noun}.",
    "Search for {noun}.",
    "Create a {noun}.",
    "Delete the {noun}.",
    "Update {noun} with {adj} values.",
    "Send {noun} to the server.",
    "Run the {noun} pipeline.",
    "Analyze {noun} and report back.",
    "Calculate the {noun} metric.",
    "List all {adj} {noun} items.",
]

_MULTI_TURN_TEMPLATES = [
    "First, {cmd} Then, tell me about {noun}.",
    "Please {cmd} After that, summarize {noun}.",
    "{cmd} Also, what do you think about {noun}?",
]

_DOMAIN_NOUNS: dict[str, list[str]] = {
    "general": [
        "data", "system", "process", "model", "report", "task", "project",
        "document", "file", "record", "event", "user", "account", "setting",
        "service", "resource", "feature", "component", "module", "function",
        "network", "server", "database", "cache", "queue", "topic", "stream",
        "pipeline", "workflow", "schedule", "notification", "alert", "metric",
    ],
    "finance": [
        "invoice", "payment", "transaction", "balance", "account", "refund",
        "receipt", "order", "subscription", "charge", "fee", "tax", "budget",
        "portfolio", "dividend", "interest", "loan", "credit", "debit",
    ],
    "healthcare": [
        "patient", "diagnosis", "treatment", "medication", "appointment",
        "record", "lab result", "prescription", "referral", "symptom",
        "vital sign", "allergy", "procedure", "insurance claim",
    ],
    "ecommerce": [
        "product", "cart", "order", "shipment", "return", "review",
        "catalog", "inventory", "discount", "coupon", "wishlist",
        "checkout", "payment", "address", "delivery",
    ],
}

_ADJS = [
    "new", "old", "large", "small", "fast", "slow", "simple", "complex",
    "important", "useful", "relevant", "recent", "active", "valid",
    "specific", "detailed", "basic", "advanced", "primary", "secondary",
]

_TOOL_NAMES = [
    "search", "lookup", "calculate", "fetch", "send_email", "query_db",
    "http_request", "read_file", "write_file", "run_code", "translate",
    "summarize", "classify", "validate", "transform",
]


def _pick(pool: Sequence[str], rng: random.Random | None = None) -> str:
    r = rng or random
    return r.choice(pool)


# ---------------------------------------------------------------------------
# Base generator mixin
# ---------------------------------------------------------------------------

class _GeneratorMixin:
    """Shared composition helpers (map / filter / chain) and shrinking stub."""

    def __init__(self) -> None:
        self._map_fn: Callable[[Any], Any] | None = None
        self._filter_fn: Callable[[Any], bool] | None = None
        self._chain_gen: _GeneratorMixin | None = None

    # -- composition --------------------------------------------------------

    def map(self, fn: Callable[[Any], Any]) -> _GeneratorMixin:
        """Transform every generated value through *fn*."""
        clone = self._clone()
        clone._map_fn = fn
        return clone

    def filter(self, fn: Callable[[Any], bool]) -> _GeneratorMixin:
        """Only keep generated values for which *fn* returns True."""
        clone = self._clone()
        clone._filter_fn = fn
        return clone

    def chain(self, other: _GeneratorMixin) -> _GeneratorMixin:
        """Pipe this generator's output into *other*'s ``generate``."""
        clone = self._clone()
        clone._chain_gen = other
        return clone

    def _apply(self, value: Any, rng: random.Random) -> Any:
        """Apply map → chain pipeline to a raw generated value."""
        if self._map_fn is not None:
            value = self._map_fn(value)
        if self._chain_gen is not None:
            value = self._chain_gen.generate(rng=rng)
        return value

    def _check_filter(self, value: Any) -> bool:
        if self._filter_fn is not None:
            return self._filter_fn(value)
        return True

    def _clone(self) -> _GeneratorMixin:
        import copy

        return copy.copy(self)


# ---------------------------------------------------------------------------
# AgentInput — realistic agent prompts / queries / commands
# ---------------------------------------------------------------------------

class AgentInput(_GeneratorMixin):
    """Generate realistic agent input text.

    Parameters
    ----------
    min_length / max_length:
        Length bounds for generated text (characters).
    domain:
        Domain vocabulary — ``"general"``, ``"finance"``,
        ``"healthcare"``, ``"ecommerce"``.
    kinds:
        List of prompt kinds to draw from: ``"question"``, ``"command"``,
        ``"multi_turn"``.
    seed:
        Optional RNG seed for reproducibility.
    """

    def __init__(
        self,
        *,
        min_length: int = 10,
        max_length: int = 500,
        domain: str = "general",
        kinds: list[str] | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.min_length = min_length
        self.max_length = max_length
        self.domain = domain
        self.kinds = kinds or ["question", "command", "multi_turn"]
        self._seed = seed
        self._rng = random.Random(seed) if seed is not None else random.Random()

    # -- generation ---------------------------------------------------------

    def generate(self, *, rng: random.Random | None = None) -> str:
        """Produce a single random input string."""
        r = rng or self._rng
        nouns = _DOMAIN_NOUNS.get(self.domain, _DOMAIN_NOUNS["general"])
        kind = r.choice(self.kinds)

        if kind == "question":
            template = r.choice(_QUESTION_TEMPLATES)
        elif kind == "command":
            template = r.choice(_COMMAND_TEMPLATES)
        else:
            template = r.choice(_MULTI_TURN_TEMPLATES)

        text = template.format(
            noun=r.choice(nouns),
            adj=r.choice(_ADJS),
            cmd=r.choice(_COMMAND_TEMPLATES).format(
                noun=r.choice(nouns), adj=r.choice(_ADJS)
            ),
        )

        # Ensure min/max length constraints
        while len(text) < self.min_length:
            text += " " + r.choice(nouns)
        if len(text) > self.max_length:
            text = text[: self.max_length].rstrip()

        text = self._apply(text, r)
        if not self._check_filter(text):
            # Retry once with a different template
            return self.generate(rng=r)
        return text

    def generate_many(self, n: int, *, rng: random.Random | None = None) -> list[str]:
        """Generate *n* distinct inputs."""
        r = rng or self._rng
        return [self.generate(rng=r) for _ in range(n)]

    # -- shrinking ----------------------------------------------------------

    def shrink_value(self, value: str) -> list[str]:
        """Return a list of candidate shrinks for *value*.

        The shrinking engine will try each candidate from smallest to largest
        until it finds the minimal failing input.
        """
        candidates: list[str] = []
        words = value.split()

        # 1. Remove words one at a time from the end
        for i in range(len(words), 0, -1):
            candidates.append(" ".join(words[:i]))

        # 2. Remove each word individually
        for i in range(len(words)):
            shorter = words[:i] + words[i + 1 :]
            if shorter:
                candidates.append(" ".join(shorter))

        # 3. Replace long words with short ones
        for i, w in enumerate(words):
            if len(w) > 3:
                shorter = words[:i] + [w[:3]] + words[i + 1 :]
                candidates.append(" ".join(shorter))

        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in candidates:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique

    def _clone(self) -> AgentInput:
        import copy
        return copy.copy(self)


# ---------------------------------------------------------------------------
# ToolCallGen — valid tool call sequences
# ---------------------------------------------------------------------------

@dataclass
class ToolCall:
    """A single generated tool call."""

    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"tool_name": self.tool_name, "arguments": self.arguments}


class ToolCallGen(_GeneratorMixin):
    """Generate sequences of :class:`ToolCall` objects.

    Parameters
    ----------
    available_tools:
        Pool of valid tool names.  Defaults to a built-in list.
    min_calls / max_calls:
        Bounds for the number of calls in a generated sequence.
    """

    def __init__(
        self,
        *,
        available_tools: list[str] | None = None,
        min_calls: int = 1,
        max_calls: int = 5,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.available_tools = available_tools or list(_TOOL_NAMES)
        self.min_calls = min_calls
        self.max_calls = max_calls
        self._seed = seed
        self._rng = random.Random(seed) if seed is not None else random.Random()

    def generate(self, *, rng: random.Random | None = None) -> list[ToolCall]:
        """Produce a random list of tool calls."""
        r = rng or self._rng
        n = r.randint(self.min_calls, self.max_calls)
        calls: list[ToolCall] = []
        for _ in range(n):
            tool = r.choice(self.available_tools)
            args = self._random_args(r)
            calls.append(ToolCall(tool_name=tool, arguments=args))
        result = self._apply(calls, r)
        if not self._check_filter(result):
            return self.generate(rng=r)
        return result

    def generate_many(
        self, n: int, *, rng: random.Random | None = None
    ) -> list[list[ToolCall]]:
        r = rng or self._rng
        return [self.generate(rng=r) for _ in range(n)]

    @staticmethod
    def _random_args(r: random.Random) -> dict[str, Any]:
        """Generate a small random argument dict."""
        args: dict[str, Any] = {}
        n_args = r.randint(0, 3)
        for _ in range(n_args):
            key = r.choice(["query", "id", "limit", "offset", "format", "value"])
            if key in ("limit", "offset"):
                args[key] = r.randint(1, 100)
            elif key == "value":
                args[key] = r.choice(["true", "false", "auto", "default"])
            else:
                args[key] = "".join(r.choices(string.ascii_lowercase, k=r.randint(3, 8)))
        return args

    def shrink_value(self, value: list[ToolCall]) -> list[list[ToolCall]]:
        """Shrink by removing tool calls from the sequence."""
        candidates: list[list[ToolCall]] = []
        for i in range(len(value) - 1, -1, -1):
            shorter = value[:i]
            if shorter or i == 0:
                candidates.append(shorter)
        # Also try removing individual calls
        for i in range(len(value)):
            candidates.append(value[:i] + value[i + 1 :])
        # Deduplicate
        seen: set[int] = set()
        unique: list[list[ToolCall]] = []
        for c in candidates:
            h = hash(tuple(
                (tc.tool_name, tuple(sorted(tc.arguments.items()))) for tc in c
            ))
            if h not in seen:
                seen.add(h)
                unique.append(c)
        return unique

    def _clone(self) -> ToolCallGen:
        import copy
        return copy.copy(self)


# ---------------------------------------------------------------------------
# ConversationGen — multi-turn conversation histories
# ---------------------------------------------------------------------------

@dataclass
class ConversationTurn:
    """A single turn in a conversation."""

    role: str  # "user" | "assistant" | "system"
    content: str


class ConversationGen(_GeneratorMixin):
    """Generate multi-turn conversation histories.

    Parameters
    ----------
    min_turns / max_turns:
        Number of turns in the generated conversation.
    domain:
        Vocabulary domain (forwarded to :class:`AgentInput`).
    """

    def __init__(
        self,
        *,
        min_turns: int = 2,
        max_turns: int = 10,
        domain: str = "general",
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.min_turns = min_turns
        self.max_turns = max_turns
        self.domain = domain
        self._seed = seed
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._input_gen = AgentInput(domain=domain, min_length=5, max_length=200)

    def generate(self, *, rng: random.Random | None = None) -> list[ConversationTurn]:
        """Produce a random conversation."""
        r = rng or self._rng
        n_turns = r.randint(self.min_turns, self.max_turns)
        turns: list[ConversationTurn] = []
        for i in range(n_turns):
            if i == 0:
                role = "system"
            elif i % 2 == 1:
                role = "user"
            else:
                role = "assistant"
            content = self._input_gen.generate(rng=r)
            turns.append(ConversationTurn(role=role, content=content))

        result = self._apply(turns, r)
        if not self._check_filter(result):
            return self.generate(rng=r)
        return result

    def generate_many(
        self, n: int, *, rng: random.Random | None = None
    ) -> list[list[ConversationTurn]]:
        r = rng or self._rng
        return [self.generate(rng=r) for _ in range(n)]

    def shrink_value(self, value: list[ConversationTurn]) -> list[list[ConversationTurn]]:
        """Shrink by removing turns from the end."""
        candidates: list[list[ConversationTurn]] = []
        for i in range(len(value) - 1, -1, -1):
            candidates.append(value[:i])
        # Also try shortening individual turn contents
        for i, turn in enumerate(value):
            words = turn.content.split()
            if len(words) > 1:
                shorter_turn = ConversationTurn(
                    role=turn.role, content=" ".join(words[: len(words) // 2])
                )
                candidate = list(value)
                candidate[i] = shorter_turn
                candidates.append(candidate)
        return candidates

    def _clone(self) -> ConversationGen:
        import copy
        return copy.copy(self)


# ---------------------------------------------------------------------------
# TrajectoryGen — complete agent trajectories
# ---------------------------------------------------------------------------

class TrajectoryGen(_GeneratorMixin):
    """Generate complete :class:`~agentbench.core.test.AgentTrajectory` objects.

    Useful for testing the testing infrastructure itself — assertions,
    runners, and property checks.

    Parameters
    ----------
    min_steps / max_steps:
        Number of steps in the trajectory.
    available_tools:
        Pool of tool names for tool_call steps.
    """

    def __init__(
        self,
        *,
        min_steps: int = 1,
        max_steps: int = 8,
        available_tools: list[str] | None = None,
        seed: int | None = None,
    ) -> None:
        super().__init__()
        self.min_steps = min_steps
        self.max_steps = max_steps
        self.available_tools = available_tools or list(_TOOL_NAMES)
        self._seed = seed
        self._rng = random.Random(seed) if seed is not None else random.Random()
        self._input_gen = AgentInput(min_length=5, max_length=200)

    def generate(self, *, rng: random.Random | None = None) -> Any:
        """Produce a random :class:`AgentTrajectory`."""
        from agentbench.core.test import AgentStep, AgentTrajectory

        r = rng or self._rng
        n_steps = r.randint(self.min_steps, self.max_steps)
        prompt = self._input_gen.generate(rng=r)

        traj = AgentTrajectory(
            run_id=str(uuid.uuid4()),
            input_prompt=prompt,
            completed=r.choice([True, True, True, False]),  # 75% complete
        )

        actions = ["tool_call", "llm_response", "tool_call", "llm_response", "error"]
        for i in range(n_steps):
            action = r.choice(actions)
            step = AgentStep(
                step_number=i + 1,
                action=action,
                tool_name=r.choice(self.available_tools) if action == "tool_call" else None,
                tool_input={"arg": "val"} if action == "tool_call" else None,
                tool_output="ok" if action == "tool_call" else None,
                reasoning="thinking..." if action == "llm_response" else None,
                response=self._input_gen.generate(rng=r) if action == "llm_response" else None,
                error="something failed" if action == "error" else None,
                latency_ms=r.uniform(10, 500),
            )
            traj.steps.append(step)

        if traj.steps:
            last = traj.steps[-1]
            if last.response:
                traj.final_response = last.response
            elif traj.completed:
                traj.final_response = self._input_gen.generate(rng=r)

        result = self._apply(traj, r)
        if not self._check_filter(result):
            return self.generate(rng=r)
        return result

    def generate_many(self, n: int, *, rng: random.Random | None = None) -> list[Any]:
        r = rng or self._rng
        return [self.generate(rng=r) for _ in range(n)]

    def shrink_value(self, value: Any) -> list[Any]:
        """Shrink by removing steps from the trajectory."""
        from agentbench.core.test import AgentTrajectory

        candidates: list[AgentTrajectory] = []
        steps = value.steps
        # Remove steps from the end
        for i in range(len(steps) - 1, -1, -1):
            shorter = AgentTrajectory(
                run_id=value.run_id,
                input_prompt=value.input_prompt,
                completed=value.completed,
                steps=list(steps[:i]),
                final_response=value.final_response,
            )
            candidates.append(shorter)
        # Remove individual steps
        for i in range(len(steps)):
            shorter_steps = steps[:i] + steps[i + 1 :]
            shorter = AgentTrajectory(
                run_id=value.run_id,
                input_prompt=value.input_prompt,
                completed=value.completed,
                steps=shorter_steps,
                final_response=value.final_response,
            )
            candidates.append(shorter)
        return candidates

    def _clone(self) -> TrajectoryGen:
        import copy
        return copy.copy(self)
