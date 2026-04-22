"""AgentBench CLI — main entry point."""

from __future__ import annotations

import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    name="agentbench",
    help="Behavioral testing framework for AI agents.",
    no_args_is_help=True,
)
console = Console()


@app.command()
def run(
    path: str = typer.Argument(".", help="Path to test suite or directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show step-by-step output"),
    filter: str | None = typer.Option(None, "--filter", "-f", help="Filter tests by name pattern"),
    parallel: int = typer.Option(1, "--parallel", "-p", help="Number of parallel workers"),
    report: str | None = typer.Option(None, "--report", "-r", help="Output file for report (JSON)"),
) -> None:
    """Run agent test suites."""
    from agentbench.core.runner import TestRunner
    from agentbench.core.config import AgentBenchConfig

    # Load config
    bench_config = AgentBenchConfig.from_yaml(config) if config else AgentBenchConfig()
    bench_config.parallel_workers = parallel

    # Discover and run
    console.print(Panel("🧪 AgentBench", subtitle="Testing what your agent actually does"))

    runner = TestRunner(config={"verbose": verbose, "filter": filter})
    result = runner.run(Path(path))

    # Display results
    for suite_result in result.suite_results:
        console.print(suite_result.summary())

    # Summary
    console.print(f"\n[bold]Total: {result.total_passed} passed, {result.total_failed} failed, "
                  f"{result.total_tests} tests[/bold]")
    console.print(f"Duration: {result.total_duration_ms / 1000:.1f}s")

    # Save report if requested
    if report:
        import json
        report_data = {
            "total_tests": result.total_tests,
            "passed": result.total_passed,
            "failed": result.total_failed,
            "duration_ms": result.total_duration_ms,
            "suites": [
                {
                    "name": s.suite_name,
                    "passed": s.passed,
                    "failed": s.failed,
                    "tests": [
                        {
                            "name": t.test_name,
                            "passed": t.passed,
                            "duration_ms": t.duration_ms,
                            "error": t.error,
                        }
                        for t in s.results
                    ],
                }
                for s in result.suite_results
            ],
        }
        Path(report).write_text(json.dumps(report_data, indent=2))
        console.print(f"\nReport saved to {report}")

    # Exit code
    sys.exit(1 if result.total_failed > 0 else 0)


@app.command()
def record(
    agent: str = typer.Argument(..., help="Agent name or path"),
    prompt: str = typer.Argument(..., help="Prompt to send to the agent"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file for trajectory"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for this recording"),
) -> None:
    """Record a golden agent trajectory for diffing later."""
    import json
    from datetime import datetime

    console.print(f"[bold]Recording trajectory[/bold] for agent '{agent}'")
    console.print(f"Prompt: {prompt!r}")

    # TODO: Actually run the agent and record
    # For now, create a placeholder
    trajectory = {
        "name": name or f"recording-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
        "agent": agent,
        "prompt": prompt,
        "recorded_at": datetime.now().isoformat(),
        "steps": [],
    }

    output_path = Path(output or f".agentbench/trajectories/{trajectory['name']}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(trajectory, indent=2))

    console.print(f"[green]✓[/green] Trajectory saved to {output_path}")


@app.command()
def diff(
    golden: str = typer.Argument(..., help="Path to golden trajectory JSON"),
    current: str | None = typer.Option(None, "--current", "-c",
                                        help="Path to current trajectory (runs agent if not provided)"),
) -> None:
    """Compare current agent run against a golden trajectory."""
    import json
    from agentbench.storage.trajectory import TrajectoryDiff

    # Load golden
    golden_path = Path(golden)
    if not golden_path.exists():
        console.print(f"[red]Golden trajectory not found: {golden}[/red]")
        raise typer.Exit(1)

    golden_data = json.loads(golden_path.read_text())

    # Load or run current
    if current:
        current_path = Path(current)
        if not current_path.exists():
            console.print(f"[red]Current trajectory not found: {current}[/red]")
            raise typer.Exit(1)
        current_data = json.loads(current_path.read_text())
    else:
        # TODO: Re-run agent with same prompt
        console.print("[yellow]Auto re-run not yet implemented. Provide --current path.[/yellow]")
        raise typer.Exit(1)

    # Diff
    differ = TrajectoryDiff()
    result = differ.compare(golden_data, current_data)

    console.print(result.format_output())


@app.command()
def init(
    name: str = typer.Argument("agent-tests", help="Name for the test project"),
    framework: str = typer.Option("raw_api", "--framework", "-f",
                                   help="Agent framework: langchain, openai, raw_api"),
    path: str | None = typer.Option(None, "--path", "-p", help="Output directory"),
) -> None:
    """Scaffold a new AgentBench test project."""
    from agentbench.cli.scaffold import scaffold_project

    output_path = Path(path or name)
    scaffold_project(output_path, name, framework)
    console.print(f"[green]✓[/green] Created test project: {output_path}")
    console.print(f"\n  cd {output_path}")
    console.print(f"  agentbench run")


if __name__ == "__main__":
    app()
