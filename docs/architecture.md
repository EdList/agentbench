# Architecture Overview

How AgentBench works under the hood — the engine, data flow, adapter pattern, and evaluation pipeline.

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                        CLI (Typer)                          │
│  run | record | diff | init | watch | report | list         │
└──────────────┬──────────────────────────────────────────────┘
               │
               ▼
┌──────────────────────────────────────────────────────────────┐
│                     TestRunner                               │
│  Discovers suites → runs test methods → collects results     │
│  Supports: parallel execution, filtering, parametric tests   │
└──────────┬──────────────────────────┬───────────────────────┘
           │                          │
           ▼                          ▼
┌─────────────────────┐    ┌─────────────────────────────────┐
│     AgentTest        │    │         expect()                │
│  Base class for      │    │  Fluent assertion builder       │
│  test suites         │    │  Collects AssertionResults      │
│                      │    │                                 │
│  .run(prompt)        │    │  .to_complete()                 │
│       │              │    │  .to_use_tool()                 │
│       ▼              │    │  .to_follow_workflow()          │
│  ┌─────────────┐     │    │  .to_not_expose()               │
│  │  Adapter    │     │    │  .to_retry()                    │
│  │  (pluggable)│     │    └─────────────────────────────────┘
│  └──────┬──────┘     │
│         │            │
└─────────┼────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────┐
│                  Adapter Layer (Strategy Pattern)             │
│                                                              │
│  ┌──────────┐ ┌──────────────┐ ┌────────┐ ┌──────────────┐  │
│  │ RawAPI   │ │ LangChain    │ │ OpenAI │ │ CrewAI       │  │
│  │(HTTP/Fn) │ │ (Callbacks)  │ │(Thread)│ │ (kickoff)    │  │
│  └──────────┘ └──────────────┘ └────────┘ └──────────────┘  │
│  ┌──────────┐ ┌──────────────┐                               │
│  │ AutoGen  │ │ LangGraph    │     All implement AgentAdapter│
│  │ (chat)   │ │ (stream)     │     .run() → AgentTrajectory  │
│  └──────────┘ └──────────────┘                               │
└──────────────────────────────────────────────────────────────┘
          │
          ▼
┌──────────────────────────────────────────────────────────────┐
│                    AgentTrajectory                            │
│  The central data structure — records every step the agent   │
│  takes during execution.                                     │
│                                                              │
│  Steps: [AgentStep, AgentStep, ...]                          │
│  Each step: action, tool_name, tool_input, tool_output,      │
│             reasoning, response, error, latency_ms           │
└──────────┬──────────────────────┬────────────────────────────┘
           │                      │
           ▼                      ▼
┌─────────────────────┐  ┌───────────────────────┐
│   Evaluation        │  │   Storage             │
│                     │  │                       │
│  JudgeEvaluator     │  │  TrajectoryStore      │
│  (LLM-as-Judge)     │  │  (JSON persistence)   │
│                     │  │                       │
│  MetricsCollector   │  │  TrajectoryDiff       │
│  (cost, latency,    │  │  (behavioral drift    │
│   token tracking)   │  │   detection)          │
└─────────────────────┘  └───────────────────────┘
```

---

## Data Flow

### Test Execution Flow

```
1. CLI: agentbench run ./tests

2. TestRunner.discover_suites("./tests")
   ├── Scan for test_*.py files
   ├── Import each module
   └── Find AgentTest subclasses

3. For each suite:
   ├── Instantiate suite class
   ├── Call setup_class() hook
   │
   ├── For each test method:
   │   ├── Create fresh instance (no state leakage)
   │   ├── Call setup() hook
   │   ├── _set_active_test(instance)
   │   │   └── Register with thread-local for expect() tracking
   │   │
   │   ├── Execute test method:
   │   │   ├── instance.run("prompt")
   │   │   │   ├── Build AgentTrajectory
   │   │   │   ├── Call adapter.run(prompt, trajectory, ...)
   │   │   │   │   └── Adapter executes the real agent
   │   │   │   │       └── Records each step via _record_step()
   │   │   │   └── Return populated trajectory
   │   │   │
   │   │   └── expect(trajectory)
   │   │       └── Creates Expectation → registers with active test
   │   │           └── .to_complete(), .to_use_tool(), etc.
   │   │               └── Appends AssertionResult to Expectation
   │   │
   │   ├── Collect expectation results from instance._expectations
   │   ├── Build TestResult (passed/failed, assertions, trajectory)
   │   └── Call teardown() hook
   │
   ├── Call teardown_class() hook
   └── Return TestSuiteResult

