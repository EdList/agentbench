# AgentBench Next 2 Weeks Execution Plan

> **For Hermes:** Use subagent-driven-development skill to execute this plan task-by-task.

**Goal:** In 2 weeks, turn AgentBench from a generic scanner into a usable **project-scoped release gate** that one design partner can run in a real pre-release or CI workflow.

**Architecture:** Do **not** try to finish the full production roadmap in 2 weeks. Instead, ship the highest-leverage vertical slice: saved projects, saved agents, saved scan policies, pass/warn/fail gating, and one CI-friendly execution path. Keep auth simple for now by using the existing authenticated principal as the owner boundary. Defer full workspaces, async workers, billing, and Postgres migration until after this slice proves workflow pull.

**Tech Stack:** FastAPI, SQLAlchemy, current auth layer, current scanner/store, static HTML/JS UI, pytest, GitHub Actions/webhook-compatible HTTP API.

---

## Why this is the right slice

### What we are optimizing for
At the end of 2 weeks, AgentBench should answer this real buyer question:

> “Can I save an agent, attach a release policy, run it before shipping, and get a clear pass/fail decision with a shareable report?”

### Why this slice beats other options right now
**Higher leverage than full workspace rebuild:** because it creates user-visible workflow value faster.

**Higher leverage than infra-only async jobs:** because teams pay for decisions, not queues.

**Higher leverage than more UI polish:** because a release gate changes behavior; polish does not.

### Explicit tradeoff
We are **not** making AgentBench fully public-launch-ready in this sprint.
We are making it **design-partner useful**.

---

## End-of-sprint definition of done

By the end of this 2-week sprint, AgentBench must support:

1. **Projects**
   - a user can create and list projects
   - all saved agents, policies, and scans belong to a project

2. **Saved agents**
   - a user can register a named agent endpoint under a project
   - scan history is tied to the saved agent, not just raw URL strings

3. **Saved scan policies**
   - enable/disable categories
   - minimum overall score
   - minimum domain scores
   - fail on critical issues
   - regression threshold

4. **Release verdicts**
   - every scan ends as `pass`, `warn`, or `fail`
   - verdict includes reasons

5. **Project-scoped report flow**
   - scan detail shows the policy used
   - share payload includes the release verdict
   - history and regression attach to saved agent/project

6. **CI-friendly execution path**
   - one endpoint or CLI flow can trigger a project+agent+policy scan and return a machine-usable verdict
   - usable from GitHub Actions or a webhook caller

---

## What we are explicitly not doing in this sprint

- full workspace/org system
- invite flows
- RBAC
- async worker architecture
- Postgres migration
- billing
- public docs/marketing launch
- many integrations

Those remain important, but they are **not the highest-leverage next 2 weeks**.

---

## Recommended immediate build slice

## Slice: **Projects + Saved Agents + Saved Policies + Release Verdicts**

This is the shortest path from “scanner demo” to “team workflow product.”

### Why this slice first
- introduces durable product objects teams understand
- creates a stable home for scan history and regressions
- makes CI integration possible
- creates a real reason to return to the product
- reuses most of the existing codebase instead of forcing a rewrite

---

# Sprint plan: 10 working days

## Week 1 — Build the product objects and verdict engine

### Day 1: Data model and API contract design

**Objective:** Freeze the schema and API shape before implementation.

**Deliverables:**
- data model sketch
- endpoint list
- verdict semantics doc

**Files:**
- Create: `docs/plans/2026-04-25-project-policy-api-spec.md`
- Review: `agentbench/server/models.py`
- Review: `agentbench/server/schemas.py`
- Review: `agentbench/server/routes/scans.py`

**Decisions to lock:**
- project model uses current authenticated principal as owner for now
- agent becomes a first-class saved resource
- scan request can reference `project_id`, `agent_id`, and optional `policy_id`
- release verdict enum = `pass | warn | fail`
- verdict reasons are explicit strings, not hidden in summary prose

**Acceptance criteria:**
- no implementation starts until the API shape is written down

---

### Day 2: Add project and saved-agent models

**Objective:** Introduce first-class entities for repeat usage.

**Files:**
- Modify: `agentbench/server/models.py`
- Modify: `agentbench/server/schemas.py`
- Create: `tests/test_project_api.py`

