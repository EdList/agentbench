"""CLI — Paste your agent URL. We'll tell you what's broken."""

from __future__ import annotations

import asyncio
import json
import logging
from urllib.parse import urlparse

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from agentbench import __version__
from agentbench.probes.base import Domain, ScanResult, Severity
from agentbench.probes.registry import get_probe_counts
from agentbench.scanner.runner import run_scan

app = typer.Typer(
    name="agentbench",
    help="Paste your agent URL. We'll tell you what's broken.",
    no_args_is_help=True,
)
console = Console()
logger = logging.getLogger(__name__)


def _version(value: bool) -> None:
    if value:
        console.print(f"agentbench {__version__}")
        raise typer.Exit


@app.callback()
def main(
    version: bool = typer.Option(
        None,
        "--version",
        "-v",
        callback=_version,
        is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """AgentBench — Behavioral CI for AI agents."""


def _validate_url(
    url: str,
    *,
    api_key: str | None = None,
    allow_insecure_http: bool = False,
) -> None:
    """Validate URL has http/https scheme, non-empty host, and safe API key transport."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        console.print(f"[red]Error:[/red] URL must use http or https scheme. Got: {url}")
        raise typer.Exit(code=1)
    if not parsed.netloc:
        console.print(f"[red]Error:[/red] URL must have a valid host. Got: {url}")
        raise typer.Exit(code=1)
    if parsed.username is not None or parsed.password is not None:
        console.print(
            "[red]Error:[/red] URL must not include embedded credentials. "
            "Use --api-key or AGENTBENCH_API_KEY instead."
        )
        raise typer.Exit(code=1)
    if parsed.scheme == "http" and api_key and not allow_insecure_http:
        console.print(
            "[red]Error:[/red] Refusing to send an API key over insecure HTTP. "
            "Use HTTPS, or pass --allow-insecure-http for trusted local development only."
        )
        raise typer.Exit(code=1)


@app.command()
def scan(
    url: str = typer.Argument(..., help="Agent endpoint URL to scan."),
    api_key: str | None = typer.Option(
        None,
        "--api-key",
        "-k",
        envvar="AGENTBENCH_API_KEY",
        help="API key for the agent endpoint.",
    ),
    model: str | None = typer.Option(
        None,
        "--model",
        "-m",
        envvar="AGENTBENCH_MODEL",
        help="Model name (required by some endpoints like OpenRouter).",
    ),
    output: str | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Save results as JSON to this file.",
    ),
    domain: list[str] | None = typer.Option(
        None,
        "--domain",
        "-d",
        help="Restrict scan to specific domains (safety, reliability, capability, consistency).",
    ),
    timeout: float = typer.Option(
        30.0,
        "--timeout",
        "-t",
        help="Per-request timeout in seconds.",
    ),
    allow_insecure_http: bool = typer.Option(
        False,
        "--allow-insecure-http",
        help="Allow sending an API key over HTTP (trusted local development only).",
    ),
) -> None:
    """Scan an agent endpoint for behavioral issues."""
    if timeout <= 0:
        console.print(f"[red]Error:[/red] Timeout must be positive, got {timeout}")
        raise typer.Exit(code=1)

    _validate_url(url, api_key=api_key, allow_insecure_http=allow_insecure_http)

    # Validate domain names
    _valid_domains = {d.value for d in Domain}
    if domain:
        for d in domain:
            if d not in _valid_domains:
                valid = ", ".join(sorted(_valid_domains))
                console.print(
                    f"[red]Error:[/red] Invalid domain '{d}'. "
                    f"Must be one of: {valid}"
                )
                raise typer.Exit(code=1)

    # Show header — compute actual probe count (respects domain filtering)
    from agentbench.probes.registry import get_all_probes

    counts = get_probe_counts()
    if domain:
        filtered_domains = {Domain(d) for d in domain}
        scan_probes = [p for p in get_all_probes() if p.domain in filtered_domains]
        total_probes = len(scan_probes)
        domain_count = len(filtered_domains)
    else:
        total_probes = sum(counts.values())
        domain_count = len(counts)
    console.print()
    console.print(
        Panel(
            f"[bold]Scanning:[/] {url}\n"
            f"[dim]{total_probes} probes across {domain_count} domains[/]",
            title="🔍 AgentBench Scanner",
            border_style="blue",
        )
    )

    # Run the scan with live progress updates
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Running {total_probes} probes...", total=total_probes)

        async def _on_progress(completed: int, _total: int) -> None:
            progress.update(task, completed=completed)

        result = asyncio.run(
            run_scan(
                url,
                api_key=api_key,
                model=model,
                domains=domain,
                timeout=timeout,
                progress_callback=_on_progress,
            )
        )
        progress.update(task, completed=total_probes)

    # Render results
    _render_scorecard(result)

    # Save to leaderboard and optionally write JSON output
    _write_output(result, output)

    console.print()

    # Exit code: 1 if any critical findings
    critical = sum(1 for f in result.findings if f.severity == Severity.CRITICAL)
    if critical > 0:
        raise typer.Exit(code=1)


def _write_output(result: ScanResult, output: str | None) -> None:
    """Save scan result to leaderboard and optionally write JSON to *output*."""
    # Save to leaderboard (protected — won't crash scan on failure)
    try:
        from agentbench.leaderboard import add_scan_result

        add_scan_result(result, label=result.url)
        console.print("[dim]Result added to leaderboard.[/dim]")
    except (OSError, TypeError, ValueError):
        # Expected: filesystem errors (OSError), malformed result objects
        # (TypeError), or invalid leaderboard data (ValueError).
        logger.debug("leaderboard save failed", exc_info=True)
        console.print("[dim yellow]Could not save to leaderboard.[/dim yellow]")

    # Save output if requested
    # NOTE: The output path is user-controlled (CLI argument) and is not
    # sanitized against path traversal.  This is acceptable because the user
    # runs the CLI themselves and implicitly trusts their own arguments.
    if output:
        try:
            import os
            import tempfile

            output_dir = os.path.dirname(os.path.abspath(output))
            # Write to a temp file first, then atomically rename to the final
            # path.  This prevents partial/corrupt output if the process is
            # interrupted mid-write.
            tmp_fd, tmp_path = tempfile.mkstemp(
                dir=output_dir, suffix=".tmp",
            )
            try:
                with os.fdopen(tmp_fd, "w") as f:
                    json.dump(result.to_dict(), f, indent=2)
                os.replace(tmp_path, output)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
                raise
            console.print(f"\n[dim]Results saved to {output}[/dim]")
        except OSError as exc:
            console.print(f"\n[red]Error saving to {output}: {exc}[/red]")
            raise typer.Exit(code=1) from exc


def _render_scorecard(result: ScanResult) -> None:
    """Render a Rich terminal scorecard."""
    console.print()

    # Overall grade
    grade = result.grade
    grade_color = {
        "A": "green",
        "B": "green",
        "C": "yellow",
        "D": "red",
        "F": "bold red",
    }
    color = grade_color.get(grade, "white")

    console.print(
        f"  Overall Score: [{color}]{result.overall_score}[/{color}]/100 "
        f"(Grade: [{color}]{grade}[/{color}])"
    )
    console.print(
        f"  Probes: {result.probes_run} | "
        f"Duration: {result.duration_seconds:.1f}s | "
        f"Findings: {len(result.findings)}"
    )
    console.print()

    # Domain scores table
    table = Table(title="Domain Scores", show_header=True, header_style="bold")
    table.add_column("Domain", style="cyan")
    table.add_column("Score", justify="right")
    table.add_column("Grade", justify="center")
    table.add_column("Passed", justify="right", style="green")
    table.add_column("Failed", justify="right", style="red")
    table.add_column("Total", justify="right", style="dim")

    for name in ["safety", "reliability", "capability", "consistency"]:
        ds = result.domain_scores.get(name)
        if ds is not None:
            sc = "green" if ds.score >= 80 else "yellow" if ds.score >= 60 else "red"
            table.add_row(
                name.title(),
                f"[{sc}]{ds.score}[/{sc}]",
                f"[{sc}]{ds.grade}[/{sc}]",
                str(ds.passed),
                str(ds.failed) if ds.failed > 0 else "0",
                str(ds.total),
            )

    console.print(table)

    # Findings
    if result.findings:
        console.print()
        crit = [f for f in result.findings if f.severity == Severity.CRITICAL]
        warn = [f for f in result.findings if f.severity == Severity.WARNING]
        info = [f for f in result.findings if f.severity == Severity.INFO]

        if crit:
            console.print(f"  [bold red]❌ Critical ({len(crit)})[/bold red]")
            for f in crit:
                console.print(f"    • {f.title}")
                console.print(f"      [dim]{f.detail[:120]}[/dim]")
                if f.remediation:
                    console.print(f"      [green]↳ Fix: {f.remediation[:100]}[/green]")

        if warn:
            console.print(f"  [yellow]⚠️  Warnings ({len(warn)})[/yellow]")
            for f in warn:
                console.print(f"    • {f.title}")
                console.print(f"      [dim]{f.detail[:120]}[/dim]")
                if f.remediation:
                    console.print(f"      [green]↳ Fix: {f.remediation[:100]}[/green]")

        if info:
            console.print(f"  [dim]ℹ️  Info ({len(info)})[/dim]")
            for f in info[:5]:
                console.print(f"    [dim]• {f.title}[/dim]")
            if len(info) > 5:
                console.print(f"    [dim]... and {len(info) - 5} more[/dim]")
    else:
        console.print("\n  [bold green]✅ No issues found. Your agent looks solid.[/bold green]")


@app.command()
def probes() -> None:
    """List all available probes."""
    all_probes = get_probe_counts()
    console.print(f"\n[bold]AgentBench Probes[/bold] ({sum(all_probes.values())} total)\n")

    table = Table(show_header=True, header_style="bold")
    table.add_column("Domain", style="cyan")
    table.add_column("Count", justify="right")

    for domain_name in ["safety", "reliability", "capability", "consistency"]:
        count = all_probes.get(domain_name, 0)
        table.add_row(domain_name.title(), str(count))

    console.print(table)
    console.print()


@app.command()
def compare(
    url: str | None = typer.Argument(None, help="Agent URL to compare."),
    label: str | None = typer.Option(None, "--label", "-l", help="Label to filter by."),
    last: int = typer.Option(10, "--last", "-n", help="Show last N entries."),
) -> None:
    """Compare scan results over time."""
    from agentbench.leaderboard import compare_results, get_recent

    if last < 1:
        console.print(f"[red]Error:[/red] --last must be at least 1, got {last}")
        raise typer.Exit(code=1)

    if url or label:
        entries = compare_results(url=url, label=label)
    else:
        entries = get_recent(last)

    if not entries:
        console.print("\n[yellow]No scan results found.[/yellow] Run a scan first.\n")
        return

    console.print()
    table = Table(title="Scan History", show_header=True, header_style="bold")
    table.add_column("Timestamp", style="dim")
    table.add_column("Label")
    table.add_column("Score", justify="right")
    table.add_column("Grade", justify="center")
    table.add_column("Critical", justify="right", style="red")
    table.add_column("Warning", justify="right", style="yellow")

    for entry in entries:
        score = entry.get("overall_score", 0)
        grade = entry.get("grade", "?")
        sc = (
            "green"
            if score >= 80
            else "yellow"
            if score >= 60
            else "red"
        )
        table.add_row(
            entry.get("timestamp", "")[:19],
            entry.get("label", entry.get("url", "")),
            f"[{sc}]{score}[/{sc}]",
            f"[{sc}]{grade}[/{sc}]",
            str(entry.get("critical_count", 0)),
            str(entry.get("warning_count", 0)),
        )

    console.print(table)
    console.print()


@app.command()
def update() -> None:
    """Check for and pull latest probe definitions."""
    from agentbench.updater import check_for_updates, pull_updates

    console.print("\n[bold]Checking for probe updates...[/bold]\n")

    available = check_for_updates()
    if not available:
        console.print("[green]✅ All probes are up to date.[/green]\n")
        return

    console.print(f"Updates available for: {', '.join(available)}")
    updated = pull_updates(available)

    if updated:
        for f in updated:
            console.print(f"  [green]✅ Updated {f}[/green]")
        console.print(f"\n[bold]{len(updated)} probe file(s) updated.[/bold]\n")
    else:
        console.print("[yellow]Failed to download updates.[/yellow]\n")
