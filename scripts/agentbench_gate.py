#!/usr/bin/env python3
"""GitHub Actions helper for calling the AgentBench project gate endpoint."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

REQUIRED_ENV_VARS = (
    "AGENTBENCH_BASE_URL",
    "AGENTBENCH_API_KEY",
    "AGENTBENCH_PROJECT_ID",
    "AGENTBENCH_AGENT_ID",
    "AGENTBENCH_POLICY_ID",
)
MULTILINE_DELIMITER = "AGENTBENCH_EOF"
PR_COMMENT_MARKER = "<!-- agentbench:gate -->"
TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def _load_config() -> dict[str, str]:
    missing = [name for name in REQUIRED_ENV_VARS if not os.getenv(name)]
    if missing:
        print(f"Missing required environment variables: {', '.join(missing)}")
        raise SystemExit(2)
    return {name: os.environ[name] for name in REQUIRED_ENV_VARS}


def _parse_bool(name: str, *, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_float(name: str, *, default: float) -> float:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    try:
        return float(value)
    except ValueError as exc:
        print(f"Invalid numeric value for {name}: {value!r}")
        raise SystemExit(2) from exc


def _headers(config: dict[str, str]) -> dict[str, str]:
    return {"X-API-Key": config["AGENTBENCH_API_KEY"]}


def _create_gate_job(config: dict[str, str]) -> dict[str, Any]:
    base_url = config["AGENTBENCH_BASE_URL"].rstrip("/")
    project_id = config["AGENTBENCH_PROJECT_ID"]
    response = httpx.post(
        f"{base_url}/api/v1/projects/{project_id}/gate/jobs",
        headers=_headers(config),
        json={
            "agent_id": config["AGENTBENCH_AGENT_ID"],
            "policy_id": config["AGENTBENCH_POLICY_ID"],
        },
        timeout=60.0,
    )
    response.raise_for_status()
    return response.json()


def _poll_gate_job(
    config: dict[str, str], job_id: str, *, timeout_seconds: float, poll_interval_seconds: float
) -> dict[str, Any]:
    base_url = config["AGENTBENCH_BASE_URL"].rstrip("/")
    deadline = time.monotonic() + timeout_seconds

    while True:
        response = httpx.get(
            f"{base_url}/api/v1/scans/jobs/{job_id}",
            headers=_headers(config),
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
        status_value = str(payload.get("status", "unknown")).lower()
        if status_value in TERMINAL_JOB_STATUSES:
            return payload
        if time.monotonic() >= deadline:
            print(f"Timed out waiting for AgentBench job {job_id} to finish.")
            raise SystemExit(2)
        if poll_interval_seconds > 0:
            time.sleep(poll_interval_seconds)


def _build_report_url(base_url: str, permalink: str | None) -> str:
    normalized_base = base_url.rstrip("/")
    if not permalink:
        return normalized_base
    if permalink.startswith(("http://", "https://")):
        return permalink
    return f"{normalized_base}/{permalink.lstrip('/')}"


def _build_pr_comment(payload: dict[str, Any], report_url: str) -> str:
    verdict = str(payload.get("release_verdict", "unknown")).upper()
    score = payload.get("overall_score", "n/a")
    grade = payload.get("overall_grade", "n/a")
    scan_id = payload.get("scan_id", "n/a")
    job_id = payload.get("job_id", "n/a")
    reasons = payload.get("verdict_reasons", []) or []

    lines = [
        PR_COMMENT_MARKER,
        "## AgentBench Gate",
        f"**Verdict:** {verdict}",
        f"**Score:** {score} · **Grade:** {grade}",
        f"**Scan ID:** `{scan_id}`",
        f"**Job ID:** `{job_id}`",
        f"**Report:** [Open report]({report_url}) _(requires AgentBench access)_",
    ]
    if reasons:
        lines.append("")
        lines.append("### Reasons")
        lines.extend(f"- {reason}" for reason in reasons)
    return "\n".join(lines)


def _build_step_summary(payload: dict[str, Any], report_url: str, *, fail_on_warn: bool) -> str:
    verdict = str(payload.get("release_verdict", "unknown")).upper()
    reasons = payload.get("verdict_reasons", []) or []
    policy_line = "Warns fail the job: **yes**" if fail_on_warn else "Warns fail the job: **no**"

    lines = [
        "## AgentBench Gate",
        "",
        f"- Verdict: **{verdict}**",
        f"- Score: **{payload.get('overall_score', 'n/a')}**",
        f"- Grade: **{payload.get('overall_grade', 'n/a')}**",
        f"- Scan: `{payload.get('scan_id', 'n/a')}`",
        f"- Job: `{payload.get('job_id', 'n/a')}`",
        f"- Report: {report_url} (requires AgentBench access)",
        f"- {policy_line}",
    ]
    if reasons:
        lines.extend(["", "### Reasons", *[f"- {reason}" for reason in reasons]])
    return "\n".join(lines)


def _append_multiline_output(lines: list[str], key: str, value: str) -> None:
    lines.append(f"{key}<<{MULTILINE_DELIMITER}")
    lines.append(value)
    lines.append(MULTILINE_DELIMITER)


def _write_github_output(
    payload: dict[str, Any],
    *,
    report_url: str,
    pr_comment_body: str,
    summary_markdown: str,
    fail_on_warn: bool,
    should_fail: bool,
) -> None:
    output_path = os.getenv("GITHUB_OUTPUT")
    if not output_path:
        return
    lines = [
        f"job_id={payload.get('job_id', '')}",
        f"scan_id={payload.get('scan_id', '')}",
        f"release_verdict={payload.get('release_verdict', '')}",
        f"permalink={payload.get('permalink', '')}",
        f"report_url={report_url}",
        f"overall_score={payload.get('overall_score', '')}",
        f"overall_grade={payload.get('overall_grade', '')}",
        f"fail_on_warn={'true' if fail_on_warn else 'false'}",
        f"should_fail={'true' if should_fail else 'false'}",
        f"verdict_reasons_json={json.dumps(payload.get('verdict_reasons', []))}",
    ]
    _append_multiline_output(lines, "pr_comment_body", pr_comment_body)
    _append_multiline_output(lines, "step_summary_markdown", summary_markdown)
    with open(output_path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(lines) + "\n")


def _write_step_summary(summary_markdown: str) -> None:
    summary_path = os.getenv("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as handle:
        handle.write(summary_markdown.rstrip() + "\n")


def main() -> int:
    config = _load_config()
    fail_on_warn = _parse_bool("AGENTBENCH_FAIL_ON_WARN")
    poll_interval_seconds = max(_parse_float("AGENTBENCH_POLL_INTERVAL_SECONDS", default=5.0), 0.0)
    timeout_seconds = max(_parse_float("AGENTBENCH_TIMEOUT_SECONDS", default=900.0), 1.0)

    try:
        created_job = _create_gate_job(config)
    except Exception as exc:  # pragma: no cover - exercised via CLI behavior
        print(f"AgentBench gate request failed: {exc}")
        return 2

    job_id = str(created_job.get("job_id", "")).strip()
    if not job_id:
        print("AgentBench gate response did not include a job_id.")
        return 2

    print(f"AgentBench gate job queued: {job_id}")

    try:
        payload = _poll_gate_job(
            config,
            job_id,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
        )
    except SystemExit:
        raise
    except Exception as exc:  # pragma: no cover - exercised via CLI behavior
        print(f"AgentBench gate polling failed: {exc}")
        return 2

    payload["job_id"] = job_id
    job_status = str(payload.get("status", "unknown")).lower()
    if job_status != "completed":
        print(
            f"AgentBench gate job {job_id} ended with status {job_status.upper()}: "
            f"{payload.get('error_detail') or 'no details provided'}"
        )
        return 2

    verdict = str(payload.get("release_verdict", "unknown")).lower()
    reasons = payload.get("verdict_reasons", []) or []
    report_url = _build_report_url(config["AGENTBENCH_BASE_URL"], payload.get("permalink"))
    if verdict not in {"pass", "warn", "fail"}:
        print(f"Unexpected AgentBench release verdict: {verdict!r}")
        return 2
    should_fail = verdict == "fail" or (verdict == "warn" and fail_on_warn)
    pr_comment_body = _build_pr_comment(payload, report_url)
    summary_markdown = _build_step_summary(payload, report_url, fail_on_warn=fail_on_warn)

    print(
        "AgentBench gate verdict: "
        f"{verdict.upper()} | score={payload.get('overall_score')} "
        f"| grade={payload.get('overall_grade')} | job={job_id}"
    )
    if reasons:
        print("Reasons:")
        for reason in reasons:
            print(f"- {reason}")
    print(f"Report: {report_url}")
    if verdict == "warn" and fail_on_warn:
        print("Warn verdict treated as failure because AGENTBENCH_FAIL_ON_WARN=true")

    _write_github_output(
        payload,
        report_url=report_url,
        pr_comment_body=pr_comment_body,
        summary_markdown=summary_markdown,
        fail_on_warn=fail_on_warn,
        should_fail=should_fail,
    )
    _write_step_summary(summary_markdown)
    return 1 if should_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
