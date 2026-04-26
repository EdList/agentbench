"""Scan-related API routes: submit a scan, retrieve results, list recent scans."""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import threading
import time
import uuid
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

import httpx

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from agentbench.scanner.analyzer import BehaviorAnalyzer
from agentbench.scanner.prober import ALL_CATEGORIES, AgentProber
from agentbench.scanner.scorer import DomainScore, ScanReport, ScoringEngine
from agentbench.scanner.store import ScanStore, ServerScanStore
from agentbench.server.auth import require_auth
from agentbench.server.config import settings
from agentbench.server.models import Project, SavedAgent, ScanJob, ScanPolicy, get_db, get_session_factory, get_engine
from agentbench.server.schemas import (
    DomainScoreResponse,
    PUBLIC_SCAN_CATEGORY_TO_PROBE_CATEGORIES,
    RegressionReportResponse,
    ScanHistoryEntryResponse,
    ScanJobResponse,
    ScanRequest,
    ScanResponse,
    ScanShareResponse,
    ScanSummaryResponse,
)

router = APIRouter(prefix="/scans", tags=["scans"])

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory scan storage — LRU-bounded, thread-safe
# ---------------------------------------------------------------------------

class _LRUScanStore:
    """Thread-safe LRU cache for recent scan responses."""

    def __init__(self, maxsize: int = 1000):
        self._maxsize = max(1, maxsize)
        self._data: OrderedDict[str, dict] = OrderedDict()
        self._lock = threading.Lock()

    def __setitem__(self, key: str, value: dict) -> None:
        with self._lock:
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            while len(self._data) > self._maxsize:
                self._data.popitem(last=False)

    def get(self, key: str, default: object = None) -> object:
        with self._lock:
            entry = self._data.get(key, default)
            if entry is not default and entry is not None:
                self._data.move_to_end(key)
            return entry

    def values(self) -> list[dict]:
        with self._lock:
            return list(self._data.values())

    def clear(self) -> None:
        with self._lock:
            self._data.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._data)

    def keys(self) -> list[str]:
        with self._lock:
            return list(self._data.keys())


_scan_store = _LRUScanStore(maxsize=settings.scan_memory_cap)

# ---------------------------------------------------------------------------
# Scan job execution — bounded thread pool
# ---------------------------------------------------------------------------

_job_executor = ThreadPoolExecutor(
    max_workers=settings.scan_max_workers,
    thread_name_prefix="agentbench-scan",
)

# ---------------------------------------------------------------------------
# Scan persistence store — "local" (SQLite file) or "server" (SQLAlchemy DB)
# ---------------------------------------------------------------------------

if settings.scan_store_mode == "server":
    store = ServerScanStore(engine=get_engine())
else:
    store = ScanStore()


@dataclass(frozen=True)
class PolicySnapshot:
    minimum_overall_score: float | None
    minimum_domain_scores: dict[str, float]
    fail_on_critical_issues: bool
    max_regression_delta: float | None


@dataclass(frozen=True)
class ResolvedScanRequest:
    project_id: str | None
    agent_id: str | None
    policy_id: str | None
    agent_url: str
    categories: list[str] | None
    policy: PolicySnapshot | None


# ---------------------------------------------------------------------------
# SSRF protection
# ---------------------------------------------------------------------------

def _is_safe_ip(ip_str: str) -> bool:
    """Return *True* only for globally routable IP addresses."""
    ip = ipaddress.ip_address(ip_str)
    return ip.is_global


