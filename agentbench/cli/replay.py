"""CLI command: ``agentbench replay <workflow>``

Replay a recorded workflow against a live agent and produce a regression
report comparing the new responses against the original baseline.
"""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentbench.recorder.workflow import Workflow
from agentbench.replayer.diff import WorkflowDiffer
from agentbench.replayer.replayer import ReplayEngine
from agentbench.replayer.report import ReplayReport

console = Console()


def replay_command(
    workflow_name: str = typer.Argument(..., help="Name of recorded workflow to replay"),
    url: str | None = typer.Option(
        None, "--url", "-u", help="Agent endpoint URL (default: reuse original)"
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
    save_report: bool = typer.Option(
        True, "--save-report/--no-save-report", help="Save report to disk"
    ),
) -> None:
    """Replay a recorded workflow against a live agent and detect regressions.

    Loads the workflow, re-sends every user message to the current agent,
    compares tool call sequences and response semantics, and outputs a
    pass/fail regression report.
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

    # -- Load workflow -------------------------------------------------------
    try:
        baseline = Workflow.load(workflow_name)
    except FileNotFoundError:
        console.print(f"[red]Workflow not found:[/red] {workflow_name}")
        raise typer.Exit(1) from None

    target_url = url or baseline.agent_url
    target_format = format or baseline.agent_format

    # -- Validate URL --------------------------------------------------------
    try:
        httpx.URL(target_url)
    except Exception:
        console.print(f"[red]Invalid URL:[/red] {target_url}")
        raise typer.Exit(1)  # noqa: PLR1720

    # -- Banner --------------------------------------------------------------
    console.print()
    console.print(
        Panel(
            f"[bold]🔄 Replaying workflow:[/bold] {workflow_name}\n"
            f"   Baseline: {baseline.turn_count} turns, "
            f"{baseline.total_tool_calls} tool calls\n"
            f"   Target: {target_url}\n"
            f"   Threshold: {threshold:.0%}",
            title="AgentBench Replay",
            border_style="cyan",
        )
    )
    console.print()

    # -- Replay --------------------------------------------------------------
    engine = ReplayEngine(
        agent_url=target_url,
        agent_format=target_format,
        headers=headers,
        timeout=timeout,
    )

    replayed = engine.replay(baseline)

    # -- Diff ----------------------------------------------------------------
    differ = WorkflowDiffer(threshold=threshold)
    diff_result = differ.diff_turns(baseline.turns, replayed.turns)

    # -- Build report --------------------------------------------------------
    report = ReplayReport.from_diff(
        workflow_name=replayed.name,
        replay_of=baseline.name,
        diff_result=diff_result,
        original_responses=[t.agent_response for t in baseline.turns],
        replayed_responses=[t.agent_response for t in replayed.turns],
        original_tool_names=[
            [tc.name for tc in t.tool_calls] for t in baseline.turns
        ],
        replayed_tool_names=[
            [tc.name for tc in t.tool_calls] for t in replayed.turns
        ],
        user_messages=baseline.user_messages,
        threshold=threshold,
    )

    # -- Display results -----------------------------------------------------
    _display_report(report, threshold)

    # -- Save report ---------------------------------------------------------
    if save_report:
        path = report.save()
        console.print(f"   Report saved: {path}")

    # -- Exit code -----------------------------------------------------------
    if not report.passed:
        raise typer.Exit(1)


def _display_report(report: ReplayReport, threshold: float) -> None:
    """Render the replay report to the console."""
    # Summary line
    verdict = "[green]✅ PASSED[/green]" if report.passed else "[red]❌ REGRESSION[/red]"
    console.print(
        f"{verdict}  Score: {report.overall_score:.1%} "
        f"(threshold: {threshold:.0%})  "
        f"Turns: {report.pass_count}/{report.turn_count} passed"
    )
    console.print()

    if not report.turn_results:
        console.print("[dim]No turns to compare.[/dim]")
        return

    # Per-turn table
    table = Table(title="Per-Turn Results", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("User Message", style="cyan", max_width=30)
    table.add_column("Tools (orig→replay)", max_width=30)
    table.add_column("Score", justify="right", width=6)
    table.add_column("Verdict", width=8)

    for tr in report.turn_results:
        verdict_str = "[green]PASS[/green]" if tr.passed else "[red]FAIL[/red]"
        msg_display = (
            tr.user_message if len(tr.user_message) <= 30
            else tr.user_message[:27] + "..."
        )
        orig_tools = ", ".join(tr.original_tools) or "—"
        replay_tools = ", ".join(tr.replayed_tools) or "—"
        tools_display = f"{orig_tools} → {replay_tools}"
        if len(tools_display) > 30:
            tools_display = tools_display[:27] + "..."

        table.add_row(
            str(tr.turn_index),
            msg_display,
            tools_display,
            f"{tr.score:.0%}",
            verdict_str,
        )

    console.print(table)
    console.print()

    # Notes for failed turns
    failed = [tr for tr in report.turn_results if not tr.passed]
    if failed:
        console.print("[bold red]Failed turns:[/bold red]")
        for tr in failed:
            console.print(f"  Turn {tr.turn_index}: {tr.notes}")
        console.print()
