"""CLI command: ``agentbench record <url>``

Interactive session recorder — capture a multi-turn agent interaction
as a reusable :class:`~agentbench.recorder.workflow.Workflow`.
"""

from __future__ import annotations

import httpx
import typer
from rich.console import Console
from rich.panel import Panel

from agentbench.recorder.recorder import SessionRecorder

console = Console()


def record_command(
    url: str = typer.Argument(..., help="Agent endpoint URL"),
    name: str = typer.Option(..., "--name", "-n", help="Workflow name"),
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
) -> None:
    """Record a multi-turn interaction with an agent endpoint.

    Start an interactive session.  Type messages to send to the agent.
    Every turn, tool call, and timing measurement is captured into a
    reusable workflow file.

    Use /done or Ctrl+D to finish and save.
    Use /cancel to discard without saving.
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
        raise typer.Exit(1)  # noqa: PLR1720

    # -- Banner --------------------------------------------------------------
    console.print()
    console.print(
        Panel(
            f"[bold]🎬 Recording workflow:[/bold] {name}\n"
            f"   Agent: {url}\n"
            f"   Format: {format}\n"
            "\n   Type messages to send. "
            "[dim]/done[/dim] to finish, [dim]/cancel[/dim] to discard.",
            title="AgentBench Recorder",
            border_style="cyan",
        )
    )
    console.print()

    # -- Create recorder -----------------------------------------------------
    recorder = SessionRecorder(
        agent_url=url,
        workflow_name=name,
        agent_format=format,
        headers=headers,
        timeout=timeout,
    )

    # -- Interactive loop ----------------------------------------------------
    try:
        while True:
            try:
                user_input = console.input("[bold cyan]You:[/bold cyan] ").strip()
            except (EOFError, KeyboardInterrupt):
                console.print()
                break

            if not user_input:
                continue

            if user_input == "/cancel":
                recorder.cancel()
                console.print("[yellow]Recording cancelled.[/yellow]")
                raise typer.Exit(0)

            if user_input in ("/done", "/exit", "/quit"):
                break

            # Send to agent
            try:
                turn = recorder.send(user_input)
            except Exception as exc:  # noqa: BLE001
                console.print(f"[red]Error:[/red] {exc}")
                continue

            # Display response
            if turn.latency_ms < 1000:
                latency = f"{turn.latency_ms:.0f}ms"
            else:
                latency = f"{turn.latency_ms / 1000:.1f}s"

            if turn.error:
                console.print(
                    f"[red]Agent ({latency}):[/red] {turn.agent_response}"
                )
            else:
                console.print(
                    f"[green]Agent ({latency}):[/green] {turn.agent_response}"
                )

            # Display tool calls
            for tc in turn.tool_calls:
                args_display = (
                    tc.arguments
                    if len(tc.arguments) < 80
                    else tc.arguments[:77] + "..."
                )
                console.print(f"  🔧 [dim]{tc.name}({args_display})[/dim]")

            console.print()

    except typer.Exit:
        return

    # -- Save workflow -------------------------------------------------------
    if recorder.turn_count == 0:
        console.print("[yellow]No turns recorded. Discarding.[/yellow]")
        recorder.cancel()
        raise typer.Exit(0)

    workflow = recorder.finish()
    path = workflow.save()

    total_s = workflow.total_duration_ms / 1000
    console.print(
        f"[green]✅ Workflow saved:[/green] {workflow.name} "
        f"({workflow.turn_count} turns, {workflow.total_tool_calls} tool calls, "
        f"{total_s:.1f}s total)"
    )
    console.print(f"   Location: {path}")