def _validate_agent_url(url: str) -> None:
    """Block private/internal URLs to prevent SSRF attacks."""
    parsed = urlparse(url)

    # Only allow http and https schemes
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid URL scheme '{parsed.scheme}'. Only http and https are allowed.",
        )

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="URL must contain a valid hostname.",
        )

    # Block well-known internal / cloud-metadata hostnames
    blocked_hostnames = {
        "localhost",
        "metadata.google.internal",
        "169.254.169.254",
        "100.100.100.200",
    }
    lower_hostname = hostname.lower()
    if lower_hostname in blocked_hostnames:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Hostname '{hostname}' is not allowed.",
        )

    # Block hostnames ending in dangerous suffixes
    for suffix in (".internal", ".local", ".localhost"):
        if lower_hostname.endswith(suffix) or lower_hostname == suffix.lstrip("."):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Hostname '{hostname}' is not allowed.",
            )

    resolved_hosts: set[str] = set()

    # Direct IP literal
    try:
        resolved_hosts.add(str(ipaddress.ip_address(hostname)))
    except ValueError:
        pass

    # Resolve hostnames and alternative numeric formats
    try:
        for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
            if family not in (socket.AF_INET, socket.AF_INET6):
                continue
            resolved_hosts.add(sockaddr[0])
    except socket.gaierror:
        # If the host does not currently resolve, allow the later HTTP request to fail naturally.
        pass

    for candidate in resolved_hosts:
        if not _is_safe_ip(candidate):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Private/internal IP address '{hostname}' is not allowed.",
            )


# ---------------------------------------------------------------------------
# SSRF protection — request-time DNS rebinding guard (second layer)
# ---------------------------------------------------------------------------

class SafeDNSTransport(httpx.HTTPTransport):
    """Custom httpx transport that re-validates resolved IPs at request time.

    This closes the DNS-rebinding window: even if an attacker's DNS server
    returns a safe IP during ``_validate_agent_url`` and then flips the record
    to an internal IP before httpx connects, we catch it here.
    """

    def handle_request(self, request: httpx.Request) -> httpx.Response:  # type: ignore[override]
        hostname = request.url.host.decode("ascii") if isinstance(request.url.host, bytes) else request.url.host
        if hostname:
            resolved: set[str] = set()
            try:
                for family, _, _, _, sockaddr in socket.getaddrinfo(hostname, None):
                    if family in (socket.AF_INET, socket.AF_INET6):
                        resolved.add(sockaddr[0])
            except socket.gaierror:
                pass
            # Also handle IP literals
            try:
                resolved.add(str(ipaddress.ip_address(hostname)))
            except ValueError:
                pass
            for ip_str in resolved:
                if not _is_safe_ip(ip_str):
                    raise httpx.ConnectError(
                        f"SSRF protection: resolved IP {ip_str} for host '{hostname}' "
                        "is private/internal. Request blocked."
                    )
        return super().handle_request(request)


def _expand_scan_categories(categories: list[str] | None) -> list[str]:
    """Map public evaluation domains onto the internal probe categories."""
    if categories is None:
        return list(ALL_CATEGORIES)

    expanded: list[str] = []
    for category in categories:
        for probe_category in PUBLIC_SCAN_CATEGORY_TO_PROBE_CATEGORIES[category]:
            if probe_category not in expanded:
                expanded.append(probe_category)
    return expanded


def _get_project_or_404(db: Session, project_id: str, principal: str) -> Project:
    project = (
        db.query(Project)
        .filter(Project.id == project_id, Project.principal == principal)
        .first()
    )
    if project is None:
        raise HTTPException(status_code=404, detail=f"Project {project_id!r} not found.")
    return project


def _get_saved_agent_or_404(db: Session, agent_id: str, principal: str) -> SavedAgent:
    agent = (
        db.query(SavedAgent)
        .filter(SavedAgent.id == agent_id, SavedAgent.principal == principal)
        .first()
    )
    if agent is None:
        raise HTTPException(status_code=404, detail=f"Saved agent {agent_id!r} not found.")
    return agent


def _get_scan_policy_or_404(db: Session, policy_id: str, principal: str) -> ScanPolicy:
    policy = (
        db.query(ScanPolicy)
        .filter(ScanPolicy.id == policy_id, ScanPolicy.principal == principal)
        .first()
    )
    if policy is None:
        raise HTTPException(status_code=404, detail=f"Scan policy {policy_id!r} not found.")
    return policy


