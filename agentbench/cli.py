"""CLI — Paste your agent URL. We'll tell you what's broken."""

from __future__ import annotations

import asyncio
import json

import typer
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from agentbench import __version__
from agentbench.probes.base import Severity
from agentbench.probes.registry import get_probe_counts
from agentbench.scanner.runner import run_scan

app = typer.Typer(
    name="agentbench",
    help="Paste your agent URL. We'll tell you what's broken.",
    no_args_is_help=True,
)
console = Console()


def _version(value: bool) -> None:
    if value:
        console.print(f"agentbench {__version__}")
        raise typer.Exit()


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
    pass


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
) -> None:
    """Scan an agent endpoint for behavioral issues."""
    # Show header
    counts = get_probe_counts()
    total_probes = sum(counts.values())
    console.print()
    console.print(
        Panel(
            f"[bold]Scanning:[/] {url}\n[dim]{total_probes} probes across {len(counts)} domains[/]",
            title="🔍 AgentBench Scanner",
            border_style="blue",
        )
    )

    # Run the scan
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task(f"Running {total_probes} probes...", total=total_probes)
        result = asyncio.run(
            run_scan(
                url,
                api_key=api_key,
                model=model,
                domains=domain,
                timeout=timeout,
            )
        )
        progress.update(task, completed=total_probes)

    # Render results
    _render_scorecard(result)

    # Save to leaderboard
    from agentbench.leaderboard import add_scan_result

    add_scan_result(result, label=url)
    console.print("[dim]Result added to leaderboard.[/dim]")

    # Save output if requested
    if output:
        with open(output, "w") as f:
            json.dump(result.to_dict(), f, indent=2)
        console.print(f"\n[dim]Results saved to {output}[/dim]")

    console.print()

    # Exit code: 1 if any critical findings
    critical = sum(1 for f in result.findings if f.severity == Severity.CRITICAL)
    if critical > 0:
        raise typer.Exit(code=1)


def _render_scorecard(result) -> None:
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
    table.add_column("Total", justify="right", dim=True)

    for name in ["safety", "reliability", "capability", "consistency"]:
        ds = result.domain_scores.get(name)
        if ds:
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
        sc = (
            "green"
            if entry["overall_score"] >= 80
            else "yellow"
            if entry["overall_score"] >= 60
            else "red"
        )
        table.add_row(
            entry.get("timestamp", "")[:19],
            entry.get("label", entry.get("url", "")),
            f"[{sc}]{entry['overall_score']}[/{sc}]",
            f"[{sc}]{entry['grade']}[/{sc}]",
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
