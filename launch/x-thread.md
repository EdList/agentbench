# X/Twitter Thread — AgentBench Launch

---

**Tweet 1/9**
Your AI agent passed all tests. Then it called the wrong API in production. 💥

That's because you tested what it *said* — not what it *did*.

We built AgentBench: pytest for AI agent behaviors.

Open source. MIT licensed. Thread 🧵

**Tweet 2/9**
The problem with AI agent testing today:

❌ Output checking — only tests the final response
❌ Prompt testing — doesn't verify tool calls or workflows
❌ Observability — shows problems AFTER they ship

Agents ACT. They call APIs, make decisions, follow multi-step workflows.

You need to test their *behavior*, not just their output.

**Tweet 3/9**
AgentBench captures the full trajectory of every agent run — every tool call, LLM response, error, and retry — and lets you write fluent assertions against it.

Here's what testing looks like:

**Tweet 4/9**
```python
from agentbench import AgentTest, expect

class CheckoutTest(AgentTest):
    agent = "checkout-agent"
    adapter = my_adapter

    def test_completes_checkout(self):
        result = self.run("Buy me a blue shirt")
        expect(result).to_complete_within(steps=10)
        expect(result).to_use_tool("payment_api", times=1)
        expect(result).to_not_expose("credit_card")
```

Clean, readable, and it tests the whole trajectory — not just the output.

**Tweet 5/9**
What can you assert on? A lot:

🎯 Tool call counts and order
🔄 Workflow adherence (search → cart → pay)
🔒 PII safety across ALL steps
🔁 Retry behavior and error recovery
📊 Step count limits (no infinite loops!)
🧑‍⚖️ LLM-as-Judge for subjective quality

**Tweet 6/9**
Failure injection is a game changer.

Simulate broken APIs, timeouts, and rate limits — without touching your real infrastructure.

```python
result = self.run("Search flights",
    inject_tool_failure="search_api",
    fail_times=2)
expect(result).to_retry(max_attempts=3)
```

Test how your agent handles adversity BEFORE your users do.

**Tweet 7/9**
Works with YOUR stack:

✅ LangChain
✅ OpenAI Assistants
✅ CrewAI
✅ AutoGen
✅ LangGraph
✅ Raw HTTP / Python functions

6 adapters, one assertion API. Framework-agnostic by design.

**Tweet 8/9**
Plus:
📼 Trajectory diffing — catch regressions instantly
⚡ Parallel execution — fast test suites
🔄 CI/CD native — JSON reports, GitHub Actions, GitLab CI
📊 Cost tracking — know what your tests cost
☁️ Optional cloud API server

Setup: `pip install agentbench && agentbench init my-tests`

**Tweet 9/9**
AgentBench is open source, MIT licensed, and ready to use.

If you're building AI agents, you need behavioral testing. Not because agents are unreliable — but because they're complex.

⭐ Star us: https://github.com/EdList/agentbench

What behaviors are YOU testing? Drop your hardest-to-test agent problem below 👇
