# 🧪 AgentBench

**Behavioral testing framework for AI agents.**

Promptfoo tests prompts. We test behaviors.

[![Tests](https://img.shields.io/github/actions/workflow/status/EdList/agentbench/test.yml?branch=main&label=tests&logo=github)](https://github.com/EdList/agentbench/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Why AgentBench?

You've built an AI agent. It works... mostly. But then you change the prompt and:
- 😱 It loops infinitely instead of completing
- 🔓 It passes credit card numbers to a logging tool
- 🔄 It calls the wrong API entirely
- 💥 It crashes on edge cases you didn't think of

**AgentBench catches these before your users do.**

We're not another prompt testing tool. We test the **full behavioral trajectory** of your agent — every step, every tool call, every decision it makes.

## Quick Start

```bash
pip install agentbench
agentbench init my-agent-tests
cd my-agent-tests
# Edit test_agent.py with your agent details
agentbench run
```

## Write Tests Like This

```python
from agentbench import AgentTest, expect
from agentbench.adapters import RawAPIAdapter

def my_agent(prompt, context=None):
    # Your agent logic here
    return {"response": "...", "steps": [...]}

adapter = RawAPIAdapter(func=my_agent)

class CheckoutAgentTest(AgentTest):
    agent = "checkout-agent"
    adapter = adapter

    def test_completes_checkout(self):
        result = self.run("Buy me a blue shirt, size M")
        expect(result).to_complete_within(steps=10)
        expect(result).to_use_tool("payment_api", times=1)
        expect(result).to_not_expose("credit_card_number")

    def test_handles_out_of_stock(self):
        result = self.run("Buy me a unicorn onesie")
        expect(result).to_not_use_tool("payment_api")
        expect(result).to_respond_with("out of stock")

    def test_retries_on_failure(self):
        result = self.run(
            "Book a flight to Tokyo",
            inject_tool_failure="search_api",
            fail_times=2,
        )
        expect(result).to_retry(max_attempts=3)
```

## CLI Commands

```bash
# Run all tests in a directory
agentbench run ./tests

# Run with verbose assertion output
agentbench run ./tests -v

# Filter tests by name pattern
agentbench run ./tests -f "checkout"

# Record a golden trajectory
agentbench record ./tests "Book a flight to Paris" -o golden.json

# Diff current run against golden
agentbench diff golden.json

# Save JSON report for CI
agentbench run ./tests -r report.json
```

## Assertions

| Assertion | What it checks |
|-----------|---------------|
| `to_complete()` | Agent finished without error |
| `to_complete_within(steps=N)` | Agent completed in ≤ N steps |
| `to_use_tool(name, times=N)` | Agent called a specific tool |
| `to_not_use_tool(name)` | Agent never called a tool |
| `to_not_expose(pattern)` | Agent never exposed sensitive data |
| `to_respond_with(text)` | Final response contains text |
| `to_retry(max_attempts=N)` | Agent retried within limits |
| `to_follow_workflow([steps])` | Agent called tools in order |
| `to_have_no_errors()` | No step had an error |

## Framework Support

| Framework | Adapter | Status |
|-----------|---------|--------|
| HTTP API | `RawAPIAdapter` | ✅ Ready |
| Python function | `RawAPIAdapter(func=...)` | ✅ Ready |
| LangChain | `LangChainAdapter` | ✅ Ready |
| OpenAI Assistants | `OpenAIAdapter` | ✅ Ready |
| CrewAI | `CrewAIAdapter` | ✅ Ready |
| AutoGen | `AutoGenAdapter` | ✅ Ready |
| LangGraph | `LangGraphAdapter` | ✅ Ready |

## Features

- 🎯 **Behavioral assertions** — test WHAT the agent does, not just what it says
- 📼 **Trajectory recording** — record golden runs, diff against regressions
- 💉 **Failure injection** — simulate broken APIs, timeouts, rate limits
- 🧑‍⚖️ **LLM-as-Judge** — use LLMs to evaluate subjective quality
- 🔧 **CI/CD ready** — JSON reports, exit codes, `--filter` for selective runs
- 🐳 **Docker sandbox** — isolated agent execution with resource limits (optional)

## Roadmap

- [x] Core test engine + assertion API
- [x] Raw API + LangChain adapters
- [x] Trajectory recording & diffing
- [x] CLI (run, record, diff, init)
- [x] Failure injection for function and HTTP modes
- [x] Verbose mode with assertion details
- [x] OpenAI Assistants adapter
- [x] Parametric tests
- [x] Parallel test execution
- [x] Test fixtures and hooks (setup/teardown)
- [x] Watch mode (file watcher)
- [x] HTML report generation
- [x] `agentbench list` command
- [x] CrewAI / AutoGen / LangGraph adapters
- [x] LLM-as-Judge: confidence scoring, caching, batch eval, custom providers
- [x] GitHub Action CI/CD integration
- [x] GitLab CI template
- [x] Cloud API scaffold (FastAPI + SQLAlchemy + JWT auth)
- [x] Complete documentation (6 guides)
- [ ] Adversarial test generation
- [ ] Property-based testing
- [ ] Multi-agent test harness
- [ ] Web dashboard

## Contributing

AgentBench is open source and we welcome contributions!

```bash
git clone https://github.com/EdList/agentbench.git
cd agentbench
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](docs/contributing.md) for detailed guidelines.

## Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | 5-minute quickstart |
| [API Reference](docs/api-reference.md) | Complete public API docs |
| [Adapters](docs/adapters.md) | Framework-specific guides |
| [Examples](docs/examples.md) | 5+ real-world test suites |
| [Architecture](docs/architecture.md) | How the engine works |
| [Contributing](docs/contributing.md) | Dev setup & PR process |

## Cloud API

AgentBench includes an optional cloud API server:

```bash
pip install agentbench[server]
agentbench serve --port 8000
```

See `agentbench/server/` for the FastAPI scaffold with JWT auth, test run management, and trajectory storage.

## License

MIT