def _evaluate_release_verdict(
    report: ScanResponse,
    *,
    agent_url: str,
    policy: PolicySnapshot | None,
    principal: str,
) -> tuple[str | None, list[str]]:
    if policy is None:
        return None, []

    reasons: list[str] = []
    if (
        policy.minimum_overall_score is not None
        and report.overall_score < policy.minimum_overall_score
    ):
        reasons.append(
            "Overall score "
            f"{round(report.overall_score, 1)} is below the required {round(policy.minimum_overall_score, 1)}."
        )

    for domain in report.domain_scores:
        threshold = policy.minimum_domain_scores.get(domain.name)
        if threshold is not None and domain.score < threshold:
            reasons.append(
                f"{domain.name} score {round(domain.score, 1)} is below the required {round(threshold, 1)}."
            )

    if policy.fail_on_critical_issues and report.critical_issues:
        reasons.append(
            f"Critical issues present ({len(report.critical_issues)}), which fails this policy."
        )

    if policy.max_regression_delta is not None:
        previous_scans = store.list_scans(agent_url=agent_url, principal=principal, limit=1)
        if previous_scans:
            previous_score = previous_scans[0]["overall_score"]
            delta = report.overall_score - previous_score
            if delta < policy.max_regression_delta:
                reasons.append(
                    "Regression delta "
                    f"{round(delta, 1)} is worse than the allowed {round(policy.max_regression_delta, 1)}."
                )

    return ("fail" if reasons else "pass"), reasons


def _coerce_scan_report(report_response: ScanResponse, score_report: object | None) -> ScanReport:
    """Normalize scan results into a persistable ScanReport."""
    if isinstance(score_report, ScanReport):
        return score_report

    timestamp = datetime.now(UTC)
    if report_response.timestamp:
        try:
            timestamp = datetime.fromisoformat(report_response.timestamp)
        except ValueError:
            timestamp = datetime.now(UTC)

    return ScanReport(
        overall_score=report_response.overall_score,
        overall_grade=report_response.overall_grade,
        domain_scores=[
            DomainScore(
                name=domain.name,
                score=domain.score,
                grade=domain.grade,
                findings=list(domain.findings),
                recommendations=list(domain.recommendations),
            )
            for domain in report_response.domain_scores
        ],
        summary=report_response.summary,
        behaviors_tested=report_response.behaviors_tested,
        behaviors_passed=report_response.behaviors_passed,
        behaviors_failed=report_response.behaviors_failed,
        critical_issues=list(report_response.critical_issues),
        timestamp=timestamp,
    )


def _scan_response_from_persisted(scan_row: dict | None) -> ScanResponse | None:
    """Hydrate a persisted scan row back into the public API response."""
    if scan_row is None:
        return None

    payload = json.loads(scan_row["report_json"])
    domains = payload.get("domains", [])
    timestamp = payload.get("timestamp", scan_row.get("created_at", datetime.now(UTC).isoformat()))

    return ScanResponse(
        scan_id=scan_row["id"],
        project_id=payload.get("project_id"),
        agent_id=payload.get("agent_id"),
        policy_id=payload.get("policy_id"),
        release_verdict=payload.get("release_verdict"),
        verdict_reasons=payload.get("verdict_reasons", []),
        overall_score=payload.get("overall_score", scan_row["overall_score"]),
        overall_grade=payload.get("grade", scan_row["grade"]),
        domain_scores=[
            DomainScoreResponse(
                name=domain["name"],
                score=domain["score"],
                grade=domain["grade"],
                findings=domain.get("findings", []),
                recommendations=domain.get("recommendations", []),
            )
            for domain in domains
        ],
        summary=payload.get("summary", ""),
        behaviors_tested=payload.get("behaviors_tested", 0),
        behaviors_passed=payload.get("behaviors_passed", 0),
        behaviors_failed=payload.get("behaviors_failed", 0),
        critical_issues=payload.get("critical_issues", []),
        timestamp=timestamp,
    )


