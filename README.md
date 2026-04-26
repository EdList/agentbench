# 🧪 AgentBench

**`pytest` for AI agent behaviors.**

Promptfoo tests prompts. We test *behaviors* — every step, every tool call, every decision your agent makes.

[![Tests](https://img.shields.io/github/actions/workflow/status/EdList/agentbench/test.yml?branch=main&label=tests&logo=github)](https://github.com/EdList/agentbench/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## See It In Action

```
$ agentbench init my-agent-tests --framework langchain
✓ Created test suite with LangChain adapter

$ cd my-agent-tests

$ agentbench run -v
Running 6 tests against checkout-agent...

  ✓ completes_checkout_within_10_steps     8 steps  2.3s
  ✓ handles_out_of_stock_gracefully        3 steps  0.8s
  ✓ retries_on_search_api_failure          5 steps  4.1s
  ✗ does_not_expose_credit_card_number     FAILED
    → Step 5: agent passed card number to logging tool
    → Fix: Add PII filter before tool call logging

3 passed · 1 failed · 0 skipped
Total: 12.4s | Cost: $0.08
```

## Why AgentBench?

You've built an AI agent. It works… *mostly*. But then you tweak the prompt and:

- 😱 It loops infinitely instead of completing
- 🔓 It passes credit card numbers to a logging tool
- 🔄 It calls the wrong API entirely
- 💥 It crashes on edge cases you didn't think of

**AgentBench catches these before your users do.**

---

## ⚡ Quick Start — 3 Commands

```bash
pip install agentbench
agentbench init my-agent-tests
agentbench run
```

That's it. Edit the generated `test_agent.py` with your agent details and you're testing.

---

## ✨ Features

| | | |
|:---|:---|:---|
| 🎯 **Behavioral Assertions** <br>Test what the agent *does*, not just what it says | 🔌 **Raw API + LangChain Ready** <br>Best-tested adapter path today, with additional adapters still maturing | 📼 **Trajectory Diffing** <br>Record golden runs, catch regressions |
| 🧑‍⚖️ **LLM-as-Judge** <br>Use LLMs to evaluate subjective quality | 💉 **Failure Injection** <br>Simulate broken APIs, timeouts, rate limits | ⚡ **Parallel Execution** <br>Run suites fast with built-in concurrency |
| 🔄 **CI/CD Integration** <br>JSON reports, exit codes, GitHub Action, GitLab CI | ☁️ **Cloud API** <br>FastAPI server for scans, reports, and release gates | 🧪 **Experimental Surfaces** <br>Adversarial, property-based, and multi-agent tooling are still maturing |

---

## Write Tests Like This

```python
from agentbench import AgentTest, expect
from agentbench.adapters import LangChainAdapter

adapter = LangChainAdapter(agent=my_checkout_agent)

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
        result = self.run("Book a flight to Tokyo",
                          inject_tool_failure="search_api", fail_times=2)
        expect(result).to_retry(max_attempts=3)
```

---

## Assertions at a Glance

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

---

## 🔌 Framework Support

| Framework | Adapter | Status |
|-----------|---------|--------|
| HTTP API | `RawAPIAdapter` | ✅ Recommended |
| Python function | `RawAPIAdapter(func=...)` | ✅ Recommended |
| LangChain | `LangChainAdapter` | ✅ Recommended |
| OpenAI Assistants | `OpenAIAdapter` | 🧪 Experimental |
| CrewAI | `CrewAIAdapter` | 🧪 Experimental |
| AutoGen | `AutoGenAdapter` | 🧪 Experimental |
| LangGraph | `LangGraphAdapter` | 🧪 Experimental |

The launch-ready adapter path today is **Raw API + LangChain**. The additional adapters are available in the repo, but should be treated as experimental until they have fuller contract coverage.

---

## 📊 How We Compare

| | **AgentBench** | **Promptfoo** | **pytest + mocks** | **Manual QA** |
|---|:---:|:---:|:---:|:---:|
| Behavioral assertions | ✅ | ❌ | 🔶 Manual | ❌ |
| Agent trajectory testing | ✅ | ❌ | ❌ | 🔶 Ad-hoc |
| Multi-framework adapters | ✅ 6 frameworks | ❌ | ❌ | ❌ |
| Failure injection | ✅ Built-in | ❌ | 🔶 Manual | ❌ |
| LLM-as-Judge | ✅ | ✅ | ❌ | ❌ |
| Trajectory diffing | ✅ | ❌ | ❌ | ❌ |
| CI/CD native | ✅ | ✅ | ✅ | ❌ |
| Cost tracking | ✅ | ❌ | ❌ | ❌ |
| Setup time | **~2 min** | 5 min | 30+ min | Ongoing |

---

## CLI Reference

```bash
agentbench run ./tests          # Run all tests
agentbench run ./tests -v       # Verbose assertion output
agentbench run ./tests -f "checkout"  # Filter by name pattern
agentbench record ./tests "Book a flight" -o golden.json  # Record golden trajectory
agentbench diff golden.json     # Diff current run against golden
agentbench run ./tests -r report.json  # JSON report for CI
```

---

## 📚 Documentation

| Guide | Description |
|-------|-------------|
| [Getting Started](docs/getting-started.md) | 5-minute quickstart |
| [API Reference](docs/api-reference.md) | Complete public API docs |
| [Adapters](docs/adapters.md) | Framework-specific guides |
| [Examples](docs/examples.md) | 5+ real-world test suites |
| [Architecture](docs/architecture.md) | How the engine works |
| [Contributing](docs/contributing.md) | Dev setup & PR process |

---

## ☁️ Cloud API

AgentBench includes an optional cloud API server:

```bash
pip install agentbench[server]
agentbench serve --port 8000
```

See `agentbench/server/` for the FastAPI scaffold with authenticated scan execution, report history, and release-gate workflows. Shared report links are intended for teammates who already have AgentBench access.

---

## 🗺️ Roadmap

- [x] Core test engine + assertion API
- [x] Raw API + LangChain adapters
- [x] Trajectory recording & diffing
- [x] CLI (run, record, diff, init)
- [x] Failure injection
- [x] OpenAI Assistants adapter
- [x] Parametric tests
- [x] Parallel test execution
- [x] Watch mode (file watcher)
- [x] HTML report generation
- [x] CrewAI / AutoGen / LangGraph adapters
- [x] LLM-as-Judge with confidence scoring & caching
- [x] GitHub Action + GitLab CI templates
- [x] Cloud API scaffold (FastAPI + JWT auth)
- [ ] Adversarial test generation
- [ ] Property-based testing
- [ ] Multi-agent test harness
- [ ] Web dashboard

---

## 🤝 Contributing

AgentBench is open source — we welcome contributions!

```bash
git clone https://github.com/EdList/agentbench.git
cd agentbench
pip install -e ".[dev]"
pytest
```

See [CONTRIBUTING.md](docs/contributing.md) for detailed guidelines.

## License

MIT
