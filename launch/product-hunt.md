# Product Hunt Launch — AgentBench

---

## Tagline (60 chars max)

**pytest for AI agent behaviors — test what they DO, not just what they say**

*Character count: 61 — slightly over. Alternatives:*

- **pytest for AI agents — test what they DO, not what they say** (55 chars)
- **Behavioral testing for AI agents — catch bugs before users do** (58 chars)
- **pytest meets AI agents — test tool calls, workflows & safety** (58 chars)

## Description (260 chars max)

AgentBench is an open-source framework that lets developers write behavioral tests for AI agents. Test tool calls, workflow adherence, PII safety, failure recovery, and more. Works with LangChain, OpenAI, CrewAI, AutoGen, LangGraph. MIT licensed. ~2 min setup.

*Character count: 258*

---

## Full Description

### 🧪 AgentBench — Behavioral Testing for AI Agents

Your AI agent passed unit tests. Then it called the wrong API in production. We built AgentBench to fix this.

Most AI testing tools check *outputs*. But agents don't just produce text — they **call tools, make decisions, follow workflows, and recover from errors**. Testing only what they say is like testing a self-driving car by checking if it arrived, without checking if it ran red lights.

AgentBench captures the **full trajectory** of every agent run and lets you write declarative assertions about what your agent *actually did*.

### ✨ Key Features

- 🎯 **Behavioral Assertions** — Test tool calls, workflow order, step counts, PII exposure, retry behavior, and more
- 🔌 **6 Framework Adapters** — LangChain, OpenAI Assistants, CrewAI, AutoGen, LangGraph, and raw HTTP/Python APIs
- 💉 **Failure Injection** — Simulate broken APIs, timeouts, and rate limits to test resilience
- 📼 **Trajectory Diffing** — Record golden runs and catch regressions automatically
- 🧑‍⚖️ **LLM-as-Judge** — Use LLMs to evaluate subjective quality with confidence scoring
- ⚡ **Parallel Execution** — Run suites fast with built-in concurrency
- 🔄 **CI/CD Native** — JSON reports, exit codes, GitHub Actions + GitLab CI templates
- 📊 **Cost Tracking** — Know exactly how much each test run costs
- 🐳 **Docker Sandbox** — Optional isolated execution with resource limits
- ☁️ **Cloud API** — Optional FastAPI server with JWT auth and trajectory storage

### 🚀 Quick Start

```bash
pip install agentbench
agentbench init my-agent-tests --framework langchain
agentbench run
```

### 📝 Write Tests Like This

```python
from agentbench import AgentTest, expect

class CheckoutAgentTest(AgentTest):
    agent = "checkout-agent"
    adapter = my_adapter

    def test_completes_checkout(self):
        result = self.run("Buy me a blue shirt, size M")
        expect(result).to_complete_within(steps=10)
        expect(result).to_use_tool("payment_api", times=1)
        expect(result).to_not_expose("credit_card_number")
```

Open source, MIT licensed. Perfect for teams building production AI agents.

---

## Gallery Descriptions for Screenshots

### Screenshot 1: CLI Output — Test Run
**Description:** AgentBench CLI running a test suite against an e-commerce checkout agent. Shows passed and failed tests with detailed assertion output including step-level diagnostics and suggested fixes.

### Screenshot 2: Verbose Assertion Output
**Description:** Verbose mode showing every assertion result — tool call counts, workflow verification, PII safety checks, and step completion — for a fully passing test suite.

### Screenshot 3: Test Failure with Diagnostics
**Description:** A failing test showing AgentBench's actionable error messages: what went wrong, what was expected, what actually happened, and a suggested fix. No more guessing why a test failed.

### Screenshot 4: Code Example
**Description:** Writing behavioral tests with AgentBench's fluent API. The `expect()` chain lets you assert on tool usage, workflow order, PII exposure, and more — in a single readable test method.

### Screenshot 5: CI/CD Integration
**Description:** AgentBench running in a GitHub Actions workflow with JSON report generation, exit code handling, and cost tracking — drop-in CI/CD integration for agent testing.

---

## First Comment (Maker Comment)

Hey Product Hunt! 👋 Ed here, one of the makers of AgentBench.

**The story:** We built AgentBench after a painful production incident. Our AI checkout agent had been "tested" — outputs looked great in QA, the prompt was tuned, the demos were smooth. Then in production, it started calling the payment API twice for some orders. Not because of a bug in our code, but because the LLM decided to retry a step it didn't need to retry. No output test caught it because the *output* looked perfectly normal — "Your order is confirmed!"

That's when we realized the gap: we were testing what our agent *said*, not what it *did*.

**The technical approach:** AgentBench intercepts the full trajectory of an agent run — every tool call, every LLM response, every error, every retry — and wraps it in a fluent assertion API inspired by pytest and Chai.js. You write `expect(result).to_follow_workflow(["search", "cart", "pay"])` and it verifies the agent called those tools in that order.

The framework-agnostic adapter pattern means it works with LangChain, OpenAI Assistants, CrewAI, AutoGen, LangGraph, or a raw Python function. We didn't want to lock anyone in.

Failure injection was a must-have from day one — you can simulate broken APIs and test how your agent handles adversity without touching real infrastructure.

**What we'd love feedback on:**
- What agent behaviors are *you* testing today? What's hard to test?
- Are there assertions you need that we haven't built yet?
- How do you currently test agent resilience and safety?

The repo is at https://github.com/EdList/agentbench — MIT licensed, PRs welcome. We're a small team and genuinely want to build something the community finds useful.

Thanks for checking it out! 🙏
