# API Reference

Complete reference for all AgentBench public APIs.

---

## Core

### `AgentTest`

**Module:** `agentbench.core.test`

Base class for writing behavioral agent tests. Subclass this to define test suites.

```python
from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


class MyTest(AgentTest):
    agent = "my-agent"                          # Agent identifier (string)
    adapter = RawAPIAdapter(func=my_func)       # Agent adapter instance
    config = None                               # Optional AgentBenchConfig override

    def test_something(self):
        result = self.run("Hello")
        expect(result).to_complete()
```

#### Class Attributes

| Attribute | Type | Description |
|---|---|---|
| `agent` | `str` | Human-readable name for the agent under test |
| `adapter` | `AgentAdapter \| None` | The adapter that wraps your agent framework |
| `config` | `AgentBenchConfig \| None` | Per-suite config override (optional) |

#### Instance Methods

##### `run(prompt, *, inject_tool_failure=None, fail_times=1, inject_latency=None, max_steps=None, timeout_seconds=None, context=None)`

Run the agent with a prompt and return the full trajectory.

| Parameter | Type | Default | Description |
|---|---|---|---|
| `prompt` | `str` | *required* | The user input to send to the agent |
| `inject_tool_failure` | `str \| ToolFailureInjection \| None` | `None` | Tool name or config to inject failures |
| `fail_times` | `int` | `1` | How many times the tool should fail |
| `inject_latency` | `str \| ToolLatencyInjection \| None` | `None` | Tool name or config to inject delays |
| `max_steps` | `int \| None` | config default | Maximum number of agent steps |
| `timeout_seconds` | `float \| None` | config default | Maximum wall time for the run |
| `context` | `dict \| None` | `None` | Additional context to pass to the agent |

**Returns:** `AgentTrajectory`

##### `trajectory` (property)

The trajectory from the most recent `run()` call.

**Type:** `AgentTrajectory | None`

#### Lifecycle Hooks

Override these methods in your subclass:

```python
class MyTest(AgentTest):
    agent = "my-agent"
    adapter = my_adapter

    def setup_class(self):
        """Called once before all tests in the suite."""
        self.shared_resource = create_resource()

    def teardown_class(self):
        """Called once after all tests in the suite."""
        self.shared_resource.cleanup()

    def setup(self):
        """Called before each test method."""
        self.test_data = load_data()

    def teardown(self):
        """Called after each test method (even on failure)."""
        cleanup()
```

---

### `AgentTrajectory`

**Module:** `agentbench.core.test`

Complete execution trajectory of an agent run. Returned by `AgentTest.run()`.

#### Fields

| Field | Type | Description |
|---|---|---|
| `run_id` | `str` | Unique identifier (UUID) |
| `test_name` | `str` | Name of the test that produced this trajectory |
| `agent_name` | `str` | Name of the agent |
| `input_prompt` | `str` | The original user prompt |
| `steps` | `list[AgentStep]` | Every step the agent took |
| `final_response` | `str` | The agent's final text response |
| `total_latency_ms` | `float` | Total execution time |
| `total_tokens` | `int` | Token usage (if tracked) |
| `total_cost_usd` | `float` | Estimated cost |
| `completed` | `bool` | Whether the agent finished successfully |
| `error` | `str \| None` | Error message if the run failed |

#### Properties & Methods

| Method | Returns | Description |
|---|---|---|
| `step_count` | `int` | Number of steps in the trajectory |
| `tool_calls` | `list[AgentStep]` | All tool-call steps |
| `tool_calls_by_name(name)` | `list[AgentStep]` | Tool calls filtered by tool name |
| `to_dict()` | `dict` | Serialize to a JSON-compatible dict |

---

### `AgentStep`

**Module:** `agentbench.core.test`

A single step in an agent's execution trajectory.

#### Fields