def _scan_summary_from_persisted(scan_row: dict) -> ScanSummaryResponse:
    """Map a persisted scan row to the list endpoint schema."""
    return ScanSummaryResponse(
        scan_id=scan_row["id"],
        agent_url=scan_row["agent_url"],
        overall_score=scan_row["overall_score"],
        overall_grade=scan_row["grade"],
        timestamp=scan_row["created_at"],
    )


def _resolve_scan_record(
    scan_id: str,
    principal: str | None = None,
) -> tuple[str, ScanResponse] | None:
    """Return (agent_url, report) from memory or persistence for a scan ID."""
    entry = _scan_store.get(scan_id)
    if entry is not None and (principal is None or entry.get("principal") == principal):
        return entry["agent_url"], entry["report"]

    persisted = store.get_scan(scan_id, principal=principal)
    response = _scan_response_from_persisted(persisted)
    if persisted is None or response is None:
        return None
    return persisted["agent_url"], response


def _build_share_payload(scan_id: str, agent_url: str, report: ScanResponse) -> ScanShareResponse:
    """Build share-friendly text blocks and a permalink for a scan."""
    permalink = f"/?scan_id={scan_id}"
    title = f"AgentBench report — {report.overall_grade} ({round(report.overall_score)}/100)"
    domain_lines = "\n".join(
        f"- {domain.name}: {domain.grade} ({round(domain.score)}/100)"
        for domain in report.domain_scores
    )
    critical_issues = "\n".join(f"- {issue}" for issue in report.critical_issues) or "- No critical issues found."
    markdown = (
        f"# {title}\n\n"
        f"Scan ID: {scan_id}\n"
        f"Permalink: {permalink}\n\n"
        f"Overall: {report.overall_grade} ({round(report.overall_score)}/100)\n"
        f"Release verdict: {(report.release_verdict or 'not-set').upper()}\n"
        f"Behaviors: {report.behaviors_passed}/{report.behaviors_tested} passed\n\n"
        f"## Domain scores\n{domain_lines}\n\n"
        f"## Critical issues\n{critical_issues}\n\n"
        f"## Summary\n{report.summary}"
    )
    slack_text = (
        f"{title}\n"
        f"Scan ID: {scan_id}\n"
        f"Permalink: {permalink}\n"
        f"Overall: {report.overall_grade} ({round(report.overall_score)}/100)\n"
        f"Release verdict: {(report.release_verdict or 'not-set').upper()}\n"
        f"Behaviors: {report.behaviors_passed}/{report.behaviors_tested} passed\n"
        f"Domain scores:\n{domain_lines}\n"
        f"Critical issues:\n{critical_issues}\n"
        f"Summary: {report.summary}"
    )
    return ScanShareResponse(
        scan_id=scan_id,
        agent_url=agent_url,
        permalink=permalink,
        title=title,
        markdown=markdown,
        slack_text=slack_text,
    )


def _policy_snapshot_from_model(policy: ScanPolicy | None) -> PolicySnapshot | None:
    if policy is None:
        return None
    return PolicySnapshot(
        minimum_overall_score=policy.minimum_overall_score,
        minimum_domain_scores=json.loads(policy.minimum_domain_scores_json),
        fail_on_critical_issues=bool(policy.fail_on_critical_issues),
        max_regression_delta=policy.max_regression_delta,
    )


