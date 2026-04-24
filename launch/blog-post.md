# Why AI Agents Need Behavioral Testing

**Your AI agent passed unit tests. Then it called the wrong API in production.**

Sound familiar? You built an agent. You wrote tests. You checked that the outputs looked reasonable. You deployed. And then… it did something *unexpected*. Not wrong in the "returned bad JSON" sense — wrong in the *called the payment API twice* or *leaked a credit card number to the logging tool* sense.

That's the gap. And it's costing teams real money and real trust.

---

## The Problem: You're Testing the Wrong Thing

Most AI agent testing today falls into one of three buckets:

**1. Output checking.** You send a prompt, read the response, and check if it "looks right." Maybe you assert a substring exists. Maybe you have a human review it. Either way, you're only testing what the agent *says* — not what it *does*.

**2. Prompt testing.** Tools like Promptfoo let you evaluate how different prompts produce different outputs. Useful! But agents don't just produce text — they call tools, make decisions, follow multi-step workflows, and recover from errors. Testing prompts doesn't tell you if your agent called `delete_database()` when it should have called `delete_record()`.

**3. Observability dashboards.** Langfuse and similar tools show you *what happened* after the fact. Great for debugging in production. Not great for *preventing* problems before they ship. Observability is a safety net, not a test suite.

Here's the uncomfortable truth: **agents ACT.** They call APIs, execute code, modify databases, send emails. Testing only their final output is like testing a self-driving car by checking if it arrived — without checking if it ran red lights along the way.

---

## The Solution: Behavioral Testing

What if you could write tests that verify *every step* your agent takes?

Not "did it produce reasonable text?" but:

- **Did it call the right tools in the right order?**
- **Did it stay within its step budget (no infinite loops)?**
- **Did it recover gracefully when an API failed?**
- **Did it leak sensitive data through any step?**
- **Did it follow the expected workflow?**

