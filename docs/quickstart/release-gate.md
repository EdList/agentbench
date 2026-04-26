# AgentBench release-gate quickstart

This is the fastest path to using AgentBench as a **team release gate**.

## What you need
- a running AgentBench server
- an API key
- one project
- one saved agent
- one scan policy
- the reusable GitHub Action in this repo (`uses: EdList/agentbench@main`)

## 1. Create a project
```bash
curl -X POST "$AGENTBENCH_BASE_URL/api/v1/projects" \
  -H "X-API-Key: $AGENTBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Support Copilot",
    "description": "Production support assistant"
  }'
```

## 2. Save the agent you want to gate
```bash
curl -X POST "$AGENTBENCH_BASE_URL/api/v1/projects/$PROJECT_ID/agents" \
  -H "X-API-Key: $AGENTBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "prod-api",
    "agent_url": "https://agent.example.com/respond"
  }'
```

## 3. Create a scan policy
A good starting policy for private beta:

```bash
curl -X POST "$AGENTBENCH_BASE_URL/api/v1/projects/$PROJECT_ID/policies" \
  -H "X-API-Key: $AGENTBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "release-gate-default",
    "minimum_overall_score": 80,
    "minimum_domain_scores": {
      "Safety": 85,
      "Reliability": 80
    },
    "fail_on_critical_issues": true,
      "max_regression_delta": -5
}'
```

`max_regression_delta` is the worst allowed score change versus the previous scan, so use a **negative** number. `-5` means “fail if this run regresses by more than 5 points.”

## 4. Test the gate locally
### Synchronous one-shot gate
```bash
curl -X POST "$AGENTBENCH_BASE_URL/api/v1/projects/$PROJECT_ID/gate" \
  -H "X-API-Key: $AGENTBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "'$AGENT_ID'",
    "policy_id": "'$POLICY_ID'"
  }'
```

### Async gate job
```bash
curl -X POST "$AGENTBENCH_BASE_URL/api/v1/projects/$PROJECT_ID/gate/jobs" \
  -H "X-API-Key: $AGENTBENCH_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "'$AGENT_ID'",
    "policy_id": "'$POLICY_ID'"
  }'
```

Expected async creation shape:
```json
{
  "job_id": "job_123",
  "status": "queued",
  "project_id": "proj_123",
  "agent_id": "agent_123",
  "policy_id": "policy_123",
  "agent_url": "https://agent.example.com/respond",
  "scan_id": null,
  "release_verdict": null,
  "verdict_reasons": []
}
```

Poll job status:
```bash
curl -H "X-API-Key: $AGENTBENCH_API_KEY" \
  "$AGENTBENCH_BASE_URL/api/v1/scans/jobs/$JOB_ID"
```

Completed jobs include machine-friendly fields such as:
- `scan_id`
- `release_verdict`
- `verdict_reasons`
- `overall_score`
- `overall_grade`
- `permalink`

## 5. Wire it into GitHub Actions
In the application repo you want to protect:

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

Configure these repo settings:

### Variables
- `AGENTBENCH_BASE_URL`
- `AGENTBENCH_PROJECT_ID`
- `AGENTBENCH_AGENT_ID`
- `AGENTBENCH_POLICY_ID`
- optional: `AGENTBENCH_FAIL_ON_WARN=true`
- optional: `AGENTBENCH_TIMEOUT_SECONDS=900`
- optional: `AGENTBENCH_POLL_INTERVAL_SECONDS=5`

### Secret
- `AGENTBENCH_API_KEY`

## 6. What teams see in GitHub
On each PR, AgentBench can now:
- create an async gate job
- poll until the scan finishes
- fail the workflow on unacceptable verdicts
- write a workflow summary
- emit a reusable PR comment body
- power a sticky PR comment with the verdict, reasons, and report link

**Note:** report links are meant for authenticated AgentBench users on your team. They are not anonymous public share links.

## Policy tuning guidance
Start with a forgiving policy, then tighten once you have scan history.

Recommended beta defaults:
- overall minimum: `75-80`
- critical issues: always fail
- regression delta: `-5`
- fail on warn in CI: keep off until your backend actually emits `warn` verdicts

## GitHub caveat for forked PRs
If your repo accepts PRs from forks, GitHub usually withholds secrets on `pull_request` events.

That means the AgentBench action may not receive `AGENTBENCH_API_KEY` for external contributors.
For private beta, prefer internal repos or trusted contributors first.

## Private beta ready, not fully public-ready
This workflow is now good enough for design partners and private beta.

Still needed before broad public launch:
- production-grade database migrations and Postgres
- rate limiting / abuse controls
- stronger org/workspace model
- onboarding, billing, and support instrumentation
- deeper ops/support tooling for failed-job investigation
