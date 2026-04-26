# AgentBench Launch Hardening Plan

> **For Hermes:** Plan only. Do not implement from this document without an explicit execution request. Use `subagent-driven-development` for execution if/when requested.

**Goal:** Finish the remaining launch-hardening work after the SSRF fix so AgentBench has a single trustworthy persistence story, a clear report-sharing policy, and a scoped public surface for private beta.

**Architecture:** Keep the current FastAPI + static web app + SQLAlchemy stack. Do **not** introduce a brand-new storage model unless a blocker forces it. For private beta, treat `ScanJob` + `ServerScanStore` as the canonical persisted scan record in server deployments, keep report sharing private-by-default, and narrow/label the experimental eval surfaces instead of broadening product claims.

**Tech Stack:** FastAPI, SQLAlchemy, SQLite now / Postgres later, static HTML/JS UI, pytest, Playwright.

---

## Current grounded context

1. **SSRF hardening is already done and verified.**
   - `agentbench/server/routes/scans.py` now blocks non-global IPs and re-checks DNS at request time.
   - Full suite currently passes: **765 passed**.

2. **Persistence is still split-brain at the product level.**
   - `agentbench/server/config.py` still defaults `AGENTBENCH_SCAN_STORE_MODE` to `local`.
   - `agentbench/scanner/store.py` contains both:
     - `ScanStore` backed by `~/.agentbench/scans.db`
     - `ServerScanStore` backed by SQLAlchemy `ScanJob` rows
   - `agentbench/server/routes/scans.py` still keeps `_scan_store` in memory as a warm-path cache.

3. **Server-mode persistence exists but is not the clearly dominant path yet.**
   - `ServerScanStore` reads/writes through `ScanJob.report_json` and `ScanJob.domain_scores_json`.
   - Most current tests force `scans_mod.store = ScanStore(...)`, so they validate the local-path behavior more heavily than the server-path behavior.

4. **Share links are safer than before but the product contract is still unclear.**
   - `ScanShareResponse` exists.
   - `site/app.html` exposes share UI.
   - There is still an unresolved product question: are reports **private to authenticated members only**, or can they be opened by unauthenticated recipients via signed links?

5. **Experimental eval surfaces still need launch scoping.**
   - Relevant modules live under:
     - `agentbench/adversarial/`
     - `agentbench/property/`
     - `agentbench/multiagent/`
   - These areas have tests, but they have not yet been explicitly framed as either:
     - launch-ready core product surfaces, or
     - experimental/non-core capabilities.

6. **The repo is already mid-flight.**
   - `git status --short` shows a dirty worktree with multiple modified and untracked files.
   - This means the next implementation pass should avoid unnecessary schema churn and should use narrowly scoped commits.

---

## Recommended product decisions before implementation

### Decision A — Persistence strategy for private beta
**Recommendation:** In API/server deployments, make **server-backed persistence** the default and canonical path.

- Keep `ScanJob` as the persisted scan artifact for now.
- Do **not** add a separate `scans` SQLAlchemy table in this pass.
- Keep local `ScanStore` only for standalone/local workflows and tests that explicitly need it.

**Why:**
- Lowest-risk path from current code.
- Avoids duplicating scan truth in two SQL-backed models.
- Matches the existing async job architecture.

### Decision B — Report sharing policy
**Recommendation:** For private beta, keep reports **private/authenticated by default**.

- Share text can be copied anywhere.
- Permalinks should work only for authenticated users with valid project access.
- UI and docs should say this explicitly.
- If public sharing is desired later, add **signed share tokens** as a separate feature, not as part of this hardening pass.

**Why:**
- Avoids accidental exposure of sensitive scan content.
- Fits the user’s teams-first/private posture.
- Prevents auth ambiguity in PRs/Slack before workspace membership is fully mature.

### Decision C — Experimental surfaces
**Recommendation:** For launch, **narrow the marketed/public surface** rather than expanding it.

- Keep adversarial/property/multi-agent modules in the repo.
- Mark them as experimental in docs/CLI copy unless they pass a focused audit.
- Do not position them as equally mature to the scan/report/gate workflow.

---

## Exit criteria for this hardening track

The work is done when all of the following are true:

1. API/server deployments use a **single authoritative persisted scan path** by default.
2. Sync scans, async scan jobs, history, regression, and share payloads all work under **server-backed persistence**.
3. Restart behavior is covered: persisted scans remain retrievable after in-memory cache loss.
4. Report-sharing behavior is explicit: users know whether a link requires auth.
5. Experimental eval modules are either audited and validated, or clearly marked non-core/experimental.
6. Targeted tests and full regression suite pass.
7. Browser smoke checks confirm the user-facing share/deep-link workflow still works.

---

# Phase 1 — Make server-backed persistence the primary product path

## Objective
Remove ambiguity between node-local history and API-server history.

