"""CLI command: ``agentbench dashboard``

Start the workflow health dashboard server.
"""

from __future__ import annotations

import typer
from rich.console import Console

console = Console()


def dashboard_command(
    port: int = typer.Option(8080, "--port", "-p", help="Server port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Server host"),
    base_dir: str | None = typer.Option(
        None, "--dir", "-d", help="Base directory (default: cwd)"
    ),
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
    console.print("  Press Ctrl+C to stop")
    console.print()

    try:
        import uvicorn

        from agentbench.dashboard.app import create_dashboard_app

        app = create_dashboard_app(base_dir=root)
        uvicorn.run(app, host=host, port=port, log_level="info")
    except ImportError:
        console.print(
            "[red]Error:[/red] Server dependencies not installed.\n"
            "  Install with: [bold]pip install 'agentbench-cli[server]'[/bold]"
        )
        raise typer.Exit(1) from None