| Field | Type | Description |
|---|---|---|
| `step_number` | `int` | Zero-indexed step number |
| `action` | `str` | `"tool_call"`, `"llm_response"`, `"error"`, or `"retry"` |
| `tool_name` | `str \| None` | Name of the tool called (if action is `tool_call`) |
| `tool_input` | `dict \| None` | Arguments passed to the tool |
| `tool_output` | `Any` | Tool return value |
| `reasoning` | `str \| None` | Agent's reasoning text |
| `response` | `str \| None` | Text response |
| `latency_ms` | `float` | Step duration in milliseconds |
| `error` | `str \| None` | Error message (if action is `error`) |
| `timestamp` | `float` | Unix timestamp |

#### Properties

| Property | Returns | Description |
|---|---|---|
| `exposed_data` | `str` | All text data exposed in this step (for PII checks) |

---

### `ToolFailureInjection`

**Module:** `agentbench.core.test`

Configuration for injecting tool failures during a test.

```python
from agentbench.core.test import ToolFailureInjection

injection = ToolFailureInjection(
    tool_name="payment_api",
    fail_times=2,
    error_message="Service unavailable",
    error_type="connection_error",
)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `tool_name` | `str` | *required* | Tool to fail |
| `fail_times` | `int` | `1` | Number of failures before recovery |
| `error_message` | `str` | `"Tool unavailable"` | Error message to inject |
| `error_type` | `str` | `"connection_error"` | Category of error |

---

### `ToolLatencyInjection`

**Module:** `agentbench.core.test`

Configuration for injecting latency into tool calls.

```python
from agentbench.core.test import ToolLatencyInjection

injection = ToolLatencyInjection(tool_name="search_api", delay_ms=2000)
```

| Field | Type | Default | Description |
|---|---|---|---|
| `tool_name` | `str` | *required* | Tool to slow down |
| `delay_ms` | `int` | `1000` | Delay in milliseconds |

---

## Assertions

### `expect(trajectory)`

**Module:** `agentbench.core.assertions`

Create an expectation chain for asserting on an agent trajectory.

```python
result = test.run("Buy a shirt")
expect(result).to_complete()
expect(result).to_use_tool("payment_api", times=1)
expect(result).to_not_expose("credit_card")
```

**Returns:** `Expectation`

---

### `Expectation`

**Module:** `agentbench.core.assertions`

Fluent assertion builder for agent trajectories. Chain methods to make multiple assertions.

#### Properties

| Property | Returns | Description |
|---|---|---|
| `results` | `list[AssertionResult]` | All assertion results collected so far |
| `all_passed` | `bool` | Whether all assertions passed |

#### Completion Assertions

| Method | Description |
|---|---|
| `.to_complete()` | Assert the agent completed without error |
| `.to_complete_within(steps=N)` | Assert the agent completed in ≤ N steps |

#### Tool Usage Assertions

| Method | Description |
|---|---|
| `.to_use_tool(name, *, times=None)` | Assert a tool was called (optionally exact count) |
| `.to_not_use_tool(name)` | Assert a tool was never called |

#### Response Assertions

| Method | Description |
|---|---|
| `.to_respond_with(text)` | Assert the final response contains text (case-insensitive) |

#### Behavior Assertions

| Method | Description |
|---|---|
| `.to_retry(max_attempts=N)` | Assert the agent retried and completed within N attempts |
| `.to_follow_workflow(steps)` | Assert tool calls appeared in the given order |
| `.to_have_no_errors()` | Assert no step had an error |

#### Privacy Assertions

| Method | Description |
|---|---|
| `.to_not_expose(pattern)` | Assert the pattern never appeared in any step's data |

#### Negation

| Method | Description |
|---|---|
| `.to_not` | Negates the *next* assertion only |

```python
expect(result).to_not.to_complete()        # Assert agent did NOT complete
```

#### Step-Level Assertions

| Method | Description |
|---|---|
| `.step(index)` | Returns a `StepAssertion` for the step at `index` |

```python
expect(result).step(0).used_tool("search")
expect(result).step(1).responded_with("found")
expect(result).step(0).has_no_error()
```

---

### `StepAssertion`

**Module:** `agentbench.core.assertions`

Assertions about a single agent step. All methods return `self` for chaining.

| Method | Description |
|---|---|
| `.used_tool(name)` | Assert this step called a specific tool |
| `.responded_with(text)` | Assert this step's response contains the text |
| `.has_no_error()` | Assert this step has no error |
| `.results` | `list[AssertionResult]` — collected results |

---

### `AssertionResult`

**Module:** `agentbench.core.assertions`

Result of a single assertion.

| Field | Type | Description |
|---|---|---|
| `passed` | `bool` | Whether the assertion passed |
| `message` | `str` | Human-readable description |
| `assertion_type` | `str` | Category (e.g., `"completion"`, `"tool_count"`) |
| `details` | `dict` | Structured details about the assertion |

Supports `bool(result)` and `str(result)` → `"✓ message"` or `"✗ message"`.

---

## Adapters

All adapters implement the `AgentAdapter` abstract base class.

### `AgentAdapter` (Base Class)

**Module:** `agentbench.adapters.base`

```python
from agentbench.adapters.base import AgentAdapter
```

| Method | Description |
|---|---|
| `run(prompt, trajectory, ...)` | Execute the agent and return a populated `AgentTrajectory` |
| `get_available_tools()` | Return list of tool names available to the agent |

### `RawAPIAdapter`

**Module:** `agentbench.adapters.raw_api`

Test agents via HTTP endpoint or Python callable.

```python
# HTTP mode
adapter = RawAPIAdapter(
    endpoint="http://localhost:8000/chat",
    headers={"Authorization": "Bearer xxx"},
    tools=["search", "calculator"],
    timeout=30.0,
)

