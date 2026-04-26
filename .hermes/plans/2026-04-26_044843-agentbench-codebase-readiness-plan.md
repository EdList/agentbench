# AgentBench Codebase Readiness Plan

> **For Hermes:** Planning only. Do not implement from this document without an explicit execution request.

**Goal:** Turn the current AgentBench codebase into a launchable, internally coherent product by fixing the real correctness/deployment risks surfaced by a full codebase audit and by narrowing any surface that is not actually ready.

**Architecture:** AgentBench is currently two products in one repo: (1) a behavioral test framework/CLI and (2) a teams-first scan/report/gate web product. The right next move is **not** to add more features. It is to make the already-claimed surfaces reliable, deployment-safe, and honestly scoped. For the server product, keep FastAPI + SQLAlchemy + static web UI and standardize on one persistence story. For the framework product, fix execution semantics and tighten public contracts before broadening adapter/experimental claims.

**Tech Stack:** Python 3.11, Typer, FastAPI, SQLAlchemy, httpx, static HTML/JS UI, Playwright, pytest.

---

## Audit basis

This plan is based on a read-through of the existing codebase plus targeted audits of:
- `agentbench/core/`, `agentbench/cli/`, `agentbench/adapters/`, `agentbench/storage/`
- `agentbench/scanner/`, `agentbench/server/`, `site/app.html`, CI/gate scripts
- `agentbench/adversarial/`, `agentbench/property/`, `agentbench/multiagent/`
- the corresponding Python and browser tests

Grounded current state:
- `pygount` summary: **135 files**, **18,694 LOC**, including **84 Python files / 16,155 Python LOC**
- full suite currently passes: **765 passed** via `python -m pytest -q`
- browser coverage exists for share flow and project selection
- the repo is mid-flight with many modified/untracked files, so the next execution pass should be **surgical and phased**

---

## What is already solid

These areas are in good enough shape that they should be preserved, not rewritten:

### Server / product surface
- principal scoping for projects, saved agents, policies, scan retrieval, history, regression, and share payloads is materially better than before
- SSRF defense is now stronger than average: scheme checks, hostname/IP checks, request-time DNS revalidation
- sync + async scan flows are coherent and covered
- share-by-`scan_id` and deep-link behavior are implemented and browser-tested
- GitHub gate script is reasonably solid for the current async pass/fail flow

### Framework / CLI surface
- core assertion API is generally strong
- Raw API and LangChain are the strongest adapters
- `run`, `init`, `list`, and HTML reporting are fairly healthy
- storage CRUD / path sanitization look good

### Experimental modules
- low-level mutator/generator/shrinker pieces are decent raw building blocks
- property-based testing is the closest of the experimental surfaces to being productizable

---

## What actually needs work

These are the highest-value findings from the audit, grouped into launch-relevant buckets.

### A. Product contract and deployment safety
1. **Server persistence is still split-brain**
   - `agentbench/server/config.py` defaults to `scan_store_mode="local"`
   - `agentbench/scanner/store.py` supports both local SQLite and server-backed store
   - async jobs still run in daemon threads inside the web process
   - startup stale-job handling is too blunt for real deployment

2. **Policy semantics are not tight enough**
   - `ScanPolicyCreateRequest.categories` accepts arbitrary strings
   - minimum domain score keys are not canonicalized/validated
   - subset-category scans currently score missing domains as zero, which makes partial scans misleading

3. **Scanner-generated tests are not trustworthy yet**
   - `agentbench/scanner/generator.py` emits assertions that do not match the real API (`to_refuse` does not exist)
   - several generated behaviors degrade into placeholders or semantically weak assertions

4. **Share/auth semantics are still too fuzzy for launch**
   - the UI copy still implies easy team sharing but does not clearly communicate auth requirements
   - query-param API key ingestion in the web UI is still a security smell

