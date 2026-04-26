# GitHub Feedback + Packaging Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Make AgentBench feel design-partner-ready by enriching the GitHub release-gate workflow with PR feedback, clearer outputs, and private-beta onboarding docs.

**Architecture:** Keep the existing synchronous gate endpoint, but make the GitHub helper script smarter: generate absolute report links from the configured base URL, support stricter warn handling, emit markdown bodies for step summaries and PR comments, and let the example workflow post/update a sticky PR comment. Add docs that explain the release-gate path and provide copy-pasteable policy examples.

**Tech Stack:** FastAPI API, Python helper script, GitHub Actions workflow YAML, pytest, markdown docs.

---

### Task 1: Add failing tests for richer gate-helper behavior
- Extend `tests/test_gate_script.py` to cover:
  - absolute report URL output generation
  - optional fail-on-warn handling
  - generated PR comment markdown / step summary output
- Run the targeted test file to verify failure.

### Task 2: Implement richer gate-helper outputs
- Modify `scripts/agentbench_gate.py` to:
  - normalize an absolute report URL from `AGENTBENCH_BASE_URL` + relative permalink
  - support `AGENTBENCH_FAIL_ON_WARN`
  - emit markdown comment / summary outputs to `GITHUB_OUTPUT`
  - optionally write a markdown summary to `GITHUB_STEP_SUMMARY`
- Re-run targeted tests until green.

### Task 3: Upgrade the example workflow
- Modify `.github/workflows/agentbench-gate.yml` to:
  - add the permissions needed for PR comments
  - pass optional strict-mode variables
  - post or update a sticky PR comment on pull requests
  - preserve useful workflow summary output even on failure
- Validate YAML.

### Task 4: Improve integration and release-gate docs
- Update `docs/integrations/github-actions.md`
- Create a publishable quickstart doc for the release-gate loop, including sample policy config and setup steps.

### Task 5: Verify end-to-end quality
- Run targeted tests for the helper + API files
- Run the full pytest suite
- Summarize what is now ready for private beta vs. what still blocks a wider public launch.