# Function mode
def my_agent(prompt: str, context: dict | None = None) -> dict:
    return {"response": "...", "steps": [...]}

adapter = RawAPIAdapter(func=my_agent, tools=["search"])
```

| Parameter | Type | Description |
|---|---|---|
| `endpoint` | `str \| None` | HTTP URL for the agent API |
| `headers` | `dict \| None` | HTTP headers (e.g., auth) |
| `func` | `Callable \| None` | Python callable for the agent |
| `tools` | `list[str] \| None` | Explicit list of tool names |
| `timeout` | `float` | HTTP request timeout (default 30s) |

### `LangChainAdapter`

**Module:** `agentbench.adapters.langchain`

```python
from agentbench.adapters import LangChainAdapter

adapter = LangChainAdapter(agent_executor, tools=["search"])
```

| Parameter | Type | Description |
|---|---|---|
| `agent_executor` | `AgentExecutor` | LangChain AgentExecutor instance |
| `tools` | `list[str] \| None` | Explicit tool names (auto-detected if omitted) |

### `OpenAIAdapter`

**Module:** `agentbench.adapters.openai`

```python
from agentbench.adapters import OpenAIAdapter
from openai import OpenAI

client = OpenAI()
adapter = OpenAIAdapter(
    client=client,
    assistant_id="asst_abc123",
    tools=["search", "calculator"],
    poll_interval=0.5,
)
```

| Parameter | Type | Description |
|---|---|---|
| `client` | `OpenAI` | OpenAI client instance |
| `assistant_id` | `str` | OpenAI Assistant ID |
| `tools` | `list[str] \| None` | Explicit tool names |
| `poll_interval` | `float` | Seconds between status polls (default 0.5) |

### `CrewAIAdapter`

**Module:** `agentbench.adapters.crewai`

```python
from agentbench.adapters import CrewAIAdapter
from crewai import Crew, Agent, Task

crew = Crew(agents=[agent], tasks=[task])
adapter = CrewAIAdapter(crew, tools=["search"])
```

| Parameter | Type | Description |
|---|---|---|
| `crew` | `Crew` | CrewAI Crew instance |
| `tools` | `list[str] \| None` | Explicit tool names |

### `AutoGenAdapter`

**Module:** `agentbench.adapters.autogen`

```python
from agentbench.adapters import AutoGenAdapter