### B. Core framework correctness
5. **Timeouts do not actually stop execution**
   - `agentbench/core/runner.py` marks timeout failures but lets worker threads keep running

6. **Parallel execution leaks shared mutable state**
   - `run_suite()` copies class instance internals into per-test instances
   - this can invalidate failure-injection behavior and other stateful tests

7. **Fixture contract is oversold**
   - `agentbench/core/fixtures.py` suggests injection support
   - the runner does not actually perform pytest-style fixture injection into test method parameters

8. **`record` / auto-`diff` can target the wrong suite**
   - `_find_adapter_in_path()` + `record()` fallback logic in `agentbench/cli/main.py` can pick the first discovered adapter rather than the requested target

9. **Trajectory diffing misses real regressions**
   - final response presence/absence and tool output drift are not compared strongly enough

### C. Claimed surface vs actual maturity
10. **Adapter readiness claims are too broad**
    - README says 6 framework adapters are ready
    - tests heavily cover Raw API / LangChain only
    - packaging extras do not line up cleanly with all claimed adapters
    - some integrations behave more like scaffolds than launch-grade adapters

11. **Adversarial CLI is not shippable as-is**
    - generated files contain placeholder `pass`
    - generated class output is not a reliable runnable suite
    - current adversarial execution does not truly preserve base assertions

12. **Property testing has one important correctness hole**
    - `no_pii_leakage()` only scans step-level exposed data, not `final_response`

13. **Multi-agent testing is not launch-ready**
    - conversation expectations do not currently fail the suite
    - topology API is mostly aspirational/stubbed
    - some assertions are vacuous or weak by default

---

## Strategic recommendation

**Do not try to “finish everything.”**

The best path is to:
1. **stabilize the marketed/core product surfaces first**
2. **fix the core framework correctness bugs next**
3. **narrow or downgrade everything else that is not truly ready**

That means:
- launch around **scan/report/gate + core behavioral test engine**
- treat **adapters beyond Raw API/LangChain** as conditional/experimental until validated
- treat **adversarial + multi-agent** as experimental, not launch promises
- keep **property-based testing** in scope only after the key correctness gap is fixed

---

# Phase 1 — Lock the launch surface and product contract

## Objective
Decide what AgentBench is actually shipping in the next release, based on the current code rather than aspiration.

## Deliverable
A short product/engineering contract document that explicitly classifies every major surface as:
- **Launch-ready core**
- **Beta/experimental**
- **Internal/not marketed**

## Files to inspect/update
- `README.md`
- `docs/getting-started.md`
- `site/app.html`
- `agentbench/cli/main.py`
- optionally new docs note under `docs/quickstart/` or `docs/plans/`

## Recommended decisions

### Core / launch-ready
- scan/report/gate server workflow
- project/saved agent/policy flows
- share-by-`scan_id` (private/authenticated)
- core behavioral test engine
- Raw API adapter
- LangChain adapter
- basic CLI (`run`, `init`, `report`, `list`, `serve`)

### Experimental / not launch promises yet
- adapters beyond Raw API + LangChain unless validated in the next phase
- adversarial CLI generation
- multi-agent testing
- property-based testing until its safety checks are corrected and end-to-end validated

## Acceptance criteria
- README feature table matches audited reality
- roadmap/status text is consistent with actual maturity
- experimental surfaces are explicitly labeled as such

---

# Phase 2 — Make the server/scanner product deployment-safe and contractually coherent

## Objective
Make the teams-first scan/report/gate product the most reliable part of the repo.

## Why this comes first
This is the clearest product wedge in the codebase, and it already has the strongest end-to-end story.

## Workstream 2.1 — Standardize persistence

### Problems to solve
- local node-scoped scan storage is still the default
- async jobs and persisted scan history are not using a clearly canonical deployment model
- startup stale-job cleanup is too blunt for real multi-instance deployment

