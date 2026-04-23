# Contributing to AgentBench

Thank you for your interest in contributing! This guide covers everything you need to get started.

---

## Quick Start

```bash
# Clone the repository
git clone https://github.com/agentbench/agentbench.git
cd agentbench

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in development mode with all dev dependencies
pip install -e ".[dev]"

# Verify the setup
agentbench --help
pytest
```

---

## Development Setup

### Prerequisites

- **Python 3.11+** (required)
- **Git**
- Optional: Docker (for sandbox testing)

### Install Dependencies

```bash
# Core + dev dependencies (pytest, ruff, mypy, pre-commit)
pip install -e ".[dev]"

# Optional: install framework adapters for integration testing
pip install -e ".[langchain]"
pip install -e ".[openai]"
pip install -e ".[all]"

# Optional: install pre-commit hooks
pre-commit install
```

### Project Structure

```
agentbench/
├── agentbench/                  # Main package
│   ├── __init__.py              # Public API exports
│   ├── core/                    # Core engine
│   │   ├── test.py              # AgentTest, AgentTrajectory, AgentStep
│   │   ├── assertions.py        # expect(), Expectation, AssertionResult
│   │   ├── runner.py            # TestRunner, TestResult, RunResult
│   │   ├── config.py            # AgentBenchConfig
│   │   ├── fixtures.py          # @fixture decorator, Fixture class
│   │   ├── parametrize.py       # @parametrize decorator
│   │   └── sandbox.py           # Docker sandbox manager
│   ├── adapters/                # Framework adapters
│   │   ├── base.py              # AgentAdapter ABC
│   │   ├── raw_api.py           # RawAPIAdapter
│   │   ├── langchain.py         # LangChainAdapter
│   │   ├── openai.py            # OpenAIAdapter
│   │   ├── crewai.py            # CrewAIAdapter
│   │   ├── autogen.py           # AutoGenAdapter
│   │   └── langgraph.py         # LangGraphAdapter
│   ├── evaluation/              # Evaluation tools
│   │   ├── judge.py             # JudgeEvaluator, JudgeResult
│   │   └── metrics.py           # MetricsCollector, RunMetrics
│   ├── storage/                 # Persistence
│   │   └── trajectory.py        # TrajectoryStore, TrajectoryDiff
│   └── cli/                     # CLI commands
│       ├── main.py              # Typer app + 7 commands
│       ├── report.py            # HTML report generation
│       └── scaffold.py          # Project scaffolding
├── tests/                       # Test suite
├── docs/                        # Documentation
├── examples/                    # Example test suites
├── pyproject.toml               # Build config, dependencies
└── README.md
```

---

## Running Tests

### Run the Full Suite

```bash
pytest
```

### Run Specific Test Files

```bash
pytest tests/test_core.py
pytest tests/test_adapters.py
pytest tests/test_evaluation.py
pytest tests/test_cli.py
pytest tests/test_storage.py
```

### Run with Coverage

```bash
pytest --cov=agentbench --cov-report=html
# Then open htmlcov/index.html
```

### Run Specific Tests

```bash
# By test name
pytest tests/test_core.py::test_expectation_to_complete

# By keyword
pytest -k "trajectory"

# With verbose output
pytest -v

# Only unit tests (skip integration/slow)
pytest -m "not integration and not slow"
```

### Test Markers

| Marker | Usage | Description |
|---|---|---|
| `@pytest.mark.slow` | Long-running tests | Skipped in quick CI |
| `@pytest.mark.integration` | Requires external services | Needs API keys / Docker |

---

## Code Style

### Formatter & Linter: Ruff