adapter = AutoGenAdapter(
    assistant=assistant_agent,
    user_proxy=user_proxy,
    group_chat_manager=None,       # optional
    tools=["search", "calculator"],
)
```

| Parameter | Type | Description |
|---|---|---|
| `assistant` | `AssistantAgent` | Primary AutoGen assistant agent |
| `user_proxy` | `UserProxyAgent` | User proxy for initiating conversations |
| `group_chat_manager` | `GroupChatManager \| None` | Optional group chat manager |
| `tools` | `list[str] \| None` | Explicit tool names |

### `LangGraphAdapter`

**Module:** `agentbench.adapters.langgraph`

```python
from agentbench.adapters import LangGraphAdapter

adapter = LangGraphAdapter(
    graph=compiled_graph,
    tools=["search"],
    node_name_map={"tools": "search"},  # optional semantic mapping
)
```

| Parameter | Type | Description |
|---|---|---|
| `graph` | `CompiledGraph` | A compiled LangGraph graph |
| `tools` | `list[str] \| None` | Explicit tool names |
| `node_name_map` | `dict \| None` | Map graph node names to semantic tool names |

---

## Parametric Tests

### `@parametrize(arg_name, arg_values)`

**Module:** `agentbench.core.parametrize`

Decorator to run a test method once per parameter value.

```python
from agentbench import parametrize

class SearchTest(AgentTest):
    agent = "search-agent"
    adapter = my_adapter

    @parametrize("query", ["Python", "Machine learning", ""])
    def test_handles_queries(self, query):
        result = self.run(query)
        expect(result).to_complete()

    @parametrize("language", ["en", "es", "fr"])
    def test_multilingual(self, language):
        result = self.run(f"Search in {language}")
        expect(result).to_complete_within(steps=5)
```

The runner generates test names like `test_handles_queries[Python]`, `test_handles_queries[Machine learning]`, etc.

| Parameter | Type | Description |
|---|---|---|
| `arg_name` | `str` | Parameter name injected into the test method |
| `arg_values` | `list \| tuple` | Values to iterate over |

---

## Fixtures

### `@fixture(func=None, *, scope="test")`

**Module:** `agentbench.core.fixtures`

Decorator to create a reusable test fixture.

```python
from agentbench import fixture

@fixture
def db_connection():
    conn = connect()
    yield conn
    conn.close()

@fixture(scope="suite")
def test_data():
    return load_test_data()
```

| Parameter | Type | Default | Description |
|---|---|---|---|
| `func` | `Callable \| None` | `None` | The fixture function |
| `scope` | `str` | `"test"` | Lifetime: `"test"`, `"suite"`, or `"session"` |

### `Fixture`

**Module:** `agentbench.core.fixtures`

Fixture object created by the `@fixture` decorator.

| Method | Returns | Description |
|---|---|---|
| `setup()` | `Any` | Execute the fixture; supports generator fixtures (yield) |
| `teardown()` | `None` | Run teardown for generator fixtures |
| `scope` | `str` | Fixture lifetime scope |
| `__call__()` | `Any` | Shortcut for `setup()` |

---

## Configuration

### `AgentBenchConfig`

**Module:** `agentbench.core.config`

```python
from agentbench.core.config import AgentBenchConfig

# Default config
config = AgentBenchConfig()

# From YAML
config = AgentBenchConfig.from_yaml("agentbench.yaml")

# Save to YAML
config.to_yaml("agentbench.yaml")
```

#### Fields

| Field | Type | Default | Description |
|---|---|---|---|
| `max_steps` | `int` | `50` | Maximum agent steps per test |
| `timeout_seconds` | `float` | `120.0` | Maximum execution time per test |
| `max_retries` | `int` | `3` | Maximum retry attempts |
| `parallel_workers` | `int` | `1` | Number of parallel workers |
| `sandbox` | `SandboxConfig` | — | Docker sandbox settings |
| `judge` | `JudgeConfig` | — | LLM-as-Judge settings |
| `trajectories_dir` | `Path` | `.agentbench/trajectories` | Directory for saved trajectories |
| `results_dir` | `Path` | `.agentbench/results` | Directory for test results |
| `default_agent` | `str` | `""` | Default agent name |
| `default_adapter` | `str` | `"raw_api"` | Default adapter type |

### `SandboxConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `True` | Enable Docker sandbox |
| `image` | `str` | `"agentbench-runner:latest"` | Docker image |
| `memory_limit` | `str` | `"512m"` | Container memory limit |
| `cpu_limit` | `float` | `1.0` | Container CPU limit |
| `network_enabled` | `bool` | `True` | Allow network access |
| `timeout_seconds` | `int` | `60` | Container timeout |
| `max_containers` | `int` | `10` | Maximum concurrent containers |

