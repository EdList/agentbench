"""CLI command: ``agentbench dashboard``

Start the workflow health dashboard server.
"""

from __future__ import annotations

import logging

import typer
from rich.console import Console

console = Console()
logger = logging.getLogger(__name__)


def dashboard_command(
    port: int = 8080,
    host: str = "127.0.0.1",
    base_dir: str | None = None,
    token: str | None = None,
) -> None:
    """Start the workflow health dashboard.

    Launches a local web server serving the AgentBench dashboard.
    Shows workflow health, regression timeline, and replay history.

    Open http://127.0.0.1:8080 in your browser after starting.
    """
    from pathlib import Path

    root = Path(base_dir) if base_dir else Path.cwd()

    console.print()
    console.print("  [bold cyan]⚡ AgentBench Dashboard[/bold cyan]")
    console.print(f"  Serving from: {root}")
    console.print(f"  URL: [underline]http://{host}:{port}[/underline]")

    if token:
        console.print("  Auth: [green]bearer token enabled[/green]")
    else:
        console.print(
            "  Auth: [yellow]disabled[/yellow] "
            "(use --token to enable)"
        )

    # Warn when binding to non-localhost
    if host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "Dashboard is binding to a non-localhost address (%s). "
            "Ensure the network is trusted or use --token to enable authentication.",
            host,
        )
        console.print(
            f"  [yellow]⚠ Binding to non-localhost ({host}). "
            "Consider using --token.[/yellow]"
        )

    console.print("  Press Ctrl+C to stop")
    console.print()

    try:
        import uvicorn

        from agentbench.dashboard.app import create_dashboard_app

        app = create_dashboard_app(base_dir=root, auth_token=token)
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        console.print(
            "[red]Error:[/red] Server dependencies not installed.\n"
            "  Install with: [bold]pip install 'agentbench-cli[server]'[/bold]"
        )
        raise typer.Exit(1) from None
