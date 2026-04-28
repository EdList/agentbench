# AgentBench — Product Specification

**Status:** Draft v1 — building toward MVP

---

## What We're Building

**AgentBench is behavioral CI for AI agents.**

You paste your agent endpoint. We probe it. We tell you what behaviors are broken, what regressed since last deploy, and whether it's safe to ship. We drop into your GitHub PR as a required check.

The product is a **platform**, not a framework. The framework is the engine underneath.

---

## The Problem

Teams shipping AI agents live in constant fear:
- "We tweaked the system prompt. Did we break the checkout flow?"
- "The agent passed a credit card number to our logging tool. We found out from a customer."
- "It loops forever when the search API returns empty results. We only catch this in prod."

Manual testing doesn't scale. Prompt evals test outputs, not behaviors. Nobody tests agent *trajectories* — the sequence of tool calls, decisions, and side effects.

This is a regression-testing problem for a new kind of software. Agents are non-deterministic, multi-step, and stateful. They need a new kind of test.

---

## The Product

Three surfaces, one engine:

### 1. Self-Service Scan (the on-ramp)
```
$ agentbench scan https://my-agent.example.com/api/chat
```
→ 60 seconds later → scorecard with shareable URL
→ 4 behavioral domains scored: Completion, Tool Usage, Safety, Consistency
→ Each domain: 0-100 score, pass/fail indicators, specific findings

No Python required. No framework knowledge. Paste URL, get results.

### 2. CI/CD Gate (the revenue driver)
```yaml
# .github/workflows/agent-check.yml
- uses: agentbench/gate@v1
  with:
    agent-url: ${{ secrets.AGENT_URL }}
    min-score: 80
```
Blocks PRs when agent behavior regresses. Shows a diff: "Previously completed in 4 steps, now takes 12. Tool `search_api` used 3x instead of 1x."

### 3. Team Dashboard (the paid product)
- All your agents, health scores over time
- Golden trajectory library: record a baseline, catch drift
- Regression timeline: when did the checkout flow break? Which commit?
- Team sharing: "hey PM, here's the agent's safety score for this release"

---

## What Makes Us Different

| | Promptfoo | Patronus | Manual QA | **AgentBench** |
|---|---|---|---|---|
| Tests prompt outputs | ✅ | ✅ | 🔶 | ❌ (not our thing) |
| Tests agent behaviors | ❌ | ❌ | 🔶 | ✅ |
| Trajectory-level assertions | ❌ | ❌ | ❌ | ✅ |
| Failure injection | ❌ | ❌ | ❌ | ✅ |
| Golden trajectory diffing | ❌ | ❌ | ❌ | ✅ |
| CI/CD native | ✅ | ✅ | ❌ | ✅ |
| Zero-code onboarding | ✅ | ❌ | ❌ | ✅ |

We don't compete on prompt evaluation. We compete on **agent behavioral regression testing** — a category that doesn't have a dominant player yet.

---

## Architecture

```
User → agentbench scan <url>
  → Prober (hits agent with test prompts, records trajectories)
  → Analyzer (classifies behaviors: completion, tool usage, PII exposure)
  → Scorer (0-100 score per domain, weighted overall)
  → Dashboard (persists, renders, shares)

CI integration:
  GitHub Action → AgentBench API → gate endpoint → pass/fail + report link
```

The engine is the existing `agentbench/core/` + `agentbench/scanner/`. The product is a thin, opinionated surface on top: CLI, API, dashboard.

---

## MVP — What We Ship First

**Goal:** One command, working end-to-end, on a single machine, from paste to scorecard.

Scope:
- `agentbench scan <agent-url>` — works for any HTTP agent endpoint
- Probes 4 behavioral categories (completion, tool usage, safety, consistency)
- Produces a terminal scorecard + writeable JSON report
- Ships on PyPI as `agentbench`
- Works on a fresh machine with zero configuration

Not in MVP:
- Web dashboard (that's post-MVP)
- GitHub Action (requires cloud API)
- Team sharing / multi-user
- Historical regression tracking
- LangChain/CrewAI/etc. adapters (RawAPI covers everything that speaks HTTP)

### MVP Success Criteria
1. A random engineer can `pip install agentbench`, paste their agent URL, and get results in under 2 minutes
2. The scorecard is meaningful — not just numbers, but specific findings ("Found: agent passed credit card number to tool `log_event` on step 3")
3. Works against real agent endpoints, not just our test fixtures

---

## Post-MVP Roadmap

1. **Web dashboard** — store results, show trends, share links
2. **CI gate** — GitHub Action that calls our API
3. **Golden trajectories** — record baseline, diff against current
4. **Team plan** — $29/mo, shared dashboard, unlimited agents
5. **Enterprise** — on-prem, SSO, audit logs, custom policies

---

## Decisions Made (and why)

| Decision | Rationale |
|---|---|
| RawAPI adapter only in MVP | Every agent framework exposes HTTP. We test at the protocol, not the framework. |
| No LangChain/etc. in MVP surface | They fragment onboarding. "Which adapter?" is a question the product shouldn't ask. |
| CLI-first, not web-first | Developer tools win through CLI. The web dashboard is post-MVP. |
| Scanner engine stays | The prober/analyzer/scorer pipeline is working, tested code. Don't rebuild it — productize it. |
| Scorecard over raw test framework | Users don't want to write assertions. They want to know if their agent is broken. The framework is power-user mode. |

---

## Open Questions

1. **Sandbox execution?** Does `agentbench scan` spin up a local sandbox to probe the agent, or hit it directly from the user's machine?
2. **Pricing model?** Freemium (1 agent free, scan only) or free-forever open source + paid cloud?
3. **Name?** Is "AgentBench" the product name or the framework name?

---

*Last updated: 2026-04-28*