### `JudgeConfig`

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `bool` | `False` | Enable LLM-as-Judge |
| `provider` | `str` | `"openai"` | `"openai"`, `"anthropic"`, or `"custom"` |
| `model` | `str` | `"gpt-4o-mini"` | Judge model name |
| `api_key_env` | `str` | `""` | Environment variable for API key |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `max_tokens` | `int` | `500` | Max response tokens |
| `cost_limit_usd` | `float` | `1.0` | Max judge cost per test run |

---

## Test Runner

### `TestRunner`

**Module:** `agentbench.core.runner`

Discovers and executes agent test suites.

```python
from agentbench.core.runner import TestRunner

runner = TestRunner(config={"verbose": True, "parallel": 4})
result = runner.run("./tests")
print(result.summary())
```

| Parameter | Type | Description |
|---|---|---|
| `config` | `dict \| None` | Runner configuration |
| `config["verbose"]` | `bool` | Show step-by-step output |
| `config["filter"]` | `str \| None` | Regex pattern to filter tests |
| `config["parallel"]` | `int` | Number of parallel workers |
| `config["bench_config"]` | `AgentBenchConfig` | Bench config to pass to tests |

#### Methods

| Method | Returns | Description |
|---|---|---|
| `run(path)` | `RunResult` | Discover and run all suites in path |
| `run_suite(suite_class)` | `TestSuiteResult` | Run all tests in a single suite |
| `discover_suites(path)` | `list[type[AgentTest]]` | Find all AgentTest subclasses |

### `TestResult`

Result of a single test method.

| Property | Type | Description |
|---|---|---|
| `test_name` | `str` | Test method name |
| `suite_name` | `str` | Suite class name |
| `passed` | `bool` | Whether the test passed |
| `assertions` | `list[AssertionResult]` | All assertion results |
| `trajectory` | `AgentTrajectory \| None` | The recorded trajectory |
| `error` | `str \| None` | Exception message if the test errored |
| `duration_ms` | `float` | Test duration |
| `assertion_count` | `int` | Total assertions |
| `passed_assertions` | `int` | Passed assertions |
| `failed_assertions` | `list` | Failed assertions only |

### `TestSuiteResult`

Result of an entire test suite.

| Property | Type | Description |
|---|---|---|
| `suite_name` | `str` | Suite class name |
| `results` | `list[TestResult]` | Individual test results |
| `total_duration_ms` | `float` | Total suite duration |
| `passed` | `int` | Number of passed tests |
| `failed` | `int` | Number of failed tests |
| `skipped` | `int` | Number of skipped tests |
| `total` | `int` | Total test count |
| `all_passed` | `bool` | True if every test passed |
| `summary()` | `str` | Formatted summary string |

### `RunResult`

Result of a complete test run (multiple suites).

| Property | Type | Description |
|---|---|---|
| `suite_results` | `list[TestSuiteResult]` | Per-suite results |
| `total_duration_ms` | `float` | Total run duration |
| `total_passed` | `int` | Total passed tests |
| `total_failed` | `int` | Total failed tests |
| `total_tests` | `int` | Total test count |
| `all_passed` | `bool` | True if every test passed |
| `summary()` | `str` | Formatted summary string |

---

## Evaluation

### `JudgeEvaluator`

**Module:** `agentbench.evaluation.judge`

Use LLMs to evaluate subjective agent behaviors.