### Files likely to change
- `agentbench/server/config.py`
- `agentbench/scanner/store.py`
- `agentbench/server/routes/scans.py`
- `agentbench/server/app.py`
- `agentbench/server/models.py`
- tests:
  - `tests/test_scan_api.py`
  - `tests/test_scan_jobs_api.py`
  - new `tests/test_server_scan_store.py`

### Plan
1. Make **server-backed persistence** the default for API/server deployments.
2. Treat `ScanJob` + server-backed report fields as the authoritative persisted scan path for now.
3. Keep local `ScanStore` only for standalone/local fallback and explicit tests.
4. Replace web-process daemon-thread assumptions with a clearer worker ownership model, even if the first step is still modest.
5. Rework stale-job handling so it does not blindly fail in-flight work on startup without ownership/lease semantics.

### Acceptance criteria
- sync scan, async scan job, history, regression, and share all work correctly with server-backed persistence
- restart behavior works without relying on `_scan_store`
- startup on an empty DB still succeeds
- tests explicitly cover server-mode behavior, not just local mode

## Workstream 2.2 — Tighten policy semantics

### Problems to solve
- policies accept invalid categories/domain keys
- partial scans currently tank overall score by assigning zero to non-run domains

### Files likely to change
- `agentbench/server/schemas.py`
- `agentbench/server/routes/policies.py`
- `agentbench/server/routes/scans.py`
- `agentbench/scanner/scorer.py`
- tests:
  - `tests/test_project_api.py`
  - `tests/test_scan_api.py`
  - new scorer-focused tests if needed

### Plan
1. Introduce canonical enums/mappings for user-facing policy categories and scoring domains.
2. Validate policy inputs at creation time instead of allowing bad values to fail late or be ignored.
3. Decide and enforce one rule for partial scans:
   - either `N/A` for non-run domains and exclude from overall score/gating
   - or disallow subset scans for gate decisions until semantics are explicit
4. Reflect the same semantics in API responses and UI labels.

### Acceptance criteria
- invalid categories/domain thresholds are rejected at create time
- partial scans no longer produce misleading overall scores
- policy verdict logic is aligned with scoring semantics

## Workstream 2.3 — Repair scanner-generated test output

### Problems to solve
- generated tests call nonexistent assertions
- several mappings are placeholders or semantically weak

### Files likely to change
- `agentbench/scanner/generator.py`
- `agentbench/scanner/analyzer.py`
- `agentbench/core/assertions.py` (only if a missing assertion is genuinely worth adding)
- tests:
  - `tests/test_scanner_generator.py`
  - `tests/test_scanner_cli.py`
  - possibly new generated-suite execution tests

### Plan
1. Audit every `behavior.test_type` emitted by the analyzer.
2. Map each one to a real executable assertion or explicit generated assertion code.
3. Remove/replace fake mappings like `to_refuse()` unless the assertion is implemented for real.
4. Add tests that actually run generated suites, not just `compile()` them.

### Acceptance criteria
- every generated assertion path is real and executable
- generated suite tests prove behavior end to end

---

# Phase 3 — Fix the core framework correctness bugs

## Objective
Make the behavioral test framework trustworthy under real use, especially in CI and parallel execution.

## Workstream 3.1 — Real timeout isolation

### Files likely to change
- `agentbench/core/runner.py`
- possibly `agentbench/core/sandbox.py`
- tests:
  - `tests/test_core.py`
  - `tests/test_sprint2_core.py`
  - new timeout isolation regression tests

### Plan
1. Stop using a daemon-thread timeout model that reports failure while work continues.
2. Move timed test execution into a killable isolation boundary (process/subprocess/sandbox).
3. Ensure timeouts cannot continue mutating state after the test has been marked failed.

### Acceptance criteria
- a timed-out test really stops executing
- no post-timeout side effects leak into later tests

## Workstream 3.2 — Eliminate shared mutable state leakage