That's behavioral testing. And it's what **[AgentBench](https://github.com/EdList/agentbench)** does.

AgentBench is `pytest` for AI agent behaviors. It lets you write declarative, fluent assertions about *what your agent does* — not just what it says. It captures the full trajectory of an agent run (every tool call, every LLM response, every error, every retry) and lets you assert against it.

Under the hood, AgentBench uses an adapter pattern. You wrap your agent — whether it's a LangChain chain, an OpenAI Assistant, a CrewAI crew, an AutoGen agent, a LangGraph graph, or a raw Python function — in a framework-specific adapter. The adapter normalizes the agent's execution into a structured `AgentTrajectory` object: a list of steps, each with an action type (`tool_call`, `llm_response`, `error`, `retry`), the tool name and inputs/outputs, the response text, and any error details. Your assertions operate on this trajectory, not on raw API responses.

---

## How It Works: Three Real Examples

### Example 1: Testing a Checkout Agent's Workflow

Let's say you have an e-commerce agent that handles purchases. You want to make sure it follows the correct workflow: search → add to cart → payment.

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

    def test_follows_correct_workflow(self):
        result = self.run("Order a shirt for me")
        expect(result).to_follow_workflow([
            "product_search", "add_to_cart", "payment_api"
        ])
```

Run it:

```bash
$ agentbench run -v

  ✓ test_completes_checkout (2.3s)
    ✓ Agent completed in 4 steps (limit: 10)
    ✓ Agent called 'payment_api' 1 time(s) (expected: 1)
    ✓ Agent did not expose 'credit_card_number'
  ✓ test_follows_correct_workflow (1.8s)
    ✓ Agent followed workflow: product_search → add_to_cart → payment_api
```

If someone changes the prompt and the agent skips the cart step, the test catches it immediately.

### Example 2: Testing Failure Recovery

Agents don't live in a world of perfect APIs. What happens when the search API goes down? Does your agent retry? Does it give up gracefully? Does it spam the API 50 times?

```python
def test_retries_on_search_failure(self):
    result = self.run(
        "Search for flights to Tokyo",
        inject_tool_failure="search_api",
        fail_times=2,
    )
    expect(result).to_retry(max_attempts=3)
    expect(result).to_complete()
```

AgentBench's **failure injection** lets you simulate broken APIs, timeouts, and rate limits — without touching your real infrastructure. You can test how your agent handles adversity *before* your users experience it.

### Example 3: Testing PII Safety

One of the most critical (and most overlooked) aspects of agent behavior: does your agent leak sensitive data? Not just in the final response — in *any* step, including tool call inputs, logging, and intermediate reasoning.

```python
def test_no_credit_card_leak(self):
    result = self.run(
        "Buy me a shirt, my card is 4111111111111111"
    )
    expect(result).to_not_expose("4111111111111111")
```

This checks *every step* in the agent's trajectory — tool call inputs, tool call outputs, LLM responses — for the sensitive pattern. If the agent passes the card number to a logging tool or includes it in a search query, the test fails with a precise diagnostic:

```
  ✗ test_no_credit_card_leak
    → Agent exposed sensitive pattern '4111111111111111' in steps [2, 3].
      What went wrong: The pattern was found in agent output data.
      Expected: Pattern '4111111111111111' should never appear in any step.
      What happened: Found in steps [2, 3].
      Suggested fix: Add PII redaction or filtering to prevent exposing '4111111111111111'.
```

Notice how every assertion failure includes: what went wrong, what was expected, what actually happened, and a suggested fix. We spent a lot of time making failures actionable — because a test that tells you "assertion failed" without telling you *why* isn't useful.

---

## The Assertions API

AgentBench provides a fluent, chainable assertion API. Here are the core assertions:

| Assertion | What it checks |
|-----------|---------------|
| `to_complete()` | Agent finished without error |
| `to_complete_within(steps=N)` | Agent completed in ≤ N steps (catches infinite loops) |
| `to_use_tool(name, times=N)` | Agent called a specific tool (optionally exact count) |
| `to_not_use_tool(name)` | Agent never called a tool (e.g., don't call payment for a return) |
| `to_not_expose(pattern)` | Agent never exposed sensitive data in any step |
| `to_respond_with(text)` | Final response contains text |
| `to_retry(max_attempts=N)` | Agent retried within limits after failure |
| `to_follow_workflow([steps])` | Agent called tools in the specified order |
| `to_have_no_errors()` | No step had an error |

All assertions chain fluently and can be combined freely. You can also inspect individual steps:

```python
result = self.run("Book a flight")
expect(result).step(0).used_tool("search_api")
expect(result).step(0).responded_with("found")
```

---

## How AgentBench Compares

| | **AgentBench** | **Promptfoo** | **Langfuse** | **Manual QA** |
|---|:---:|:---:|:---:|:---:|
| Behavioral assertions | ✅ | ❌ | ❌ | 🔶 Ad-hoc |
| Tool call testing | ✅ | ❌ | Observability only | ❌ |
| Workflow verification | ✅ | ❌ | ❌ | 🔶 |
| Multi-framework adapters | ✅ 6 frameworks | ❌ | ❌ | ❌ |
| Failure injection | ✅ Built-in | ❌ | ❌ | ❌ |
| Trajectory diffing | ✅ | ❌ | ❌ | ❌ |
| LLM-as-Judge | ✅ | ✅ | ✅ | ❌ |
| CI/CD native | ✅ | ✅ | ❌ | ❌ |
| Cost tracking per test | ✅ | ❌ | Dashboard only | ❌ |
| Setup time | ~2 min | 5 min | 10+ min | Ongoing |

**Promptfoo** is excellent for prompt engineering — A/B testing prompts, evaluating output quality across models. But it tests *prompts*, not *agent behaviors*. It doesn't know about tool calls, workflows, or trajectories.

**Langfuse** gives you beautiful dashboards showing what your agents did in production. It's observability, not testing. You see problems after they happen.

**Manual QA** is… well, manual. It doesn't scale, it's not reproducible, and it can't run in CI.

AgentBench fills the gap: **automated, reproducible, CI-friendly testing of agent behaviors.** It's the thing you run *before* you deploy, not the thing you check *after* something goes wrong.

---

## Feature Highlights

- **6 framework adapters** — LangChain, OpenAI Assistants, CrewAI, AutoGen, LangGraph, and raw HTTP/Python functions. Works with whatever you're using.
- **Trajectory diffing** — Record a "golden run" and diff future runs against it. Catch regressions the moment they appear.
- **LLM-as-Judge** — For subjective quality checks that can't be expressed as simple assertions. Uses GPT-4o-mini (or your preferred model) with confidence scoring and caching.
- **Parallel execution** — Run your test suite fast with built-in concurrency.
- **CI/CD integration** — JSON reports, exit codes, GitHub Actions, GitLab CI templates. Drop it into your pipeline in minutes.
- **Parametric tests** — Test your agent across multiple inputs without writing duplicate code.
- **Cost tracking** — Know exactly how much your test suite costs to run.
- **MIT licensed** — Open source, free forever.

---

## Get Started in 30 Seconds

```bash
pip install agentbench
agentbench init my-agent-tests --framework langchain
cd my-agent-tests
agentbench run
```

Edit `test_agent.py` with your agent details. Write behavioral assertions. Run them in CI. Ship with confidence.

** → [GitHub: EdList/agentbench](https://github.com/EdList/agentbench)**

If you're building AI agents, you need behavioral testing. Not because agents are unreliable — but because they're *complex*. They make multi-step decisions, call tools, handle errors, and interact with the real world. Testing only their output is testing only the tip of the iceberg.

AgentBench lets you test the whole thing. Give it a star, open an issue, or contribute. We'd love to hear what you think.

---

*AgentBench is MIT-licensed, Python 3.11+, and works with LangChain, OpenAI, CrewAI, AutoGen, LangGraph, and raw HTTP APIs.*