We use [Ruff](https://docs.astral.sh/ruff/) for formatting and linting.

```bash
# Check for issues
ruff check .

# Auto-fix issues
ruff check --fix .

# Format code
ruff format .
```

### Configuration

Defined in `pyproject.toml`:

```toml
[tool.ruff]
target-version = "py311"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "N", "W", "UP"]
```

### Style Guidelines

1. **Line length:** 100 characters max
2. **Imports:** Use `from __future__ import annotations` at the top of every file
3. **Type hints:** All public functions must have type hints
4. **Docstrings:** Use triple-double-quoted docstrings for all public classes and functions
5. **Naming:**
   - Classes: `PascalCase` (e.g., `AgentTest`, `TrajectoryStore`)
   - Functions/methods: `snake_case` (e.g., `to_complete`, `run_suite`)
   - Constants: `UPPER_SNAKE_CASE` (e.g., `JUDGE_TEMPLATES`)
   - Private members: prefix with `_` (e.g., `_trajectory`, `_run_http`)
6. **Dataclasses:** Use `@dataclass` for data containers; use `field(default_factory=...)` for mutable defaults
7. **Errors:** Use specific error types; always include helpful messages

### Type Checking

```bash
mypy agentbench/
```

Configuration in `pyproject.toml`:

```toml
[tool.mypy]
python_version = "3.11"
strict = true
```

---

## PR Process

### Before Submitting

1. **Create a branch** from `main`:
   ```bash
   git checkout -b feature/my-feature
   ```

2. **Write tests** for your changes:
   - Unit tests for core logic
   - Integration tests for adapter changes (mark with `@pytest.mark.integration`)

3. **Run the full check suite**:
   ```bash
   ruff check .
   ruff format --check .
   mypy agentbench/
   pytest
   ```

4. **Update documentation** if your change affects the public API.

### PR Checklist

- [ ] Tests pass (`pytest`)
- [ ] Linting passes (`ruff check .`)
- [ ] Type checking passes (`mypy agentbench/`)
- [ ] New public APIs have docstrings
- [ ] New features have tests
- [ ] Documentation updated (if applicable)
- [ ] No breaking changes (or clearly documented)

### Commit Messages

Use clear, descriptive commit messages:

```
feat: add streaming support to LangGraphAdapter
fix: handle empty tool_calls in AutoGenAdapter
docs: add RAG agent example to examples gallery
test: add coverage for failure injection edge cases
refactor: extract step recording into base adapter method
```

### Code Review

- All PRs require at least one review
- CI must pass before merging
- Squash merge is preferred for feature branches

---

## Adding a New Adapter

To add support for a new agent framework:

1. **Create the adapter file** at `agentbench/adapters/myframework.py`
2. **Subclass `AgentAdapter`** from `agentbench.adapters.base`
3. **Implement required methods:**
   - `run(prompt, trajectory, ...)` → `AgentTrajectory`
   - `get_available_tools()` → `list[str]`
4. **Register in `agentbench/adapters/__init__.py`**
5. **Add to `pyproject.toml`** optional dependencies
6. **Write tests** in `tests/test_adapters.py`
7. **Update docs:**
   - Add to the adapters table in `README.md`
   - Add a section in `docs/adapters.md`
   - Add to the scaffold templates in `agentbench/cli/scaffold.py`

Example skeleton:

```python
"""MyFramework adapter — test MyFramework agents with AgentBench."""

from __future__ import annotations

import time
from typing import Any

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import (
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)


class MyFrameworkAdapter(AgentAdapter):
    """Adapter for MyFramework agents.

    Usage::
        from myframework import Agent

        agent = Agent(...)
        adapter = MyFrameworkAdapter(agent, tools=["search"])
    """

    def __init__(self, agent: Any, tools: list[str] | None = None) -> None:
        self._agent = agent
        self._tools = tools

    def get_available_tools(self) -> list[str]:
        if self._tools is not None:
            return list(self._tools)
        # Introspect from the agent
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
        start = time.time()
        try:
            # Execute the agent
            result = self._agent.run(prompt)

            # Record steps
            self._record_step(
                trajectory,
                action="llm_response",
                response=str(result),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.final_response = str(result)
            trajectory.completed = True

        except Exception as exc:
            self._record_step(
                trajectory,
                action="error",
                error=str(exc),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(exc)

        return trajectory
```

---

## Reporting Issues

- **Bug reports:** Use GitHub Issues with the `bug` label. Include:
  - Python version
  - AgentBench version (`pip show agentbench`)
  - Minimal reproduction code
  - Expected vs actual behavior

- **Feature requests:** Use GitHub Issues with the `enhancement` label. Describe:
  - The use case
  - Proposed API (if you have ideas)
  - Why existing features don't cover it

---

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