**Build:**
- `Project` becomes actively used by API
- add `SavedAgent` model
  - `id`
  - `project_id`
  - `name`
  - `agent_url`
  - `created_at`
- ensure project ownership is scoped to current principal

**Tests:**
- create project
- list projects
- create saved agent under project
- list saved agents for project
- deny cross-principal visibility

**Acceptance criteria:**
- a user can persist a project and a saved agent with tests passing

---

### Day 3: Add scan-policy model and schemas

**Objective:** Define reusable release rules.

**Files:**
- Modify: `agentbench/server/models.py`
- Modify: `agentbench/server/schemas.py`
- Create: `tests/test_policy_api.py`

**Build:**
- add `ScanPolicy` model
  - `id`
  - `project_id`
  - `name`
  - `categories_json`
  - `minimum_overall_score`
  - `minimum_domain_scores_json`
  - `fail_on_critical_issues`
  - `max_regression_delta`
  - `created_at`
- create policy request/response schemas

**Tests:**
- create/list policy
- validate malformed thresholds
- validate policy visibility is project/principal-scoped

**Acceptance criteria:**
- policy objects exist and are reusable across scans

---

### Day 4: Add project, agent, and policy routes

**Objective:** Make the new product objects usable through the API.

**Files:**
- Create: `agentbench/server/routes/projects.py`
- Create: `agentbench/server/routes/agents.py`
- Create: `agentbench/server/routes/policies.py`
- Modify: `agentbench/server/app.py`
- Test: `tests/test_project_api.py`
- Test: `tests/test_policy_api.py`

**Build:**
- `POST /api/v1/projects`
- `GET /api/v1/projects`
- `POST /api/v1/projects/{project_id}/agents`
- `GET /api/v1/projects/{project_id}/agents`
- `POST /api/v1/projects/{project_id}/policies`
- `GET /api/v1/projects/{project_id}/policies`

**Acceptance criteria:**
- API clients can create and retrieve all three resource types
- OpenAPI reflects concrete schemas

---

### Day 5: Add verdict engine to scans

**Objective:** Turn scan output into a release decision.

**Files:**
- Modify: `agentbench/server/routes/scans.py`
- Modify: `agentbench/server/schemas.py`
- Create: `tests/test_scan_verdicts.py`

**Build:**
- accept `project_id`, `agent_id`, and optional `policy_id` in scan submission
- attach project/policy metadata to scan persistence
- compute verdict based on policy:
  - overall threshold
  - per-domain threshold
  - critical-issue rule
  - regression threshold if prior baseline exists
- return:
  - `release_verdict`
  - `verdict_reasons`

**Acceptance criteria:**
- same scan can be pass under one policy and fail under another
- reasons are explicit and test-covered

---

## Week 2 — Make it usable in a real team workflow

### Day 6: Tie history and regression to saved agents

**Objective:** Move from raw URL semantics to product semantics.

**Files:**
- Modify: `agentbench/server/routes/scans.py`
- Modify: `agentbench/scanner/store.py`
- Test: `tests/test_scan_api.py`

**Build:**
- prefer `agent_id` as the stable identity for history/regression
- retain URL-based fallback only for backward compatibility
- ensure share payloads and listings expose agent name/project context

**Acceptance criteria:**
- history/regression work cleanly for saved agents even if URLs later change in display or metadata

---

### Day 7: CI-friendly scan execution endpoint

**Objective:** Create a simple machine-usable release gate.

**Files:**
- Modify: `agentbench/server/routes/scans.py`
- Modify: `agentbench/server/schemas.py`
- Create: `tests/test_scan_gate_api.py`

**Build:**
- add a dedicated endpoint like:
  - `POST /api/v1/projects/{project_id}/gate`
  - body: `agent_id`, `policy_id`
- response includes:
  - scan id
  - release verdict
  - reasons
  - overall score
  - permalink

**Acceptance criteria:**
- a CI system can call one endpoint and decide success/failure without scraping prose

---

### Day 8: Minimal project UI for saved agents and policies

**Objective:** Make the workflow visible in the product, not just the API.

**Files:**
- Modify: `site/app.html`
- Optional create: `site/app.js` if splitting logic becomes necessary
- Browser tests: `tests/browser/share-flow.spec.js`
- Create: `tests/browser/project-policy-flow.spec.js`

