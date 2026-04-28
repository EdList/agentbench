"""CLI command: ``agentbench gate``

CI gate — replay all recorded workflows, produce a pass/fail verdict,
and exit 1 if any workflow regresses.
"""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentbench.gate.runner import GateResult, GateRunner

console = Console()


def gate_command(
    url: str = typer.Option(
        ..., "--url", "-u", help="Agent endpoint URL"
    ),
    format: str = typer.Option(
        "openai", "--format", "-f", help="Agent API format: openai | raw"
    ),
    header: list[str] | None = typer.Option(
        None, "--header", "-H", help="HTTP header (key:value), repeatable"
    ),
    timeout: float = typer.Option(
        30.0, "--timeout", "-t", help="Request timeout (seconds)"
    ),
    api_key: str | None = typer.Option(
        None, "--api-key", "-k", help="API key (sent as Bearer token)"
    ),
    threshold: float = typer.Option(
        0.8, "--threshold", help="Regression score threshold (0-1)"
    ),
    workflow: list[str] | None = typer.Option(
        None, "--workflow", "-w",
        help="Specific workflow(s) to gate (repeatable). Default: all.",
    ),
    save_reports: bool = typer.Option(
        True, "--save-reports/--no-save-reports",
        help="Save individual replay reports to disk",
    ),
) -> None:
    """CI gate — replay workflows and block on regression.

    Loads all recorded workflows (or specific ones with -w), replays each
    against the current agent, and produces a pass/fail verdict.

    Exit code 0 = all clear, 1 = regression detected.
    Use in CI/CD pipelines to block deployments that cause behavioral changes.
    """
    # -- Parse headers -------------------------------------------------------
    headers: dict[str, str] = {}
    if header:
        for h in header:
            if ":" in h:
                key, value = h.split(":", 1)
                headers[key.strip()] = value.strip()

    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    # -- Validate URL --------------------------------------------------------
    try:
        httpx.URL(url)
    except Exception:
        console.print(f"[red]Invalid URL:[/red] {url}")
        raise typer.Exit(1) from None

    # -- Banner --------------------------------------------------------------
    workflow_list = workflow or ["all"]
    console.print()
    console.print(
        Panel(
            f"[bold]🚧 CI Gate[/bold]\n"
            f"   Target: {url}\n"
            f"   Threshold: {threshold:.0%}\n"
            f"   Workflows: {', '.join(workflow_list)}",
            title="AgentBench Gate",
            border_style="yellow",
        )
    )
    console.print()

    # -- Run gate ------------------------------------------------------------
    runner = GateRunner(
        agent_url=url,
        agent_format=format,
        headers=headers,
        timeout=timeout,
        threshold=threshold,
    )

    result = runner.run(workflow_names=workflow if workflow else None)

    # -- Display results -----------------------------------------------------
    _display_result(result)

    # -- Save reports --------------------------------------------------------
    if save_reports:
        for wr in result.workflow_results:
            if wr.report:
                path = wr.report.save()
                console.print(f"   Report: {path}")

    console.print()

    # -- Exit code -----------------------------------------------------------
    if not result.passed:
        console.print(
            "[bold red]🚫 GATE FAILED — behavioral regression detected[/bold red]"
        )
        raise typer.Exit(1)
    else:
        console.print("[bold green]✅ GATE PASSED — all workflows nominal[/bold green]")


def _display_result(result: GateResult) -> None:
    """Render gate results to console."""
    # Summary table
    table = Table(title="Gate Results", show_lines=True)
    table.add_column("Workflow", style="cyan", max_width=25)
    table.add_column("Score", justify="right", width=8)
    table.add_column("Turns", justify="right", width=8)
    table.add_column("Verdict", width=8)

    for wr in result.workflow_results:
        verdict = "[green]PASS[/green]" if wr.passed else "[red]FAIL[/red]"
        score_str = f"{wr.score:.0%}"

        if wr.error:
            score_str = "[red]ERR[/red]"

        table.add_row(
            wr.workflow_name,
            score_str,
            f"{wr.pass_count}/{wr.turn_count}",
            verdict,
        )

    console.print(table)

    # Aggregate
    console.print()
    agg_verdict = "[green]PASSED[/green]" if result.passed else "[red]FAILED[/red]"
    console.print(
        f"  Result: {agg_verdict}  "
        f"Workflows: {result.passed_workflows}/{result.total_workflows} passed  "
        f"Total turns: {result.total_turns}"
    )

    # Errors
    errored = [wr for wr in result.workflow_results if wr.error]
    if errored:
        console.print("\n[bold red]Errors:[/bold red]")
        for wr in errored:
            console.print(f"  {wr.workflow_name}: {wr.error}")