4. Build RunResult across all suites
5. Print summary, save report, exit with code
```

---

## Core Components

### AgentTest (Core Test Class)

`AgentTest` is the user-facing base class. It:

- Stores the agent name and adapter reference
- Provides `self.run(prompt)` which delegates to the adapter
- Manages failure/latency injection configuration
- Maintains the per-instance trajectory state
- Sets `__test__ = False` to prevent pytest from collecting it

The runner creates a **fresh instance per test method** to prevent state leakage between tests.

### expect() and the Assertion System

The assertion system uses a **fluent builder pattern**:

```python
expect(trajectory).to_complete()        # Appends AssertionResult, returns Expectation
expect(trajectory).to_use_tool("x")     # Appends another AssertionResult
expect(trajectory).to_respond_with("y") # And another
```

**Thread-local tracking:** When `expect()` is called, it checks a thread-local `_active_test` variable (set by the runner before each test). If found, the `Expectation` is registered with the test instance's `_expectations` list. This allows the runner to collect all assertions after the test method returns — without requiring explicit assertion checking.

**Negation:** `.to_not` flips the next assertion's `passed` flag and prefixes the message with `"NOT:"`. It resets after one assertion.

**Step assertions:** `.step(index)` returns a `StepAssertion` with its own assertion methods (`.used_tool()`, `.responded_with()`, `.has_no_error()`).

### TestRunner

The runner is responsible for:

1. **Discovery:** Scanning paths for `test_*.py` files, importing them, finding `AgentTest` subclasses
2. **Method discovery:** Inspecting instances for `test_*` methods, expanding `@parametrize` decorators
3. **Execution:** Running each test with proper isolation (fresh instance per test)
4. **Result collection:** Gathering assertions, trajectories, and timing
5. **Hooks:** Calling `setup_class`, `setup`, `teardown`, `teardown_class` at the right times
6. **Parallelism:** Using `ThreadPoolExecutor` when `parallel > 1`

**Fresh instance per test:** Each test method gets a brand-new instance of the suite class. This prevents state leakage — if test A sets `self._data = ...`, test B won't see it.

**Parametric expansion:** When a method has `_agentbench_parametrize` metadata (set by the `@parametrize` decorator), the runner expands it into N separate test items, each named like `test_handles[X]`.

---

## Adapter Pattern

All adapters implement the `AgentAdapter` abstract base class using the **Strategy pattern**. This decouples test logic from agent framework specifics.

### AgentAdapter Interface

```python
class AgentAdapter(ABC):
    @abstractmethod
    def run(self, prompt, trajectory, failure_injections, latency_injections,
            max_steps, timeout_seconds, context) -> AgentTrajectory:
        ...

    @abstractmethod
    def get_available_tools(self) -> list[str]:
        ...
```

### Shared Infrastructure

The base class provides common utilities that all adapters use:

- **`_record_step(trajectory, action, ...)`** — Creates an `AgentStep` and appends it to the trajectory. Every adapter uses this to ensure consistent step recording.
- **`_should_inject_failure(tool_name, failure_injections)`** — Checks if a failure should be injected for the current tool call. Decrements the failure counter.
- **`_safe_step_kwargs(data)`** — Filters step dicts to only include valid `_record_step` parameters.

### Adapter Integration Strategies

Each adapter uses the framework's native integration mechanism:

| Adapter | Integration Strategy |
|---|---|
| **RawAPI (HTTP)** | Sends POST to the endpoint, parses JSON response into steps |
| **RawAPI (Function)** | Calls the Python callable, parses dict/list response into steps |
| **LangChain** | Registers a `BaseCallbackHandler` to intercept every LLM call, tool call, and agent action |
| **OpenAI** | Creates thread/run, polls status, intercepts `requires_action` for tool calls via run steps API |
| **CrewAI** | Calls `crew.kickoff()`, parses `CrewOutput.tasks_output` into steps |
| **AutoGen** | Calls `user_proxy.initiate_chat()`, reads `chat_messages` post-execution |
| **LangGraph** | Uses `graph.stream()` for step-by-step capture, falls back to `graph.invoke()` |

### Failure & Latency Injection

Failure injection is implemented at the adapter level because each framework handles tool calls differently:

1. **RawAPI (Function):** Modifies step data before recording — changes `action` to `"error"` when the tool matches
2. **LangChain:** Intercepts in `on_agent_action` callback — records an error step and sets `_injected_failure` flag to skip `on_tool_end`
3. **OpenAI:** Checks during `_resolve_tool_calls` — returns an error JSON as the tool output
4. **LangGraph/AutoGen/CrewAI:** Checks tool names during step recording and replaces with error steps

Latency injection is simpler — each adapter adds `time.sleep(delay_ms / 1000)` before tool execution.

---

## AgentTrajectory: The Central Data Structure

`AgentTrajectory` is the heart of AgentBench. It's a complete record of everything the agent did during a test run.

### Structure

```python
@dataclass
class AgentTrajectory:
    run_id: str              # UUID
    test_name: str           # "SuiteName.test_method"
    agent_name: str          # "checkout-agent"
    input_prompt: str        # "Buy me a shirt"
    steps: list[AgentStep]   # Every step the agent took
    final_response: str      # Agent's last message
    total_latency_ms: float  # Total execution time
    total_tokens: int        # Token usage
    total_cost_usd: float    # Estimated cost
    completed: bool          # Did it finish successfully?
    error: str | None        # Top-level error
