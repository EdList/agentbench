# Getting Started with AgentBench

**5-minute quickstart guide** — install, write your first test, run it, and understand the output.

---

## Installation

```bash
# Core framework
pip install agentbench

# With optional framework support
pip install agentbench[langchain]     # LangChain agents
pip install agentbench[openai]        # OpenAI Assistants
pip install agentbench[crewai]        # CrewAI crews
pip install agentbench[all]           # Everything
```

> **Requirements:** Python 3.11+

---

## Scaffold a Project

```bash
agentbench init my-agent-tests
cd my-agent-tests
```

This creates:

```
my-agent-tests/
├── test_agent.py         # Your test file (edit this)
├── agentbench.yaml       # Configuration
├── requirements.txt
└── .agentbench/
    └── trajectories/     # Recorded golden trajectories
```

You can specify a scaffold template:

```bash
agentbench init my-tests --framework langchain
agentbench init my-tests --framework raw_api   # default
```

When you run tests from that project, AgentBench automatically loads the local `agentbench.yaml` file unless you pass `--config` explicitly.

---

## Write Your First Test

Open `test_agent.py` and define your agent and test class:

```python
from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter


def my_agent(prompt: str, context: dict | None = None) -> dict:
    """Replace this with your actual agent logic."""
    return {
        "response": f"Echo: {prompt}",
        "steps": [
            {"action": "llm_response", "response": f"Echo: {prompt}"},
        ],
    }


adapter = RawAPIAdapter(func=my_agent)


class MyAgentTest(AgentTest):
    agent = "my-agent"
    adapter = adapter

    def test_basic_response(self):
        """Agent should respond to basic prompts."""
        result = self.run("Hello, how are you?")
        expect(result).to_complete()
        expect(result).to_respond_with("Echo")

    def test_completes_quickly(self):
        """Agent should respond within 10 steps."""
        result = self.run("Tell me a joke")
        expect(result).to_complete_within(steps=10)

    def test_no_errors(self):
        """Agent should not produce errors on normal input."""
        result = self.run("What is 2 + 2?")
        expect(result).to_have_no_errors()
        expect(result).to_complete()
```

### Key concepts at a glance

| Concept | What it does |
|---|---|
| `AgentTest` | Base class — subclass it to define a test suite |
| `self.run(prompt)` | Sends a prompt to your agent, returns a full `AgentTrajectory` |
| `expect(trajectory)` | Starts a fluent assertion chain |
| `.to_complete()` | Asserts the agent finished without error |
| `.to_use_tool("name")` | Asserts the agent called a specific tool |
| `.to_not_expose("secret")` | Asserts the agent never leaked sensitive data |

---

## Run Your Tests

```bash
# Run all tests in the current directory
agentbench run

# Run a specific test file
agentbench run test_agent.py

# Run a directory of tests
agentbench run ./tests

# Verbose mode — show every assertion result
agentbench run -v

# Filter tests by name pattern
agentbench run -f "basic"

# Save a JSON report for CI
agentbench run -r report.json

# Run tests in parallel (4 workers)
agentbench run -p 4
```

---

## Understanding the Output

Here's what a typical run looks like:

```
🧪 AgentBench
Testing what your agent actually does

============================================================
  Suite: MyAgentTest
  3 passed, 0 failed, 0 skipped
  Duration: 0.1s
============================================================
  ✓ test_basic_response (0.03s)
  ✓ test_completes_quickly (0.02s)
  ✓ test_no_errors (0.04s)

Total: 3 passed, 0 failed, 3 tests
Duration: 0.1s
```

### With verbose mode (`-v`):

```
  ✓ test_basic_response (0.03s)
    ✓ Agent completed successfully
    ✓ Agent response contains 'Echo'
  ✓ test_completes_quickly (0.02s)
    ✓ Agent completed in 1 steps (limit: 10)
  ✓ test_no_errors (0.04s)
    ✓ Agent had 0 error(s)
    ✓ Agent completed successfully
```

### When tests fail:

```
============================================================
  Suite: CheckoutAgentTest
  1 passed, 2 failed, 0 skipped
  Duration: 2.3s
============================================================
  ✗ test_completes_checkout (2.1s)
    → Agent called 'payment_api' 0 time(s) (expected: 1)
  ✗ test_no_pii_exposure (0.2s)
    → Agent exposed 'credit_card_number' in steps [2, 3]
  ✓ test_handles_return (0.1s)
```

The exit code is **0** if all tests pass, **1** if any fail — perfect for CI/CD pipelines.

---

## Next Steps

- **[API Reference](api-reference.md)** — Complete reference for all public APIs
- **[Adapters Guide](adapters.md)** — Framework-specific setup for LangChain, OpenAI, CrewAI, etc.
- **[Examples Gallery](examples.md)** — Real-world test suites for e-commerce, support, RAG, and more
- **[Architecture](architecture.md)** — How the engine works under the hood

---

## Common Patterns

### Testing failure recovery

```python
def test_retries_on_api_failure(self):
    result = self.run(
        "Search for flights",
        inject_tool_failure="search_api",
        fail_times=2,
    )
    expect(result).to_retry(max_attempts=3)
```

### Testing workflow order

```python
def test_follows_correct_order(self):
    result = self.run("Book me a flight")
    expect(result).to_follow_workflow(["search", "select", "payment", "confirm"])
```

### Testing PII safety

```python
def test_no_credit_card_leak(self):
    result = self.run("My card is 4111111111111111, buy a shirt")
    expect(result).to_not_expose("4111111111111111")
```

### Parametric tests

```python
from agentbench import parametrize

class SearchTest(AgentTest):
    agent = "search-agent"
    adapter = my_adapter

    @parametrize("query", ["Python tutorials", "Machine learning basics", ""])
    def test_handles_various_queries(self, query):
        result = self.run(query)
        expect(result).to_complete()
```

### Using LLM-as-Judge

```python
from agentbench.evaluation import JudgeEvaluator

judge = JudgeEvaluator(provider="openai", model="gpt-4o-mini")

def test_response_quality(self):
    result = self.run("Explain quantum computing")
    judge_result = judge.evaluate(result, template="appropriate_response")
    assert judge_result.passed, f"Judge score: {judge_result.score}"
```
