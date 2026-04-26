# AgentBench Blocker Remediation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Remove the release blockers from the deep audit so AgentBench’s scan/share flows are safe and team-ready.

**Architecture:** Fix security and correctness in the backend first, then align the browser UI with the corrected API contracts. Keep persistence in SQLite, but scope scans by authenticated principal and make share/deeplink behavior depend on `scan_id`, not raw agent URLs or guessed history ordering.

**Tech Stack:** FastAPI, SQLite, Pydantic, pytest, static HTML/JS UI.

---

## Completed

### Task 1: Enforce scan ownership by authenticated principal
- Added `principal` ownership to persisted scans with SQLite backfill for existing DBs
- Scoped scan retrieval, history, regression, and share payloads to the authenticated principal
- Added API tests proving cross-principal access now returns `404` or empty history

### Task 2: Fix share permalinks and move sharing to scan-id-first semantics
- `POST /api/v1/scans` now returns `scan_id`
- Scan responses rehydrate `scan_id` from persistence
- Share permalinks now use `/?scan_id=...` and share text no longer depends on raw agent URLs
- The browser uses server-provided `scan_id` instead of guessing the latest run from history ordering

### Task 3: Remove hardcoded browser auth mismatch
- Replaced hardcoded `demo` UI auth with `resolveApiKey()`
- Browser now prefers `window.AGENTBENCH_API_KEY`, query param `api_key`, localStorage, then falls back to `dev-key`

### Task 4: Harden SSRF validation
- Validation now resolves hostnames and alternate numeric formats via `socket.getaddrinfo`
- Added tests covering `2130706433`, `127.1`, and `0x7f000001`

### Task 5: Final verification
- `./.venv/bin/pytest tests/test_scan_api.py -q` → 28 passed
- `./.venv/bin/pytest -q` → 718 passed
- Browser smoke check passed for deep links, share permalinks, history switching, and auth default resolution