## Files likely to change
- `agentbench/server/config.py`
- `agentbench/scanner/store.py`
- `agentbench/server/routes/scans.py`
- `agentbench/server/models.py`
- `agentbench/server/app.py`
- `tests/test_scan_api.py`
- `tests/test_scan_jobs_api.py`
- New: `tests/test_server_scan_store.py` (recommended)

## Plan

### 1.1 Define the persistence contract in code
Document and enforce:
- `local` mode = standalone/local fallback only
- `server` mode = API-server canonical mode

**Implementation direction:**
- Flip the default in `agentbench/server/config.py` from `local` to `server`.
- Keep env override support for explicit local-mode testing.
- Add a short code comment explaining why the API server defaults to server-backed persistence.

### 1.2 Tighten `ServerScanStore` behavior
Audit and stabilize these methods in `agentbench/scanner/store.py`:
- `save_scan()`
- `get_scan()`
- `list_scans()`
- `get_regression_report()`
- `_job_to_row_dict()`
- `_job_to_summary_dict()`

**Checks to explicitly cover:**
- job row exists before result is saved
- no duplicate row creation on sync vs async flows
- `scan_id` vs `job.id` semantics are consistent
- missing `report_json` rows are filtered safely
- principal filtering is always preserved
- timestamps/duration semantics are not misleading

### 1.3 Reduce the importance of `_scan_store`
Keep `_scan_store` only as a convenience cache, not as a product dependency.

**Rule:** if `_scan_store` is empty, user-visible behavior must still be correct.

Verify endpoints in `agentbench/server/routes/scans.py`:
- `POST /api/v1/scans`
- `POST /api/v1/scans/jobs`
- `GET /api/v1/scans`
- `GET /api/v1/scans/{scan_id}`
- `GET /api/v1/scans/history/{agent_url}`
- `GET /api/v1/scans/regression/{agent_url}`
- `GET /api/v1/scans/{scan_id}/share`

### 1.4 Validate startup and empty-DB boot
Because startup already calls:
- `create_tables()`
- `scans.fail_stale_scan_jobs()`

confirm the server still boots correctly when:
- DB is brand new
- `scan_jobs` exists but has no rows
- `AGENTBENCH_SCAN_STORE_MODE=server`

## Recommended tests

### New/updated tests
- `tests/test_server_scan_store.py`
  - save/get/list/regression round-trips against `ServerScanStore`
  - principal scoping in server mode
  - rows without `report_json` do not break list/regression
- `tests/test_scan_api.py`
  - sync scan flow under `server` mode
  - retrieval after `_scan_store.clear()` under `server` mode
  - history/regression/share all sourced from SQL-backed rows
- `tests/test_scan_jobs_api.py`
  - async scan job writes complete scan payload into authoritative server-backed store
  - cancel/failure flows do not create fake completed scan history

### Verification commands
```bash
.venv/bin/pytest tests/test_server_scan_store.py -q
.venv/bin/pytest tests/test_scan_api.py -q
.venv/bin/pytest tests/test_scan_jobs_api.py -q
```

---

# Phase 2 — Lock the report-sharing contract

## Objective
Make sharing behavior safe, explicit, and unsurprising for private beta users.

## Files likely to change
- `agentbench/server/schemas.py`
- `agentbench/server/routes/scans.py`
- `site/app.html`
- `tests/test_scan_api.py`
- New/updated browser checks under `tests/browser/`
- Docs:
  - `docs/getting-started.md`
  - `docs/quickstart/` (relevant pages)
  - optional integration docs under `docs/integrations/`

## Plan

### 2.1 Encode access semantics in the API
Extend or clarify `ScanShareResponse` so the frontend does not guess access semantics.

**Recommended fields to add or derive:**
- `access_level`: `authenticated` for now
- `access_note`: short human-readable warning, e.g. “Requires AgentBench access”

### 2.2 Align the UI copy with the real auth model
Update `site/app.html` share surface copy so it does **not** imply public accessibility.

Examples of desired UX language:
- “Copy a report link for teammates with AgentBench access.”
- “Recipients must be signed in with an authorized API key/account.”

### 2.3 Decide how CI/PR links should behave
For the current beta, links in CI comments should:
- point to `/?scan_id=...`
- work for authorized users only
- include surrounding comment text that says auth is required

If the current GitHub Action / gate script emits report URLs, verify that the generated copy matches this contract.

**Files to inspect if needed:**
- `scripts/agentbench_gate.py`
- `.github/workflows/agentbench-gate.yml`
- `action.yml`

## Recommended tests
- API test that share payload includes explicit access metadata (if added).
- Browser smoke test from a clean session that confirms the unauthenticated state is explained clearly.
- Regression test that share text does not leak raw agent URL secrets/embedded credentials.

