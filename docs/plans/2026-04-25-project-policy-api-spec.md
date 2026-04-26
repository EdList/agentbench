# AgentBench Project / Saved Agent / Scan Policy API Spec

**Purpose:** Define the first product-scoped API layer that turns AgentBench from a raw URL scanner into a project-scoped release-gate workflow.

**Scope:** This spec covers:
- projects
- saved agents
- scan policies
- the updated scan submission contract needed for later verdict work

**Non-goals for this slice:**
- workspaces / org invites
- RBAC
- async jobs
- billing
- Postgres migration

---

## Ownership model

For this slice, ownership is still based on the existing authenticated `principal` returned by `require_auth`.

Rules:
- every project is owned by exactly one principal
- every saved agent belongs to exactly one project
- every scan policy belongs to exactly one project
- cross-principal reads return `404` for nested resources or filter objects out of list responses

---

## Resource model

### Project
- `id: str`
- `name: str`
- `description: str | null`
- `created_at: datetime`

### SavedAgent
- `id: str`
- `project_id: str`
- `name: str`
- `agent_url: str`
- `created_at: datetime`

### ScanPolicy
- `id: str`
- `project_id: str`
- `name: str`
- `categories: list[str] | null`
- `minimum_overall_score: float | null`
- `minimum_domain_scores: dict[str, float]`
- `fail_on_critical_issues: bool`
- `max_regression_delta: float | null`
- `created_at: datetime`

---

## Endpoints

## Projects

### `POST /api/v1/projects`
Create a project for the authenticated principal.

Request:
```json
{
  "name": "Support Agent",
  "description": "Customer support release gate"
}
```

Response `201`:
```json
{
  "id": "proj_123",
  "name": "Support Agent",
  "description": "Customer support release gate",
  "created_at": "2026-04-25T05:00:00Z"
}
```

### `GET /api/v1/projects`
List projects for the authenticated principal.

Response `200`:
```json
{
  "projects": [
    {
      "id": "proj_123",
      "name": "Support Agent",
      "description": "Customer support release gate",
      "created_at": "2026-04-25T05:00:00Z"
    }
  ],
  "total": 1
}
```

---

## Saved agents

### `POST /api/v1/projects/{project_id}/agents`
Create a saved agent inside a project.

Request:
```json
{
  "name": "Production Support Agent",
  "agent_url": "https://example.com/agent"
}
```

Response `201`:
```json
{
  "id": "agent_123",
  "project_id": "proj_123",
  "name": "Production Support Agent",
  "agent_url": "https://example.com/agent",
  "created_at": "2026-04-25T05:00:00Z"
}
```

### `GET /api/v1/projects/{project_id}/agents`
List saved agents inside a project.

Response `200`:
```json
{
  "agents": [
    {
      "id": "agent_123",
      "project_id": "proj_123",
      "name": "Production Support Agent",
      "agent_url": "https://example.com/agent",
      "created_at": "2026-04-25T05:00:00Z"
    }
  ],
  "total": 1
}
```

Errors:
- `404` if `project_id` is not visible to the authenticated principal

---

## Scan policies

### `POST /api/v1/projects/{project_id}/policies`
Create a reusable scan policy.

Request:
```json
{
  "name": "Release Gate",
  "categories": ["safety", "reliability"],
  "minimum_overall_score": 80,
  "minimum_domain_scores": {
    "Safety": 90
  },
  "fail_on_critical_issues": true,
  "max_regression_delta": -5
}
```

Response `201`:
```json
{
  "id": "policy_123",
  "project_id": "proj_123",
  "name": "Release Gate",
  "categories": ["safety", "reliability"],
  "minimum_overall_score": 80,
  "minimum_domain_scores": {
    "Safety": 90
  },
  "fail_on_critical_issues": true,
  "max_regression_delta": -5,
  "created_at": "2026-04-25T05:00:00Z"
}
```

### `GET /api/v1/projects/{project_id}/policies`
List scan policies for a project.

Response `200`:
```json
{
  "policies": [
    {
      "id": "policy_123",
      "project_id": "proj_123",
      "name": "Release Gate",
      "categories": ["safety", "reliability"],
      "minimum_overall_score": 80,
      "minimum_domain_scores": {
        "Safety": 90
      },
      "fail_on_critical_issues": true,
      "max_regression_delta": -5,
      "created_at": "2026-04-25T05:00:00Z"
    }
  ],
  "total": 1
}
```

Errors:
- `404` if `project_id` is not visible to the authenticated principal

---

## Future scan contract extension

This slice does not require the verdict engine yet, but the scan API should evolve toward this request shape:

### `POST /api/v1/scans`
Future request shape:
```json
{
  "project_id": "proj_123",
  "agent_id": "agent_123",
  "policy_id": "policy_123"
}
```

Backward-compatible fallback for the current scanner can temporarily remain:
```json
{
  "agent_url": "https://example.com/agent",
  "categories": ["safety"]
}
```

---

## Validation rules

### Project
- `name` required
- blank names rejected

### SavedAgent
- `name` required
- `agent_url` required
- `agent_url` should remain subject to current scan URL validation rules when used for scanning

### ScanPolicy
- `name` required
- `minimum_domain_scores` defaults to `{}`
- domain thresholds must be between `0` and `100`
- `minimum_overall_score` must be between `0` and `100` when present
- `categories` may be null to mean “all default categories”

---

## Response semantics

- object creation returns `201`
- list endpoints return `200`
- unauthorized access returns `401`
- missing / cross-principal nested parent returns `404`
- validation errors return `422`

---

## Rationale

This is the smallest API surface that creates durable product objects teams understand:
- project
- saved agent
- saved policy

Once these exist, AgentBench can evolve from “scan this URL” into “evaluate this saved agent against this saved release policy.”
