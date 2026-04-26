# AgentBench Production-Readiness Roadmap

> **For Hermes:** Use subagent-driven-development skill to execute this plan task-by-task.

**Goal:** Turn AgentBench from a promising teams-first prototype into a private-beta-ready product, then a public launch candidate, then a durable commercial SaaS.

**Architecture:** Keep the current FastAPI + web app shape for the short term, but shift the product from a generic “agent scanner” into a team workflow product: release gating, regression detection, CI integration, and explainable evidence. Harden the backend first, then add team/workspace, eval authoring, and deployment plumbing before public launch.

**Tech Stack:** FastAPI, static HTML/JS UI, Python scanner pipeline, SQLite today; Postgres + background workers + managed auth/infra for public release.

---

## Product verdict

### What problem AgentBench is solving
AgentBench is solving **release confidence for AI agents**:
- Can we trust this agent enough to ship?
- Did the latest change make it worse?
- Can the team agree on a pass/fail bar?
- Can we block bad releases before users see failures?

### Is the problem real?
**Yes.** The category is validated by products like LangSmith and Braintrust, and by vendor docs from Anthropic/OpenAI emphasizing evals, guardrails, and behavior testing.

### Is it painful enough to pay for?
**Yes, for the right buyer:** teams shipping customer-facing or tool-using agents. Not for hobby users.

### Current product truth
AgentBench is currently:
- a strong prototype
- a credible wedge
- not yet a public-ready paid product

The main gap is that it still behaves more like a **scanner/report** than a **release gate/workflow system**.

---

## Positioning to lock in before building further

### Primary product definition
**AgentBench is the release gate and regression system for AI agents.**

### Primary buyer
- AI engineering lead
- platform / infra lead
- applied AI team lead
- product owner for customer-facing AI workflows

### Best initial ICP
Start with one of these and do not broaden too early:
1. **AI support agents**
2. **Tool-using business agents**
3. **Enterprise internal copilots with policy/safety risk**

### Anti-ICP for now
Do **not** optimize first for:
- hobby builders
- generic chatbots
- research demos
- “benchmark for fun” users

---

## Release stages

### Stage 1 — Private beta ready
**Definition:** A small number of design partners can use AgentBench weekly without hand-holding.

### Stage 2 — Public launch ready
**Definition:** Strangers can sign up, onboard, run evals, understand results, and trust the product operationally.

### Stage 3 — Commercially durable
**Definition:** AgentBench is sticky in customer workflow, measurable in ROI, and defendable beyond a demo.

---

# Stage 1: Private beta readiness

## Exit criteria
Before inviting outside teams, AgentBench must have all of the following:
- saved agents / projects
- saved eval suites or scan policies
- background job execution
- pass / warn / fail release policy
- reliable regression comparisons
- CI/webhook integration for at least one workflow
- team/workspace model
- better report explainability
- production hosting/monitoring basics

---

### Task 1: Lock the product contract

**Objective:** Stop feature drift by defining exactly what AgentBench is and is not.

**Deliverables:**
- one-sentence positioning statement
- ICP doc
- non-goals doc
- beta success criteria

**Must answer:**
- What kind of agent is in scope?
- What job does AgentBench do better than a hand-built eval script?
- What event triggers usage: pre-release, PR, nightly, post-incident?
- What exact artifact does the buyer need: score, gate, diff, report, alert?

**Output:** `docs/plans/positioning-and-icp.md`

**Release gate:** No more broad product work until this is written.

---

### Task 2: Introduce first-class projects and workspaces

**Objective:** Replace principal-scoped scans with real team-owned entities.

**Current gap:** Auth exists, but there is no product-level model for organization, workspace, project, or membership.

**Build:**
- workspaces
- users
- memberships / roles
- projects
- project-scoped agents
- API tokens per workspace/project

**Data model needed:**
- `workspaces`
- `users`
- `workspace_memberships`
- `projects`
- `agents`
- `api_tokens`

**Likely files:**
- `agentbench/server/models.py`
- new migration system
- `agentbench/server/auth.py`
- `agentbench/server/routes/*`
- new workspace/project settings UI

**Release gate:** Every scan belongs to a workspace + project, not just a raw principal string.

---

### Task 3: Move scans onto a durable job model