### Files likely to change
- `agentbench/core/runner.py`
- `agentbench/core/test.py`
- tests:
  - `tests/test_core.py`
  - `tests/test_sprint2_core.py`

### Plan
1. Separate legitimate class hook state from framework internals.
2. Avoid copying mutable internal injection/trajectory state between test instances.
3. Add a regression test proving failure injection behaves identically in sequential and parallel modes.

### Acceptance criteria
- parallel mode does not change behavioral outcomes relative to sequential mode
- internal runner state is not shared unintentionally

## Workstream 3.3 — Either implement fixture injection or narrow the contract

### Files likely to change
- `agentbench/core/fixtures.py`
- `agentbench/core/runner.py`
- docs in `README.md` / `docs/getting-started.md`
- tests for fixture execution

### Plan
Choose one of these and commit to it:
1. **Implement real fixture parameter injection** into test methods/setup hooks
2. **Narrow docs/examples** so fixtures are only described as explicit values/helpers, not pytest-style injection

### Recommendation
Implement it only if it is small and testable. Otherwise narrow the contract now.

## Workstream 3.4 — Fix `record` / `diff` targeting and storage diff blind spots

### Files likely to change
- `agentbench/cli/main.py`
- `agentbench/storage/trajectory.py`
- tests:
  - `tests/test_cli.py`
  - `tests/test_storage.py`
  - new `record/diff` E2E tests

### Plan
1. Make `record` and auto-`diff` resolve a specific requested target, not “first adapter found”.
2. Persist enough identity metadata in golden files to re-run the correct thing.
3. Extend diffs to include final-response presence/absence and tool output changes.

### Acceptance criteria
- multi-suite repos do not record/diff the wrong agent
- trajectory diff catches real output drift that currently passes silently

---

# Phase 4 — Clean up adapter claims and packaging

## Objective
Make adapter support honest and test-backed.

## Files likely to change
- `pyproject.toml`
- `README.md`
- `agentbench/adapters/*.py`
- tests:
  - `tests/test_adapters.py`
  - new per-adapter contract tests where justified

## Plan
1. Audit each adapter claimed as “ready” against:
   - installability / extras
   - runnable happy-path tests
   - actual behavior fidelity
2. For each adapter beyond Raw API/LangChain, choose:
   - **promote** by finishing integration + adding tests
   - or **downgrade** to experimental/not officially supported yet
3. Ensure package extras reflect actual supported adapters.

## Acceptance criteria
- README adapter status table matches real test-backed support
- packaging extras and docs do not promise missing integrations

---

# Phase 5 — Narrow and harden experimental surfaces

## Objective
Keep useful experimental code, but stop treating it as equivalent to the core launch surface.

## Workstream 5.1 — Property-based testing

### Files likely to change
- `agentbench/property/properties.py`
- `tests/test_property.py`

### Plan
1. Fix `no_pii_leakage()` so it inspects `final_response` (and likely `error`) in addition to step-level exposure.
2. Add regression tests for step-less/final-response-only leaks.
3. After that, reassess whether property testing can be labeled beta-ready.

## Workstream 5.2 — Adversarial testing

### Files likely to change
- `agentbench/cli/main.py`
- `agentbench/adversarial/discovery.py`
- `agentbench/core/runner.py`
- tests:
  - `tests/test_adversarial.py`
  - CLI E2E tests

### Plan
1. Do **not** ship adversarial file generation as launch-ready until it emits runnable suites.
2. Rework adversarial execution so it preserves base test semantics instead of acting like a smoke test.
3. Add variant-count/token-budget caps that are safe for real model costs.

## Workstream 5.3 — Multi-agent testing

### Files likely to change
- `agentbench/multiagent/assertions.py`
- `agentbench/multiagent/test.py`
- `agentbench/multiagent/patterns.py`
- `agentbench/core/runner.py`
- `tests/test_multiagent.py`