```python
from agentbench.evaluation import JudgeEvaluator

judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")
result = judge.evaluate(trajectory, template="appropriate_response")
print(result.score, result.passed, result.reasoning)
```

#### Constructor

| Parameter | Type | Default | Description |
|---|---|---|---|
| `provider` | `str` | `"openai"` | LLM provider: `"openai"`, `"anthropic"`, or `"custom"` |
| `model` | `str` | `"gpt-4o-mini"` | Model name |
| `api_key` | `str \| None` | `None` | API key (falls back to env vars) |
| `temperature` | `float` | `0.0` | Sampling temperature |
| `max_tokens` | `int` | `500` | Maximum response tokens |
| `cache_enabled` | `bool` | `True` | Cache judge responses |
| `custom_llm_call` | `Callable \| None` | `None` | Custom LLM call function |

#### Methods

| Method | Returns | Description |
|---|---|---|
| `evaluate(trajectory, template, criteria, threshold)` | `JudgeResult` | Evaluate a single trajectory |
| `evaluate_batch(trajectories, ...)` | `list[JudgeResult]` | Evaluate multiple trajectories |
| `clear_cache()` | `None` | Clear the evaluation cache |

#### Evaluate Parameters

| Parameter | Type | Default | Description |
|---|---|---|---|
| `trajectory` | `AgentTrajectory` | *required* | The trajectory to evaluate |
| `template` | `str` | `"appropriate_response"` | Judge prompt template |
| `criteria` | `str \| None` | `None` | Custom criteria (for `"custom"` template) |
| `threshold` | `float` | `0.7` | Minimum score to pass |

#### Built-in Templates

| Template | Evaluates |
|---|---|
| `"appropriate_response"` | Response quality and appropriateness |
| `"follows_instructions"` | Whether the agent followed user instructions |
| `"safety_check"` | PII leakage, harmful content, unauthorized tool use |
| `"custom"` | Custom criteria provided via `criteria` parameter |

#### Properties

| Property | Type | Description |
|---|---|---|
| `total_cost_usd` | `float` | Total cost of all judge calls |
| `total_calls` | `int` | Total LLM calls made |
| `cache_hits` | `int` | Number of cached responses reused |

### `JudgeResult`

**Module:** `agentbench.evaluation.judge`

| Field | Type | Description |
|---|---|---|
| `passed` | `bool` | Whether the score meets the threshold |
| `score` | `float` | Score from 0.0 to 1.0 |
| `reasoning` | `str` | The judge's explanation |
| `judge_model` | `str` | Model used for judging |
| `latency_ms` | `float` | Evaluation time |
| `cost_usd` | `float` | Cost of this evaluation |
| `confidence` | `float` | Judge confidence (0.0–1.0) |
| `details` | `dict` | Additional details (e.g., safety issues) |

---

### `MetricsCollector`

**Module:** `agentbench.evaluation.metrics`

Collect and aggregate metrics from agent trajectories.

```python
from agentbench.evaluation import MetricsCollector

collector = MetricsCollector()
metrics = collector.collect(trajectory)
print(metrics.summary())

# Aggregate across all runs
agg = collector.aggregate()
print(agg["success_rate"], agg["avg_steps"])
```

#### Methods

| Method | Returns | Description |
|---|---|---|
| `collect(trajectory)` | `RunMetrics` | Extract and store metrics from a trajectory |
| `aggregate()` | `dict` | Aggregate metrics across all collected runs |

### `RunMetrics`

| Field | Type | Description |
|---|---|---|
| `total_steps` | `int` | Total agent steps |
| `total_tool_calls` | `int` | Total tool calls |
| `tool_calls_by_name` | `dict[str, int]` | Tool calls grouped by name |
| `total_latency_ms` | `float` | Total execution time |
| `avg_step_latency_ms` | `float` | Average step latency |
| `max_step_latency_ms` | `float` | Slowest step latency |
| `total_tokens` | `int` | Token usage |
| `estimated_cost_usd` | `float` | Estimated cost |
| `errors` | `int` | Error count |
| `retries` | `int` | Retry count |
| `completed` | `bool` | Whether the run completed |
| `summary()` | `str` | Human-readable summary |