**Objective:** Make scan execution reliable and asynchronous.

**Current gap:** Scan execution is request-bound and prototype-shaped.

**Build:**
- `scan_jobs` table
- status lifecycle: `queued`, `running`, `passed`, `warned`, `failed`, `errored`, `cancelled`
- worker process
- retry policy
- idempotency key support
- cancellation support
- duration + error capture

**Infra direction:**
- short term: background worker + Postgres-backed queue
- optional later: Redis/Celery/RQ/Arq

**Release gate:** Large or slow scans cannot block request threads.

---

### Task 4: Add first-class eval policies

**Objective:** Replace “generic scan” with a reusable release policy.

**Build:**
- saved eval suites / policies
- categories enabled/disabled
- threshold configuration
- severity mapping
- pass/warn/fail logic
- baseline selection rules

**Example policy model:**
- minimum overall score
- minimum per-domain score
- fail on critical issue presence
- fail on regression delta worse than X
- require all mandatory checks to pass

**Release gate:** A team can answer “what causes a release to fail?” in one screen.

---

### Task 5: Explain failures, not just scores

**Objective:** Make reports trustworthy enough for teams to act on.

**Build:**
- drill down from score → domain → scenario → prompt/input → response → finding → recommendation
- evidence snippets
- clearer failure reason taxonomy
- rationale for pass/fail decision
- compare current vs baseline with explicit deltas

**Release gate:** A skeptical engineer can understand *why* a scan failed without reading source code.

---

### Task 6: Ship one real workflow integration

**Objective:** Put AgentBench in the path of a real release workflow.

**Pick one first:**
1. GitHub PR check/status + comment
2. CI action
3. webhook for deployment pipelines

**Minimum feature set:**
- trigger a scan from CI
- wait/poll for result
- compare to baseline
- post pass/fail summary
- expose link to full report

**Release gate:** At least one design partner can use AgentBench in CI before merging or deploying.

---

### Task 7: Add beta-grade ops and supportability

**Objective:** Make the system operable by you, not just usable by customers.

**Build:**
- structured logs
- error tracking
- health endpoints for worker + API
- metrics: scan count, duration, failure rate, queue depth
- admin tooling for re-run/cancel/debug
- support playbook

**Release gate:** You can debug a failed customer scan without SSH archaeology.

---

### Task 8: Private beta packaging

**Objective:** Make it understandable to early users.

**Build:**
- landing page copy aligned to release gating
- onboarding doc
- quickstart for CI
- sample eval policy templates
- changelog / release notes

**Release gate:** A design partner can get value in one session.

---

# Stage 2: Public launch readiness

## Exit criteria
Before open public release, AgentBench must have:
- robust multi-tenant architecture
- proper database + migrations
- rate limits / abuse controls
- secure auth model
- onboarding flow
- billing or at least usage metering
- docs/support/legal basics
- production deployment reliability

---

### Task 9: Migrate core persistence to Postgres

**Objective:** Remove SQLite as the core production system.

**Why:** SQLite is fine for prototype/local dev, but public SaaS needs stronger concurrency, backup strategy, and operational tooling.

**Build:**
- Postgres connection management
- migrations
- data migration path from existing SQLite scan store
- retention policies
- backup/restore procedure

**Release gate:** Production data is no longer dependent on a local SQLite file.

---

### Task 10: Harden security and abuse prevention

**Objective:** Make scanning arbitrary endpoints safe enough for public exposure.

**Build:**
- rate limiting
- quotas per workspace/token
- request body size limits
- response size limits
- max scan runtime
- egress restrictions where possible
- stronger SSRF protections at infra and app layers
- signed links or protected share access
- audit log for sensitive actions

**Release gate:** Public abuse does not trivially turn the service into a free network probe or denial-of-service target.

---

### Task 11: Replace dev-style auth with real product auth

**Objective:** Make user and machine access production-grade.

**Build:**
- email/social/OIDC auth or managed auth provider
- workspace invitations
- session management
- API tokens with rotation/revocation
- RBAC
- later: SSO/SAML for enterprise

**Release gate:** Access is manageable by workspace admins, not by manual config.

---

### Task 12: Build real onboarding

**Objective:** Make self-serve adoption possible.