def _resolve_scan_request(body: ScanRequest, principal: str, db: Session) -> ResolvedScanRequest:
    project = _get_project_or_404(db, body.project_id, principal) if body.project_id else None
    saved_agent = _get_saved_agent_or_404(db, body.agent_id, principal) if body.agent_id else None
    policy = _get_scan_policy_or_404(db, body.policy_id, principal) if body.policy_id else None

    if saved_agent is not None:
        if project is not None and saved_agent.project_id != project.id:
            raise HTTPException(status_code=404, detail="Saved agent does not belong to this project.")
        if project is None:
            project = _get_project_or_404(db, saved_agent.project_id, principal)

    if policy is not None:
        if project is not None and policy.project_id != project.id:
            raise HTTPException(status_code=404, detail="Scan policy does not belong to this project.")
        if project is None:
            project = _get_project_or_404(db, policy.project_id, principal)

    agent_url = saved_agent.agent_url if saved_agent is not None else body.agent_url
    categories = body.categories
    if categories is None and policy is not None and policy.categories_json:
        categories = json.loads(policy.categories_json)

    _validate_agent_url(agent_url)
    return ResolvedScanRequest(
        project_id=project.id if project is not None else None,
        agent_id=saved_agent.id if saved_agent is not None else None,
        policy_id=policy.id if policy is not None else None,
        agent_url=agent_url,
        categories=categories,
        policy=_policy_snapshot_from_model(policy),
    )


def _persist_scan_response(
    scan_id: str,
    principal: str,
    resolved: ResolvedScanRequest,
    report_response: ScanResponse,
    score_report: object | None,
) -> None:
    # Write to persistent store FIRST — if this fails, don't cache stale data
    persistable_report = _coerce_scan_report(report_response, score_report)
    store.save_scan(
        scan_id,
        resolved.agent_url,
        persistable_report,
        principal=principal,
        metadata={
            "project_id": report_response.project_id,
            "agent_id": report_response.agent_id,
            "policy_id": report_response.policy_id,
            "release_verdict": report_response.release_verdict,
            "verdict_reasons": report_response.verdict_reasons,
        },
    )

    # Cache in-memory only after successful persistence
    _scan_store[scan_id] = {
        "scan_id": scan_id,
        "principal": principal,
        "agent_url": resolved.agent_url,
        "report": report_response,
        "timestamp": datetime.now(UTC).isoformat(),
    }


def _execute_resolved_scan(
    resolved: ResolvedScanRequest,
    principal: str,
) -> tuple[ScanResponse, object | None]:
    scan_id = str(uuid.uuid4())
    try:
        result = _run_scan(resolved.agent_url, resolved.categories)
        if isinstance(result, tuple):
            report_response, score_report = result
        else:
            report_response = result
            score_report = None
        release_verdict, verdict_reasons = _evaluate_release_verdict(
            report_response,
            agent_url=resolved.agent_url,
            policy=resolved.policy,
            principal=principal,
        )
        report_response = report_response.model_copy(
            update={
                "scan_id": scan_id,
                "project_id": resolved.project_id,
                "agent_id": resolved.agent_id,
                "policy_id": resolved.policy_id,
                "release_verdict": release_verdict,
                "verdict_reasons": verdict_reasons,
            }
        )
        return report_response, score_report
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Failed to scan agent at {resolved.agent_url}: {exc}",
        ) from exc


def _run_resolved_scan(resolved: ResolvedScanRequest, principal: str) -> ScanResponse:
    report_response, score_report = _execute_resolved_scan(resolved, principal)
    _persist_scan_response(report_response.scan_id or str(uuid.uuid4()), principal, resolved, report_response, score_report)
    return report_response


def _scan_job_to_response(job: ScanJob) -> ScanJobResponse:
    return ScanJobResponse(
        job_id=job.id,
        status=job.status,
        cancel_requested=bool(job.cancel_requested),
        project_id=job.project_id,
        agent_id=job.agent_id,
        policy_id=job.policy_id,
        agent_url=job.agent_url,
        scan_id=job.scan_id,
        release_verdict=job.release_verdict,
        verdict_reasons=json.loads(job.verdict_reasons_json or "[]"),
        overall_score=job.overall_score,
        overall_grade=job.overall_grade,
        permalink=f"/?scan_id={job.scan_id}" if job.scan_id else None,
        error_detail=job.error_detail,
        created_at=job.created_at,
        started_at=job.started_at,
        completed_at=job.completed_at,
    )


