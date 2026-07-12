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
from agentbench.probes.base import Severity
from agentbench.probes.registry import get_probe_counts
from agentbench.scanner.agent_runner import AgentScanResult

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
def discover(
    url: str = typer.Argument(..., help="Agent endpoint URL to discover."),
    api_key: str | None = typer.Option(
        None, "--api-key", "-k",
        envvar="AGENTBENCH_API_KEY",
        help="API key for the agent endpoint.",
    ),
    timeout: float = typer.Option(15.0, "--timeout", "-t"),
    allow_insecure_http: bool = typer.Option(False, "--allow-insecure-http"),
) -> None:
    """Auto-discover an agent's tools and attack surface."""
    from agentbench.discovery import discover_agent

    _validate_url(url, api_key=api_key, allow_insecure_http=allow_insecure_http)

    console.print()
    console.print(
        Panel(
            f"[bold]Discovering:[/] {url}",
            title="🔍 AgentBench Discovery",
            border_style="blue",
        )
    )

    profile = asyncio.run(
        discover_agent(url, api_key=api_key, timeout=timeout)
    )

    console.print("\n[bold]Discovery methods tried:[/]")
    for method in profile.discovery_methods_tried:
        status = "✓" if method in profile.discovery_methods_succeeded else "✗"
        color = "green" if method in profile.discovery_methods_succeeded else "dim"
        console.print(f"  [{color}]{status}[/{color}] {method.value}")

    if profile.tools:
        console.print(f"\n[bold green]Found {len(profile.tools)} tools[/]")
        tool_table = Table(title="Discovered Tools", show_header=True)
        tool_table.add_column("Name", style="cyan")
        tool_table.add_column("Risk", justify="center")
        tool_table.add_column("Description", style="dim")

        risk_colors = {
            "critical": "red bold",
            "high": "yellow",
            "medium": "blue",
            "low": "green",
        }
        for tool in profile.tools:
            risk_style = risk_colors.get(tool.risk.value, "white")
            desc = tool.description[:60] + "..." if len(tool.description) > 60 else tool.description
            tool_table.add_row(
                tool.name,
                f"[{risk_style}]{tool.risk.value.upper()}[/{risk_style}]",
                desc,
            )
        console.print(tool_table)
    else:
        console.print("\n[dim]No tools discovered via standard protocols.[/]")
        console.print("[dim]Heuristic probing will be used during scan.[/]")

    console.print("\n[bold]Attack Surface Summary[/]")
    console.print(profile.attack_surface_summary)


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
    use_llm: bool = typer.Option(
        False,
        "--llm-analyzer",
        help="Enable LLM-based second-pass analysis for fewer false positives.",
    ),
    analyzer_key: str | None = typer.Option(
        None,
        "--analyzer-key",
        envvar="ANALYZER_API_KEY",
        help="API key for the LLM analyzer (defaults to --api-key).",
    ),
    analyzer_model: str | None = typer.Option(
        None,
        "--analyzer-model",
        envvar="ANALYZER_MODEL",
        help="Model for LLM analysis (default: meta-llama/llama-3.3-70b-instruct).",
    ),
) -> None:
    """Scan an agent endpoint for security vulnerabilities.

    Auto-discovers your agent's tools, generates targeted probes based on
    the discovered attack surface, fires them, and produces a scorecard.

    Exit code 1 if any critical findings.
    """
    if timeout <= 0:
        console.print(f"[red]Error:[/red] Timeout must be positive, got {timeout}")
        raise typer.Exit(code=1)

    _validate_url(url, api_key=api_key, allow_insecure_http=allow_insecure_http)

    from agentbench.scanner.agent_runner import run_agent_scan

    console.print()
    console.print(
        Panel(
            f"[bold]Scanning:[/] {url}\n"
            f"[dim]Discovery → Probe Generation → Analysis[/]",
            title="🔍 AgentBench Scanner",
            border_style="blue",
        )
    )

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:
        task = progress.add_task("Discovering agent tools...", total=None)

        async def _on_progress(completed: int, total: int) -> None:
            progress.update(
                task,
                description=f"Running probes ({completed}/{total})...",
                total=total,
                completed=completed,
            )

        result = asyncio.run(
            run_agent_scan(
                url,
                api_key=api_key,
                model=model,
                timeout=timeout,
                use_llm_analyzer=use_llm,
                analyzer_api_key=analyzer_key or api_key,
                analyzer_model=analyzer_model,
                progress_callback=_on_progress,
            )
        )

    # Render results
    _render_agent_scorecard(result)

    # Save output
    if output:
        try:
            import os
            import tempfile

            output_dir = os.path.dirname(os.path.abspath(output))
            tmp_fd, tmp_path = tempfile.mkstemp(dir=output_dir, suffix=".tmp")
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

    console.print()

    # Exit code: 1 if any critical findings
    if result.critical_count > 0:
        raise typer.Exit(code=1)


def _render_agent_scorecard(result: AgentScanResult) -> None:
    """Render the agent-system scan results as a Rich terminal scorecard."""
    console.print()

    # Header: discovery summary
    console.print(
        Panel(
            f"[bold]Discovery:[/] {len(result.profile.tools)} tools found via "
            f"{', '.join(m.value for m in result.profile.discovery_methods_succeeded) or 'heuristic'}\n"
            f"[bold]Probes:[/] {result.probes_run} targeted probes in {result.duration_seconds:.1f}s",
            title="Scan Complete",
            border_style="blue",
        )
    )

    # Tool summary
    if result.profile.tools:
        tool_table = Table(title="Discovered Attack Surface", show_header=True)
        tool_table.add_column("Tool", style="cyan")
        tool_table.add_column("Risk", justify="center")
        for t in result.profile.tools:
            risk_colors = {
                "critical": "red bold",
                "high": "yellow",
                "medium": "blue",
                "low": "green",
            }
            rs = risk_colors.get(t.risk.value, "white")
            tool_table.add_row(
                t.name,
                f"[{rs}]{t.risk.value.upper()}[/{rs}]",
            )
        console.print(tool_table)

    # Overall score
    grade = result.grade
    grade_color = {
        "A": "green", "B": "green", "C": "yellow",
        "D": "red", "F": "bold red",
    }
    color = grade_color.get(grade, "white")
    console.print(
        f"\n  [bold]Score:[/] [{color}]{result.overall_score}/100"
        f" (Grade: {grade})[/{color}]"
    )

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
            console.print(f"\n  [yellow]⚠️  Warnings ({len(warn)})[/yellow]")
            for f in warn:
                console.print(f"    • {f.title}")
                console.print(f"      [dim]{f.detail[:120]}[/dim]")
                if f.remediation:
                    console.print(f"      [green]↳ Fix: {f.remediation[:100]}[/green]")

        if info:
            console.print(f"\n  [dim]ℹ️  Info ({len(info)})[/dim]")
            for f in info[:5]:
                console.print(f"    [dim]• {f.title}[/dim]")
    else:
        console.print(
            "\n  [bold green]✅ No vulnerabilities found. "
            "Your agent resisted all attacks.[/bold green]"
        )


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