### Plan
1. Make failed conversation expectations fail the suite for real.
2. Decide whether topology/routing semantics will actually be implemented now.
3. If not, explicitly narrow the surface to simple sequential simulation utilities.
4. Strengthen vacuous assertions like completion/participation checks.

### Acceptance criteria
- multi-agent failures can no longer pass silently
- topology claims are either implemented or explicitly downgraded

---

# Phase 6 — UI/auth/report-sharing polish for the shipped contract

## Objective
Align the user-facing UI with the actual backend contract.

## Files likely to change
- `site/app.html`
- `agentbench/server/schemas.py`
- `agentbench/server/routes/scans.py`
- `scripts/agentbench_gate.py`
- docs under `docs/quickstart/` or `docs/integrations/`
- browser tests in `tests/browser/`

## Plan
1. Make share/auth semantics explicit in UI copy.
2. Remove or replace query-param API key ingestion in the browser.
3. Display release verdict + reasons prominently in the results UI when policy-backed scans are present.
4. Ensure CI/shared-link copy states whether auth is required.

## Acceptance criteria
- UI no longer implies public sharing if the product is actually private/authenticated
- release verdicts are visible in the main product surface
- browser tests cover the updated share/auth/verdict flow

---

# Verification plan

## Per-phase targeted verification

### Server/scanner product
```bash
python -m pytest tests/test_scan_api.py -q
python -m pytest tests/test_scan_jobs_api.py -q
python -m pytest tests/test_project_api.py -q
python -m pytest tests/test_gate_script.py -q
python -m pytest tests/test_scanner_generator.py -q
python -m pytest tests/test_scanner_cli.py -q
```

### Core framework
```bash
python -m pytest tests/test_core.py -q
python -m pytest tests/test_sprint2_core.py -q
python -m pytest tests/test_cli.py -q
python -m pytest tests/test_sprint2_cli.py -q
python -m pytest tests/test_storage.py -q
```

### Experimental surfaces
```bash
python -m pytest tests/test_property.py -q
python -m pytest tests/test_adversarial.py -q
python -m pytest tests/test_multiagent.py -q
```

### Browser/UI
```bash
npm test
```

## Final regression gate
```bash
python -m pytest -q
```

---

## Recommended execution order

1. **Phase 1 — launch surface contract**
2. **Phase 2 — server/scanner deployment + policy/scoring + generator correctness**
3. **Phase 3 — framework correctness (timeouts, parallel state, record/diff)**
4. **Phase 4 — adapter claim cleanup**
5. **Phase 5 — experimental surface narrowing/hardening**
6. **Phase 6 — UI/auth/report-sharing polish**
7. **Final full verification**

This order is intentional:
- it fixes the most user-facing and monetizable surface first
- it prevents docs/UI from promising features that are not actually ready
- it delays risky breadth work until the core contracts are stable

---

## Risks and tradeoffs

### Risk 1 — trying to finish every surface at once
This repo contains more product surface than one pass should absorb.

**Mitigation:** narrow claims first, then harden selectively.

### Risk 2 — over-refactoring the data model
Persistence is imperfect, but introducing a brand-new scan model may create churn.

**Mitigation:** standardize the current server-backed path first; only split models if a concrete blocker appears.

### Risk 3 — hiding framework bugs behind passing tests
The suite is green, but the audit found important blind spots.

**Mitigation:** add regression tests for exactly the discovered semantic failures before broader changes.

### Risk 4 — shipping ambiguous auth/share behavior
This causes trust and adoption problems even when backend logic is correct.

**Mitigation:** explicit UI/docs/API contract and browser coverage.

---

## Immediate recommended next step

If execution starts next, begin with **Phase 1 + the first slice of Phase 2**:

1. lock the launch surface in docs/README/UI copy
2. make server-backed persistence the clearly authoritative server path
3. tighten policy/scoring semantics

That gives AgentBench a coherent product story before touching the broader framework and experimental surfaces.