def _get_scan_job_or_404(db: Session, job_id: str, principal: str) -> ScanJob:
    job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
    if job is None:
        raise HTTPException(status_code=404, detail=f"Scan job {job_id!r} not found.")
    return job


def _run_scan_job_worker(job_id: str, principal: str, resolved: ResolvedScanRequest) -> None:
    factory = get_session_factory()
    db = factory()
    deadline = time.monotonic() + settings.scan_timeout_seconds
    try:
        job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
        if job is None:
            return
        if bool(job.cancel_requested):
            job.status = 'cancelled'
            job.completed_at = datetime.now(UTC)
            db.commit()
            return

        job.status = 'running'
        job.started_at = datetime.now(UTC)
        db.commit()

        # Check timeout and cancellation before running
        if time.monotonic() > deadline:
            job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
            if job is not None:
                job.status = 'failed'
                job.error_detail = 'Scan timed out before execution started (queue backlog).'
                job.completed_at = datetime.now(UTC)
                db.commit()
            return

        try:
            report, score_report = _execute_resolved_scan(resolved, principal)
        except HTTPException as exc:
            job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
            if job is None:
                return
            job.status = 'failed'
            job.error_detail = str(exc.detail)
            job.completed_at = datetime.now(UTC)
            db.commit()
            return
        except Exception as exc:
            # Generic catch-all — prevents stuck 'running' jobs
            logger.exception("Unhandled exception in scan job %s", job_id)
            job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
            if job is not None:
                job.status = 'failed'
                job.error_detail = f"Internal error: {exc}"
                job.completed_at = datetime.now(UTC)
                db.commit()
            return

        # Check timeout after scan completes
        if time.monotonic() > deadline:
            job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
            if job is not None:
                job.status = 'failed'
                job.error_detail = f'Scan exceeded maximum execution time ({settings.scan_timeout_seconds}s).'
                job.completed_at = datetime.now(UTC)
                db.commit()
            return

        # Check DB-polling cancellation
        job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
        if job is None:
            return
        if bool(job.cancel_requested):
            job.status = 'cancelled'
            job.error_detail = 'Scan job was cancelled before completion was recorded.'
            job.completed_at = datetime.now(UTC)
            db.commit()
            return

        _persist_scan_response(report.scan_id or str(uuid.uuid4()), principal, resolved, report, score_report)
        job.status = 'completed'
        job.scan_id = report.scan_id
        job.release_verdict = report.release_verdict
        job.verdict_reasons_json = json.dumps(report.verdict_reasons)
        job.overall_score = report.overall_score
        job.overall_grade = report.overall_grade
        # Persist full report data into the ScanJob row for server-backed queries
        if isinstance(store, ServerScanStore):
            persistable = _coerce_scan_report(report, score_report)
            job.report_json = json.dumps(store._report_to_dict(persistable, metadata={
                "project_id": report.project_id,
                "agent_id": report.agent_id,
                "policy_id": report.policy_id,
                "release_verdict": report.release_verdict,
                "verdict_reasons": report.verdict_reasons,
            }))
            job.domain_scores_json = json.dumps([
                {"name": d.name, "score": d.score, "grade": d.grade}
                for d in report.domain_scores
            ])
        job.completed_at = datetime.now(UTC)
        db.commit()
    except Exception:
        # Absolute last-resort catch — ensure job never stays in 'running'
        logger.exception("Fatal error in scan job worker for %s", job_id)
        try:
            job = db.query(ScanJob).filter(ScanJob.id == job_id, ScanJob.principal == principal).first()
            if job is not None and job.status in ('queued', 'running'):
                job.status = 'failed'
                job.error_detail = 'Worker crashed unexpectedly.'
                job.completed_at = datetime.now(UTC)
                db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def _create_scan_job(resolved: ResolvedScanRequest, principal: str, db: Session) -> ScanJob:
    job = ScanJob(
        principal=principal,
        status='queued',
        agent_url=resolved.agent_url,
        project_id=resolved.project_id,
        agent_id=resolved.agent_id,
        policy_id=resolved.policy_id,
        categories_json=json.dumps(resolved.categories) if resolved.categories is not None else None,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    try:
        _job_executor.submit(_run_scan_job_worker, job.id, principal, resolved)
    except RuntimeError:
        # Executor is shut down or full — mark job as failed immediately
        job.status = 'failed'
        job.error_detail = 'Scan queue is full. Please retry later.'
        job.completed_at = datetime.now(UTC)
        db.commit()
    return job


def fail_stale_scan_jobs() -> None:
    """Mark orphaned queued/running jobs as failed after a process restart."""
    factory = get_session_factory()
    db = factory()
    try:
        rows = db.query(ScanJob).filter(ScanJob.status.in_(['queued', 'running'])).all()
        now = datetime.now(UTC)
        for job in rows:
            job.status = 'failed'
            job.error_detail = 'Scan job was interrupted by a server restart before completion.'
            job.completed_at = now
        if rows:
            db.commit()
    except SQLAlchemyError:
        db.rollback()
    finally:
        db.close()


def _list_recent_scans(
    limit: int,
    offset: int,
    principal: str | None = None,
) -> list[ScanSummaryResponse]:
    """List scans, preferring persisted rows and falling back to in-memory cache."""
    persisted_rows = store.list_scans(principal=principal, limit=limit, offset=offset)
    if persisted_rows:
        return [_scan_summary_from_persisted(row) for row in persisted_rows]

    entries = _scan_store.values()
    sorted_entries = sorted(
        [
            entry
            for entry in entries
            if principal is None or entry.get("principal") == principal
        ],
        key=lambda e: e.get("timestamp", ""),
        reverse=True,
    )
    page = sorted_entries[offset : offset + limit]
    return [
        ScanSummaryResponse(
            scan_id=e["scan_id"],
            agent_url=e["agent_url"],
            overall_score=e["report"].overall_score,
            overall_grade=e["report"].overall_grade,
            timestamp=e["timestamp"],
        )
        for e in page
    ]


def _run_scan(agent_url: str, categories: list[str] | None) -> tuple[ScanResponse, object]:
    """Execute the full prober → analyzer → scorer pipeline synchronously.

    Returns (ScanResponse, ScanReport) so callers can persist the rich report.
    """
    cats = _expand_scan_categories(categories)

    # Wrap the agent URL in a simple callable for the prober
    def _agent_fn(prompt: str) -> str:
        """Send *prompt* to the agent via HTTP and return the response text."""
        transport = SafeDNSTransport()
        with httpx.Client(timeout=30.0, transport=transport) as client:
            resp = client.post(
                agent_url,
                json={"prompt": prompt},
            )
            resp.raise_for_status()
            data = resp.json()
            # Support common response shapes
            if isinstance(data, dict):
                return data.get("response", data.get("output", str(data)))
            return str(data)

    # 1. Probe
    prober = AgentProber(agent_fn=_agent_fn, categories=cats)
    session = prober.probe_all()

    # 2. Analyze
    analyzer = BehaviorAnalyzer(use_llm=settings.scanner_use_llm)
    behaviors = analyzer.analyze(session)

    # 3. Score
    engine = ScoringEngine()
    report = engine.score(behaviors)

    # 4. Convert to response
    response = ScanResponse(
        overall_score=report.overall_score,
        overall_grade=report.overall_grade,
        domain_scores=[
            DomainScoreResponse(
                name=ds.name,
                score=ds.score,
                grade=ds.grade,
                findings=ds.findings,
                recommendations=ds.recommendations,
            )
            for ds in report.domain_scores
        ],
        summary=report.summary,
        behaviors_tested=report.behaviors_tested,
        behaviors_passed=report.behaviors_passed,
        behaviors_failed=report.behaviors_failed,
        critical_issues=report.critical_issues,
        timestamp=report.timestamp.isoformat(),
    )
    return response, report


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post(
    "",
    response_model=ScanResponse,
    status_code=status.HTTP_200_OK,
)
def submit_scan(
    body: ScanRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanResponse:
    """Scan an agent and return the full report immediately.

    The scan runs synchronously (38 probes, no LLM) and typically completes
    in under a minute.
    """
    resolved = _resolve_scan_request(body, principal, db)
    return _run_resolved_scan(resolved, principal)


@router.post(
    "/jobs",
    response_model=ScanJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_scan_job(
    body: ScanRequest,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanJobResponse:
    """Create an async scan job and return immediately with a pollable job id."""
    resolved = _resolve_scan_request(body, principal, db)
    job = _create_scan_job(resolved, principal, db)
    return _scan_job_to_response(job)


@router.get(
    "/jobs/{job_id}",
    response_model=ScanJobResponse,
)
def get_scan_job(
    job_id: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanJobResponse:
    """Return the latest state for a scan job."""
    job = _get_scan_job_or_404(db, job_id, principal)
    return _scan_job_to_response(job)


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=ScanJobResponse,
)
def cancel_scan_job(
    job_id: str,
    principal: str = Depends(require_auth),
    db: Session = Depends(get_db),
) -> ScanJobResponse:
    """Request cancellation for a running or queued scan job."""
    job = _get_scan_job_or_404(db, job_id, principal)
    job.cancel_requested = 1
    if job.status == "queued":
        job.status = "cancelled"
        job.error_detail = "Scan job was cancelled before execution started."
        job.completed_at = datetime.now(UTC)
    db.commit()
    db.refresh(job)
    return _scan_job_to_response(job)


@router.get(
    "",
    response_model=list[ScanSummaryResponse],
)
def list_scans(
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    principal: str = Depends(require_auth),
) -> list[ScanSummaryResponse]:
    """List recent scans, ordered by time descending."""
    return _list_recent_scans(limit=limit, offset=offset, principal=principal)


# ---------------------------------------------------------------------------
# Persistence / Regression endpoints (must come before /{scan_id})
# ---------------------------------------------------------------------------


@router.get(
    "/history/{agent_url:path}",
    response_model=list[ScanHistoryEntryResponse],
)
def scan_history(
    agent_url: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    principal: str = Depends(require_auth),
) -> list[ScanHistoryEntryResponse]:
    """List persisted scan history for a specific agent URL."""
    return store.list_scans(agent_url=agent_url, principal=principal, limit=limit, offset=offset)


@router.get(
    "/regression/{agent_url:path}",
    response_model=RegressionReportResponse,
)
def regression_report(
    agent_url: str,
    principal: str = Depends(require_auth),
) -> RegressionReportResponse:
    """Get regression report comparing the two latest scans for an agent."""
    report = store.get_regression_report(agent_url, principal=principal)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail="Not enough scan history to compute regression.",
        )
    return report


@router.get(
    "/{scan_id}/share",
    response_model=ScanShareResponse,
)
def get_scan_share(
    scan_id: str,
    principal: str = Depends(require_auth),
) -> ScanShareResponse:
    """Build share-friendly content and a permalink for a specific scan."""
    resolved = _resolve_scan_record(scan_id, principal=principal)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found.")
    agent_url, report = resolved
    return _build_share_payload(scan_id, agent_url, report)


@router.get(
    "/{scan_id}",
    response_model=ScanResponse,
)
def get_scan(
    scan_id: str,
    principal: str = Depends(require_auth),
) -> ScanResponse:
    """Retrieve a previously-run scan report by ID."""
    resolved = _resolve_scan_record(scan_id, principal=principal)
    if resolved is not None:
        _, report = resolved
        return report

    raise HTTPException(status_code=404, detail=f"Scan {scan_id!r} not found.")