```

### AgentStep

Each step records:

```python
@dataclass
class AgentStep:
    step_number: int       # 0, 1, 2, ...
    action: str            # "tool_call" | "llm_response" | "error" | "retry"
    tool_name: str | None  # "search_api"
    tool_input: dict       # {"query": "..."}
    tool_output: Any       # "Found 3 results"
    reasoning: str | None  # "I need to search for..."
    response: str | None   # "Here are the results..."
    latency_ms: float      # 142.5
    error: str | None      # "API timeout"
    timestamp: float       # Unix timestamp
```

### Key Properties

- `step_count` — Total number of steps
- `tool_calls` — Filtered list of only `tool_call` steps
- `tool_calls_by_name(name)` — Tool calls filtered by name (used by `to_use_tool()`)
- `to_dict()` — Serializes to JSON for storage/diffing

The `exposed_data` property on `AgentStep` concatenates all text fields (`reasoning`, `response`, `tool_output`, `tool_input`) for PII scanning — this is what `to_not_expose()` checks against.

---

## Evaluation Pipeline

### LLM-as-Judge

The `JudgeEvaluator` enables subjective quality assessment:

```
AgentTrajectory → JudgeEvaluator.evaluate() → JudgeResult
```

**Flow:**
1. Select a **template** (e.g., `"appropriate_response"`, `"safety_check"`)
2. Format the trajectory data into the template (prompt, response, steps, tool calls)
3. Check the **cache** (SHA256 hash of model + prompt)
4. If not cached, **call the LLM** (OpenAI or Anthropic)
5. **Parse** the JSON response (handles markdown code blocks)
6. Compute **confidence** based on distance from threshold:
   - Score ≥ 0.2 away from threshold → confidence 1.0 (high)
   - Score 0.1–0.2 away → confidence 0.7 (medium)
   - Score < 0.1 away → confidence 0.4 (low)
7. Cache and return the `JudgeResult`

**Batch evaluation** reuses the cache across multiple trajectories.

### Metrics Collection

`MetricsCollector` extracts quantitative metrics from trajectories:

```
AgentTrajectory → MetricsCollector.collect() → RunMetrics
                                      ↓
                              MetricsCollector.aggregate() → aggregated dict
```

Metrics tracked per run:
- Step count, tool call count, tool calls by name
- Total/average/max latency
- Token usage, estimated cost
- Error count, retry count
- Completion status

Aggregation provides totals, averages, and success rates across all collected runs.

### Trajectory Diffing

`TrajectoryDiff` compares two trajectories to detect behavioral drift:

```
Golden Trajectory + Current Trajectory → TrajectoryDiff.compare() → DiffResult
```

**Severity classification:**

| Severity | Triggers |
|---|---|
| **CRITICAL** | Different tool called, different action type, new error, PII exposure |
| **WARNING** | Different step count, different tool inputs, extra/missing steps |
| **INFO** | Different response wording, different final response |
| **MATCH** | Steps are equivalent |

This is used by `agentbench diff` to catch regressions: record a golden run, then compare future runs against it.

---

## Parallel Execution

The runner supports parallel execution at two levels:

1. **Suite-level parallelism:** When `parallel > 1` and there are multiple suites, suites run in parallel via `ThreadPoolExecutor`
2. **Test-level parallelism:** Within a suite, individual test methods run in parallel

Fresh instances per test prevent state corruption. Thread-local `_active_test` ensures `expect()` registration works correctly in parallel threads.

---

## CLI Architecture

The CLI is built with [Typer](https://typer.tiangolo.com/) and [Rich](https://rich.readthedocs.io/):

```
agentbench (Typer app)
├── run       → TestRunner.run() → console output + optional JSON report
├── record    → AgentTest.run() → save trajectory JSON
├── diff      → TrajectoryDiff.compare() → rich-formatted diff
├── init      → scaffold_project() → create project files
├── watch     → TestRunner + watchdog observer → re-run on file changes
├── report    → generate_html_report() → self-contained HTML
└── list      → TestRunner.discover_suites() → print test tree
```

Exit code 0 = all passed, 1 = failures detected. This makes it CI/CD-friendly.

---

## Design Decisions

### Why fresh instances per test?

Shared state between tests causes flaky failures. Creating a new instance per test method guarantees isolation.

### Why thread-local for expect() tracking?

Python doesn't have a clean way to intercept all assertions in a test method. By using thread-local storage, `expect()` can register itself with the running test without requiring explicit assertion collection.

### Why the adapter pattern?

Agent frameworks have wildly different APIs. The adapter pattern lets us normalize all of them into the same `AgentTrajectory` format, so assertions and tooling work identically regardless of framework.

### Why dataclasses over Pydantic?

AgentBench is a testing tool, not a web service. Dataclasses are simpler, have zero dependencies, and are sufficient for the data shapes we need. Pydantic would add unnecessary weight.

### Why JSON for storage?

Trajectories are naturally JSON-serializable (via `to_dict()`). JSON is human-readable, diffable, and works with any tooling. No database needed.