**Build:**
- simple project selector or single-project beta view
- saved agents list
- saved policy selector in scan flow
- display `pass/warn/fail` prominently in results and share area

**Acceptance criteria:**
- a user can choose a saved agent + policy before running a scan
- results clearly communicate release status

---

### Day 9: GitHub Actions / webhook starter integration

**Objective:** Put AgentBench into one real engineering workflow.

**Files:**
- Create: `.github/workflows/agentbench-gate.yml` or example workflow docs
- Create: `docs/integrations/github-actions.md`
- Optional create: `scripts/agentbench_gate.py`
- Test: lightweight integration test or docs-verified example

**Build:**
- example GitHub Action that:
  - calls gate endpoint
  - prints verdict + reasons
  - fails workflow on `fail`
- if Action packaging is too much for 2 weeks, ship a documented curl/python example that is production-usable enough for design partners

**Acceptance criteria:**
- one design partner could wire this into CI with minimal effort

---

### Day 10: Hardening, docs, and demo path

**Objective:** Make the slice usable by a design partner next week.

**Files:**
- Update docs under `docs/`
- Update roadmap docs under `docs/plans/`
- Add/expand tests as needed

**Build:**
- end-to-end smoke test checklist
- seeded demo data path
- API docs cleanup
- explicit “how to use AgentBench as a release gate” doc

**Acceptance criteria:**
- you can demo the full story in under 5 minutes:
  1. pick project
  2. pick agent
  3. pick policy
  4. run scan
  5. get pass/fail
  6. share link
  7. trigger from CI

---

## Suggested file additions / changes

### Likely new backend files
- `agentbench/server/routes/projects.py`
- `agentbench/server/routes/agents.py`
- `agentbench/server/routes/policies.py`
- `tests/test_project_api.py`
- `tests/test_policy_api.py`
- `tests/test_scan_verdicts.py`
- `tests/test_scan_gate_api.py`

### Likely existing files to modify
- `agentbench/server/models.py`
- `agentbench/server/schemas.py`
- `agentbench/server/routes/scans.py`
- `agentbench/server/app.py`
- `agentbench/scanner/store.py`
- `site/app.html`
- `tests/browser/share-flow.spec.js`

---

## Priority order

### Must ship in this sprint
1. projects
2. saved agents
3. saved policies
4. verdict engine
5. CI-friendly gate endpoint

### Nice to have if time remains
6. minimal project/policy UI
7. GitHub Action starter

### Explicitly defer
8. workspaces
9. async job system
10. Postgres migration
11. billing
12. enterprise auth

---

## Risks and tradeoffs

### Risk 1: Trying to do workspaces and projects at once
**Mitigation:** use principal-owned projects now; add workspaces next.

### Risk 2: Trying to build async jobs too early
**Mitigation:** keep synchronous scans for this slice if latency remains acceptable; validate real user pull first.

### Risk 3: Overbuilding policy complexity
**Mitigation:** only support the 4–5 threshold types needed for release gating.

### Risk 4: UI slowing down backend workflow progress
**Mitigation:** ship API-first; UI only needs to be good enough for demo and design-partner use.

---

## Success metrics for the 2-week sprint

This sprint is successful if, by the end:
- you can save at least one project, agent, and policy
- scans produce `pass/warn/fail`
- verdict reasons are explicit
- one CI caller can consume the result cleanly
- the product story sounds like “release gate” instead of “scanner”

---

## Recommended first task to start immediately

### Start with: **Day 1 + Day 2 + Day 3 backend contract work**

That means the immediate implementation order should be:
1. write the API spec doc
2. add `SavedAgent` model + tests
3. add `ScanPolicy` model + tests
4. add project/agent/policy routes

This is the highest-leverage start because everything else depends on these product objects.

---

## After this sprint

If this slice lands well, the next sprint should be:
1. async scan jobs
2. workspace model
3. better onboarding
4. stronger CI integration
5. Postgres migration prep

---

## Bottom line

For the next 2 weeks, do **not** try to boil the ocean.

Build the first real commercial slice:

**project-scoped saved agents + saved policies + pass/warn/fail release gating + one CI path**

That is the fastest path from prototype to something a team might genuinely adopt.