---

## Storage

### `TrajectoryStore`

**Module:** `agentbench.storage.trajectory`

Persist and load agent trajectories to/from disk.

```python
from agentbench.storage import TrajectoryStore

store = TrajectoryStore(".agentbench/trajectories")
store.save(trajectory.to_dict(), name="golden-checkout")
data = store.load("golden-checkout")
names = store.list()
store.delete("old-run")
```

| Method | Returns | Description |
|---|---|---|
| `save(data, name)` | `Path` | Save a trajectory dict to JSON |
| `load(name)` | `dict` | Load a trajectory from JSON |
| `list()` | `list[str]` | List all saved trajectory names |
| `delete(name)` | `None` | Delete a saved trajectory |

### `TrajectoryDiff`

**Module:** `agentbench.storage.trajectory`

Compare two trajectories and identify behavioral drift.

```python
from agentbench.storage import TrajectoryDiff

differ = TrajectoryDiff()
result = differ.compare(golden_data, current_data)
print(result.format_output())
print(result.has_critical)
```

| Method | Returns | Description |
|---|---|---|
| `compare(golden, current)` | `DiffResult` | Compare two trajectory dicts |

### `DiffResult`

| Field | Type | Description |
|---|---|---|
| `golden_name` | `str` | Name of the golden trajectory |
| `current_name` | `str` | Name of the current trajectory |
| `step_diffs` | `list[StepDiff]` | Per-step differences |
| `summary` | `dict[str, int]` | Count of each severity level |
| `has_critical` | `bool` | Whether any critical diffs exist |
| `has_warnings` | `bool` | Whether any warnings exist |
| `format_output()` | `str` | Rich-formatted diff output |

### `StepDiff`

| Field | Type | Description |
|---|---|---|
| `step_number` | `int` | Step index |
| `severity` | `str` | `"critical"`, `"warning"`, `"info"`, or `"match"` |
| `field` | `str` | Field that differs |
| `golden_value` | `Any` | Value in the golden trajectory |
| `current_value` | `Any` | Value in the current trajectory |
| `message` | `str` | Human-readable description |

---

## CLI

### Commands

| Command | Description |
|---|---|
| `agentbench run [PATH]` | Run agent test suites |
| `agentbench record AGENT PROMPT` | Record a golden trajectory |
| `agentbench diff GOLDEN` | Diff current run against golden |
| `agentbench init [NAME]` | Scaffold a new test project |
| `agentbench watch [PATH]` | Watch files and re-run on changes |
| `agentbench report JSON` | Generate HTML report from JSON |
| `agentbench list [PATH]` | Discover and list test suites |

### `run` Options

| Flag | Short | Description |
|---|---|---|
| `--config` | `-c` | Path to config YAML |
| `--verbose` | `-v` | Show step-by-step output |
| `--filter` | `-f` | Filter tests by name pattern (regex) |
| `--parallel` | `-p` | Number of parallel workers |
| `--report` | `-r` | Output file for JSON report |

### `init` Options

| Flag | Short | Description |
|---|---|---|
| `--framework` | `-f` | Framework: `raw_api`, `langchain`, `openai`, `crewai`, `autogen`, `langgraph` |
| `--path` | `-p` | Output directory |

### `record` Options

| Flag | Short | Description |
|---|---|---|
| `--output` | `-o` | Output file path |
| `--name` | `-n` | Name for the recording |

### `diff` Options

| Flag | Short | Description |
|---|---|---|
| `--current` | `-c` | Path to current trajectory (auto-runs if omitted) |
| `--agent` | `-a` | Path to agent test directory |

### `watch` Options

| Flag | Short | Description |
|---|---|---|
| `--filter` | `-f` | Filter tests by name pattern |
| `--config` | `-c` | Path to config YAML |
| `--verbose` | `-v` | Show step-by-step output |

### Exit Codes

| Code | Meaning |
|---|---|
| `0` | All tests passed |
| `1` | One or more tests failed (or critical diff found) |