**Build:**
- create workspace
- create project
- register agent
- choose policy template
- run first scan
- connect CI
- interpret first report

**Release gate:** A new user can reach first value without founder intervention.

---

### Task 13: Add billing or at least metering

**Objective:** Support commercialization and fair usage.

**Build:**
- usage accounting: scans, scenarios, runtime, storage, seats
- plan tiers
- quota enforcement
- billing hooks or Stripe integration

**Release gate:** You can charge, limit, or grandfather customers intentionally.

---

### Task 14: Add public-launch trust surface

**Objective:** Reduce buyer friction.

**Build:**
- docs site
- pricing page
- privacy policy
- terms of service
- data retention policy
- security page
- support/contact path
- uptime/status page

**Release gate:** A stranger can assess whether the product is credible enough to adopt.

---

# Stage 3: Commercial durability

## Exit criteria
After launch, AgentBench should become harder to replace because it is embedded in team workflow.

---

### Task 15: Ingest production traces

**Objective:** Turn real failures into future eval coverage.

**Why this matters:** This is likely the deepest moat. Teams do not just want synthetic scans; they want production-informed regression prevention.

**Build:**
- trace ingestion API / SDK
- map traces to projects/agents
- turn traces into candidate eval cases
- promote incidents into regression suites

---

### Task 16: Human review and annotation loop

**Objective:** Handle ambiguous failures with human judgment.

**Build:**
- annotation queues
- reviewer assignment
- false-positive marking
- override workflow
- reviewer comments

---

### Task 17: Verticalize the product

**Objective:** Make the product feel purpose-built for the ICP.

**Possible packs:**
- support-agent readiness pack
- tool-using business-agent pack
- enterprise copilot safety pack

---

### Task 18: Expand ecosystem integrations

**Objective:** Make AgentBench fit existing engineering motion.

**Targets:**
- GitHub
- GitLab
- Slack
- Linear/Jira
- deployment systems
- model/observability stacks

---

## Priority order

### Build now
1. product contract / ICP lock
2. workspaces + projects
3. async scan jobs
4. eval policies and release gates
5. explainable reports
6. one real CI integration

### Build before public launch
7. Postgres + migrations
8. abuse/security hardening
9. real auth + RBAC
10. onboarding
11. metering/billing
12. docs/legal/support surface

### Build after first customer pull
13. production trace ingestion
14. annotation loop
15. vertical packs
16. broader integrations

---

## What to explicitly avoid right now

Do **not** spend the next cycle on:
- fancy cosmetics
- broad multi-agent orchestration features
- agent-facing APIs before team workflow is strong
- many integrations at once
- custom enterprise features before the core release-gate workflow is sticky

---

## Recommended next build sequence from the current codebase

### Next 2 weeks
- lock positioning and ICP
- implement project/workspace model
- design scan job state machine
- design eval policy schema

### Next 2–4 weeks
- ship async scan execution
- ship saved policies
- ship pass/warn/fail gating
- improve report drill-down and failure evidence

### Next 4–6 weeks
- ship GitHub Action / PR integration
- onboard 2–5 design partners
- collect real workflow pain

### Before public release
- migrate to Postgres
- add rate limits / quotas
- add real auth/onboarding
- add public docs + pricing + support surface

---

## Beta success metrics

A private beta is working if at least 3 teams do all of the following:
- run scans repeatedly over multiple weeks
- connect AgentBench to a real release or PR workflow
- use regression results to change shipping decisions
- share reports internally without founder assistance
- ask for deeper workflow support, not basic explanation

---

## Public launch readiness checklist

A public launch is justified only if:
- onboarding works self-serve
- scans are async and reliable
- workspace/project boundaries are real
- release gate semantics are clear
- one workflow integration is production-usable
- Postgres + migrations are live
- rate limits and quotas are enforced
- auth is real
- docs/pricing/legal/support are present
- early users return because the product changes decisions, not because the demo is cool

---

## Bottom line

AgentBench should be built as a **team release gate for AI agents**, not a generic scanner.

If the next work is focused on:
- release gating
- regression confidence
- team workflows
- explainability
- CI adoption

then the product is pointed at a real, painful, paid problem.

If not, it risks remaining a polished demo.
