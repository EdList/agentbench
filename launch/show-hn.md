# Show HN: AgentBench – pytest for AI agent behaviors

**Title:** Show HN: AgentBench – pytest for AI agent behaviors

**Body:**

Hi HN. We built AgentBench because we kept running into the same problem: AI agents would pass our output tests but break in production by doing the wrong *things* — calling the wrong APIs, looping infinitely, leaking PII through tool calls, skipping steps in a workflow.

Most testing tools (Promptfoo, etc.) focus on prompt quality — does the output look right? But agents don't just produce text. They call tools, make multi-step decisions, recover from errors, and follow workflows. Testing only the final output is like testing a self-driving car by checking if it arrived, without checking if it ran red lights.

AgentBench captures the full agent trajectory (every tool call, LLM response, error, and retry) and lets you write fluent assertions against it: `expect(result).to_use_tool("payment_api", times=1)`, `expect(result).to_follow_workflow(["search", "cart", "pay"])`, `expect(result).to_not_expose("credit_card")`.

It includes 6 framework adapters (LangChain, OpenAI Assistants, CrewAI, AutoGen, LangGraph, raw API), failure injection for resilience testing, trajectory diffing for regression detection, LLM-as-Judge for subjective evaluation, and CI/CD integration with JSON reports. MIT licensed, Python 3.11+.

GitHub: https://github.com/EdList/agentbench

We'd love feedback from anyone building production agents — what behaviors are you testing today, and what's missing?