### Verification commands
```bash
.venv/bin/pytest tests/test_scan_api.py -q
.venv/bin/pytest tests/test_gate_script.py -q
```

If browser tests cover this flow:
```bash
npm test -- --grep share
```

---

# Phase 3 — Audit and scope adversarial/property/multi-agent surfaces

## Objective
Prevent launch messaging from overclaiming maturity.

## Files likely to inspect/change
- `agentbench/adversarial/`
- `agentbench/property/`
- `agentbench/multiagent/`
- `agentbench/cli/main.py`
- `agentbench/cli/scaffold.py`
- `docs/getting-started.md`
- `docs/quickstart/`
- product/UI copy in `site/app.html` if these capabilities are mentioned there
- tests:
  - `tests/test_adversarial.py`
  - `tests/test_property.py`
  - `tests/test_multiagent.py`
  - `tests/test_cli.py`

## Plan

### 3.1 Inventory public entry points
List where these features are exposed today:
- CLI commands/options
- scaffold templates
- docs/quickstart text
- marketing/site copy
- import surface in package `__init__` modules

### 3.2 Classify each surface
For each area, choose one label:
- **Core/private-beta ready**
- **Experimental but available**
- **Internal/not marketed**

### 3.3 Apply the smallest safe change
For anything not clearly launch-ready:
- remove or soften product claims
- mark docs as experimental
- avoid deleting code unless it is actively harmful
- prefer feature narrowing over invasive refactors in this pass

### 3.4 Add focused confidence tests where needed
Only add tests that prove the launch contract, not a giant refactor.

Examples:
- CLI help text labels an area experimental
- scaffold output does not imply unsupported maturity
- public docs match actual capabilities

## Verification commands
```bash
.venv/bin/pytest tests/test_adversarial.py -q
.venv/bin/pytest tests/test_property.py -q
.venv/bin/pytest tests/test_multiagent.py -q
.venv/bin/pytest tests/test_cli.py -q
```

---

# Phase 4 — Final launch-hardening verification

## Objective
Prove the product still works end to end after the persistence and contract changes.

## Verification checklist

### Backend
- sync scan works
- async scan job works
- cancellation still works
- history/regression/share still work after restart/cache clear
- startup succeeds on empty DB
- auth scoping still holds across all scan endpoints

### Frontend
- run a scan from the web UI
- open the deep link by `scan_id`
- open the share surface
- verify copy reflects auth requirement
- switch between history items without attaching to the wrong scan

### CI / integration
- verify gate script still produces correct poll + verdict behavior
- verify any emitted report link text matches private-beta auth semantics

## Recommended commands
```bash
.venv/bin/pytest tests/test_scan_api.py -q
.venv/bin/pytest tests/test_scan_jobs_api.py -q
.venv/bin/pytest tests/test_gate_script.py -q
.venv/bin/pytest -q
```

If browser tests are available:
```bash
npm test
```

---

## Proposed execution order

1. **Phase 1 first** — persistence contract
2. **Phase 2 second** — sharing/auth contract
3. **Phase 3 third** — narrow experimental surface
4. **Phase 4 last** — full verification and release notes/doc updates

This order minimizes rework because report-sharing and product-surface cleanup both depend on knowing what persistence/auth contract is actually shipping.

---

## Risks and tradeoffs

### Risk 1 — Hidden dependence on local `ScanStore`
If any endpoint or UI path still implicitly assumes the local SQLite history file, flipping server mode to default may expose regressions.

**Mitigation:** add explicit server-mode tests before changing defaults in production config.

### Risk 2 — `ScanJob` is doing double duty
`ScanJob` currently represents both execution state and, effectively, the persisted scan artifact.

**Tradeoff:** this is acceptable for private beta if the contract is documented and tested. Avoid introducing a separate `scans` table unless the current model proves unworkable.

### Risk 3 — Share UX confusion
If the UI still implies public sharing while links require auth, users will think the feature is broken.

**Mitigation:** make auth requirement explicit in API metadata, UI labels, and CI comment text.

### Risk 4 — Experimental feature overhang
Leaving adversarial/property/multi-agent surfaces ambiguous creates a launch-risk even if the core scan workflow is solid.

**Mitigation:** explicitly downgrade messaging for any surface not ready for teams-first beta usage.

---

## Open questions for later (not blockers for the next implementation pass)

1. Should authenticated sharing eventually be scoped to **workspace membership** rather than raw principal/API key?
2. Do we want signed public report links later, or is private-team sharing sufficient for the product wedge?
3. When Postgres becomes standard, should scan artifacts stay on `ScanJob` or move to a dedicated `scans` table?
4. Which of adversarial/property/multi-agent should graduate first into the marketed product surface?

---

## Recommended next action

If you want, the next execution step should be:

**Phase 1 only:** make server-backed persistence the authoritative/default API-server path, with server-mode tests first and no broader refactor.
