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
        None, "--version", "-v", callback=_version, is_eager=True,
        help="Show version and exit.",
    ),
) -> None:
    """AgentBench — Behavioral CI for AI agents."""
    pass


@app.command()
def scan(
    url: str = typer.Argument(..., help="Agent endpoint URL to scan."),
    api_key: str | None = typer.Option(
        None, "--api-key", "-k", envvar="AGENTBENCH_API_KEY",
        help="API key for the agent endpoint.",
    ),
    model: str | None = typer.Option(
        None, "--model", "-m", envvar="AGENTBENCH_MODEL",
        help="Model name (required by some endpoints like OpenRouter).",
    ),
    output: str | None = typer.Option(
        None, "--output", "-o",
        help="Save results as JSON to this file.",
    ),
    domain: list[str] | None = typer.Option(
        None, "--domain", "-d",
        help="Restrict scan to specific domains "
             "(safety, reliability, capability, consistency).",
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", "-t",
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
            f"[bold]Scanning:[/] {url}\n"
            f"[dim]{total_probes} probes across {len(counts)} domains[/]",
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
        task = progress.add_task(
            f"Running {total_probes} probes...", total=total_probes
        )
        result = asyncio.run(run_scan(
            url,
            api_key=api_key,
            model=model,
            domains=domain,
            timeout=timeout,
        ))
        progress.update(task, completed=total_probes)

    # Render results
    _render_scorecard(result)

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
        "A": "green", "B": "green", "C": "yellow", "D": "red", "F": "bold red",
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

        if warn:
            console.print(f"  [yellow]⚠️  Warnings ({len(warn)})[/yellow]")
            for f in warn:
                console.print(f"    • {f.title}")
                console.print(f"      [dim]{f.detail[:120]}[/dim]")

        if info:
            console.print(f"  [dim]ℹ️  Info ({len(info)})[/dim]")
            for f in info[:5]:
                console.print(f"    [dim]• {f.title}[/dim]")
            if len(info) > 5:
                console.print(f"    [dim]... and {len(info) - 5} more[/dim]")
    else:
        console.print(
            "\n  [bold green]✅ No issues found. Your agent looks solid.[/bold green]"
        )


@app.command()
def probes() -> None:
    """List all available probes."""
    all_probes = get_probe_counts()
    console.print(
        f"\n[bold]AgentBench Probes[/bold] "
        f"({sum(all_probes.values())} total)\n"
    )

    table = Table(show_header=True, header_style="bold")
    table.add_column("Domain", style="cyan")
    table.add_column("Count", justify="right")

    for domain_name in ["safety", "reliability", "capability", "consistency"]:
        count = all_probes.get(domain_name, 0)
        table.add_row(domain_name.title(), str(count))

    console.print(table)
    console.print()
