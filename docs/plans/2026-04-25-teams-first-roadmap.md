# Teams-First Product Hardening Plan

> **For Hermes:** Use subagent-driven-development skill to execute this plan task-by-task.

**Goal:** Turn AgentBench into a reliable team-facing product for scanning, regression tracking, and CI adoption.

**Architecture:** Keep the current FastAPI + static web UI architecture, but harden persistence and retrieval so scan results survive restarts and remain available to the dashboard and API clients. Prioritize team workflows before agent-facing APIs.

**Tech Stack:** FastAPI, SQLite, Pydantic, pytest, static HTML/JS UI.

---

## Completed today

### Task 1: Make scan retrieval restart-safe
- Persist scan reports even when the scan pipeline returns `ScanResponse` instead of `ScanReport`
- Fall back to SQLite for `GET /api/v1/scans` and `GET /api/v1/scans/{scan_id}`
- Preserve report metadata needed for summaries, history, and regression views

### Task 2: Add typed schemas for history and regression endpoints
- Added explicit Pydantic response models for scan history and regression reports
- Updated OpenAPI docs so these endpoints advertise concrete schemas instead of raw `dict` payloads
- Added endpoint tests covering typed payloads and OpenAPI schema references

### Task 3: Add clickable history drill-down in the web UI
- Made history entries interactive buttons with active/latest states and keyboard-friendly behavior
- Added historical report drill-down plus a “Back to Latest” recovery path
- Verified in the browser that clicking an older scan swaps the report view and that returning to latest restores the newest report

### Task 4: Add export/share workflow for teams
- Added one-click JSON download, HTML export, and copy-summary actions on the results page
- Added user-visible action status feedback for export/share actions
- Implemented a prompt-based fallback when clipboard copy is unavailable
- Verified in the browser that HTML/JSON export stubs fire and that copy-summary opens a copy-ready dialog

### Task 5: Add a team-facing share surface
- Added a typed `/api/v1/scans/{scan_id}/share` endpoint with permalink + PR/Slack-ready text blocks
- Added a dedicated share panel with copy-link / copy-for-PR / copy-for-Slack actions
- Added deep-link boot flow so `app.html?scan_id=...&agent_url=...` opens the exact shared report directly
- Kept the browser scanner wired to authenticated scan requests by default (`X-API-Key: demo`)
- Verification: `./.venv/bin/pytest -q` → 712 passed, plus local browser verification for deep-link loading, history-aware permalinks, and share-copy actions

---

## Next slices

### Task 6: Add team distribution hooks
**Objective:** Let teams push a scan summary directly into their workflow — e.g. Slack-ready formatting improvements, webhook delivery, or PR comment payloads from the API.

**Files:**
- Modify: `site/app.html`
- Modify: `agentbench/server/routes/scans.py`
- Test: add focused API tests for distribution payloads

**Verification:** `./.venv/bin/pytest -q`
