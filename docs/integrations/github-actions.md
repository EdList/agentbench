# GitHub Actions integration for AgentBench Gate

AgentBench now ships a **reusable GitHub Action** plus an example workflow.

That means teams can wire AgentBench into their own repos without copying the helper script manually, and the action now supports **async scan jobs + polling** out of the box.

## What ships
- Reusable action: `action.yml`
- Helper script used by the action: `scripts/agentbench_gate.py`
- Example workflow for this repo: `.github/workflows/agentbench-gate.yml`

## Reusable action usage
In another repository, you can use AgentBench directly from this repo:

```yaml
name: AgentBench Gate

on:
  pull_request:

permissions:
  contents: read
  issues: write
  pull-requests: write

jobs:
  gate:
    runs-on: ubuntu-latest
    steps:
      - name: Run AgentBench gate
        id: agentbench
        uses: EdList/agentbench@main
        with:
          base_url: ${{ vars.AGENTBENCH_BASE_URL }}
          project_id: ${{ vars.AGENTBENCH_PROJECT_ID }}
          agent_id: ${{ vars.AGENTBENCH_AGENT_ID }}
          policy_id: ${{ vars.AGENTBENCH_POLICY_ID }}
          fail_on_warn: ${{ vars.AGENTBENCH_FAIL_ON_WARN || 'false' }}
          timeout_seconds: ${{ vars.AGENTBENCH_TIMEOUT_SECONDS || '900' }}
          poll_interval_seconds: ${{ vars.AGENTBENCH_POLL_INTERVAL_SECONDS || '5' }}
          api_key: ${{ secrets.AGENTBENCH_API_KEY }}
```

For partner rollouts, prefer a versioned ref such as `EdList/agentbench@v0` instead of tracking `@main` forever.

The in-repo example workflow uses `uses: ./` so this repository dogfoods the published action.

## Required GitHub configuration

### Repository Variables
Add these as **Repository Variables**:
- `AGENTBENCH_BASE_URL`
- `AGENTBENCH_PROJECT_ID`
- `AGENTBENCH_AGENT_ID`
- `AGENTBENCH_POLICY_ID`

### Optional Repository Variables
- `AGENTBENCH_FAIL_ON_WARN`
  - `false` or unset → `warn` keeps the workflow green
  - `true` → `warn` fails the workflow just like `fail`
  - today this only matters if your AgentBench backend is configured to emit `warn` verdicts
- `AGENTBENCH_TIMEOUT_SECONDS`
  - maximum total wait time for the async scan job
- `AGENTBENCH_POLL_INTERVAL_SECONDS`
  - poll interval between job status checks

### Repository Secret
Add this as a **Repository Secret**:
- `AGENTBENCH_API_KEY`

## Action behavior
The helper script behind the action:
1. validates required inputs/environment
2. creates an async gate job with `POST /api/v1/projects/{project_id}/gate/jobs`
3. polls `GET /api/v1/scans/jobs/{job_id}` until the job reaches a terminal state
4. builds an **absolute report URL** from `AGENTBENCH_BASE_URL` + the returned permalink
5. prints the verdict, score, grade, job id, reasons, and report URL
6. appends a markdown summary to `GITHUB_STEP_SUMMARY`
7. emits GitHub outputs
8. exits with:
   - `0` for `pass`
   - `0` for `warn` unless `fail_on_warn=true`
   - `1` for `fail`, or `warn` in strict mode
   - `2` for malformed responses, non-completed jobs, configuration problems, polling timeouts, or request failures

## Action inputs
- `base_url`
- `api_key`
- `project_id`
- `agent_id`
- `policy_id`
- `fail_on_warn` (optional)
- `timeout_seconds` (optional)
- `poll_interval_seconds` (optional)

## Action outputs
- `job_id`
- `scan_id`
- `release_verdict`
- `permalink`
- `report_url`
- `overall_score`
- `overall_grade`
- `fail_on_warn`
- `should_fail`
- `verdict_reasons_json`
- `pr_comment_body`
- `step_summary_markdown`

## Sticky PR comment pattern
The example workflow includes a PR-comment step that:
- runs on pull requests with `always()`
- creates or updates a comment marked with `<!-- agentbench:gate -->`
- shows verdict, score, reasons, job id, and a clickable report link

Those report links are intended for authenticated AgentBench users on the team; they are not anonymous public links.

## Required workflow permissions
The example workflow declares:
- `contents: read`
- `issues: write`
- `pull-requests: write`

Those permissions are required for sticky PR comment behavior.

## Manual local run
```bash
export AGENTBENCH_BASE_URL="https://agentbench.example.com"
export AGENTBENCH_API_KEY="***"
export AGENTBENCH_PROJECT_ID="proj_123"
export AGENTBENCH_AGENT_ID="agent_123"
export AGENTBENCH_POLICY_ID="policy_123"
export AGENTBENCH_FAIL_ON_WARN="false"
export AGENTBENCH_TIMEOUT_SECONDS="900"
export AGENTBENCH_POLL_INTERVAL_SECONDS="5"
python scripts/agentbench_gate.py
```

## Expected console output
```text
AgentBench gate job queued: job_123
AgentBench gate verdict: FAIL | score=72.0 | grade=C | job=job_123
Reasons:
- Overall score 72.0 is below the required 80.0.
Report: https://agentbench.example.com/?scan_id=scan_456
```

## Example PR comment
```md
<!-- agentbench:gate -->
## AgentBench Gate
**Verdict:** FAIL
**Score:** 72.0 · **Grade:** C
**Scan ID:** `scan_456`
**Job ID:** `job_123`
**Report:** [Open report](https://agentbench.example.com/?scan_id=scan_456)

### Reasons
- Overall score 72.0 is below the required 80.0.
```

## Important GitHub caveat
If you run this on `pull_request` events from forks, GitHub usually withholds repository secrets.

That means `AGENTBENCH_API_KEY` may be unavailable and the gate will fail with an input/request error.

For private beta, the safest assumption is:
- internal repos or trusted contributors → use the PR comment flow as shown
- public OSS with fork PRs → start with summary-only behavior, or carefully evaluate `pull_request_target` before changing triggers

## Notes
- The report URL is absolute so GitHub summaries and PR comments are clickable.
- Unknown or malformed verdicts fail closed with exit code `2`.
- Non-completed async jobs also fail closed.
- If you do **not** want PR comments, remove the `actions/github-script` step and rely on `GITHUB_STEP_SUMMARY` only.
