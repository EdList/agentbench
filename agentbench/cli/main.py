"""AgentBench CLI — main entry point."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    name="agentbench",
    help="Behavioral testing framework for AI agents.",
    no_args_is_help=True,
)
console = Console()


def _find_adapter_in_path(path: Path) -> Any:
    """Discover an AgentTest class and extract its adapter from a Python file or directory."""
    from agentbench.core.test import AgentTest

    if path.is_file() and path.suffix == ".py":
        files = [path]
    elif path.is_dir():
        files = sorted(path.rglob("test_*.py"))
    else:
        return None

    for py_file in files:
        try:
            spec = importlib.util.spec_from_file_location(py_file.stem, py_file)
            if not spec or not spec.loader:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            for _name, obj in inspect.getmembers(module, inspect.isclass):
                if issubclass(obj, AgentTest) and obj is not AgentTest:
                    instance = obj()
                    if instance.adapter:
                        return instance
        except Exception:
            continue
    return None


@app.command()
def run(
    path: str = typer.Argument(".", help="Path to test suite or directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show step-by-step output"),
    filter_pattern: str | None = typer.Option(
        None, "--filter", "-f", help="Filter tests by name pattern"
    ),
    parallel: int = typer.Option(1, "--parallel", "-p", help="Number of parallel workers"),
    report: str | None = typer.Option(None, "--report", "-r", help="Output file for report (JSON)"),
) -> None:
    """Run agent test suites."""
    from agentbench.core.config import AgentBenchConfig
    from agentbench.core.runner import TestRunner

    # Load config
    bench_config = AgentBenchConfig.from_yaml(config) if config else AgentBenchConfig()
    bench_config.parallel_workers = parallel

    # Discover and run
    console.print(Panel("🧪 AgentBench", subtitle="Testing what your agent actually does"))

    runner = TestRunner(config={
        "verbose": verbose,
        "filter": filter_pattern,
        "parallel": parallel,
        "max_steps": bench_config.max_steps,
        "timeout_seconds": bench_config.timeout_seconds,
        "max_retries": bench_config.max_retries,
        "default_adapter": bench_config.default_adapter,
        "bench_config": bench_config,
    })
    result = runner.run(Path(path))

    # Warn if no tests found
    if result.total_tests == 0:
        console.print(
            "[yellow]⚠ No tests found.[/yellow] "
            "Check your path and test file names (test_*.py)."
        )
        raise typer.Exit(1)

    # Display results
    for suite_result in result.suite_results:
        console.print(suite_result.summary())

        # Verbose: show assertion details for each test
        if verbose:
            for test_result in suite_result.results:
                if test_result.assertions:
                    for a in test_result.assertions:
                        icon = "✓" if a.passed else "✗"
                        console.print(f"    {icon} {a.message}")

    # Summary
    console.print(f"\n[bold]Total: {result.total_passed} passed, {result.total_failed} failed, "
                  f"{result.total_tests} tests[/bold]")
    console.print(f"Duration: {result.total_duration_ms / 1000:.1f}s")

    # Save report if requested
    if report:
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
                            "assertions": [
                                {"passed": a.passed, "message": a.message, "type": a.assertion_type}
                                for a in t.assertions
                            ],
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
    agent: str = typer.Argument(..., help="Agent name or path to test directory"),
    prompt: str = typer.Argument(..., help="Prompt to send to the agent"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file for trajectory"),
    name: str | None = typer.Option(None, "--name", "-n", help="Name for this recording"),
) -> None:
    """Record a golden agent trajectory for diffing later."""
    console.print(f"[bold]Recording trajectory[/bold] for agent '{agent}'")
    console.print(f"Prompt: {prompt!r}")

    # Try to discover an adapter from the given path
    agent_path = Path(agent)
    test_instance = None

    if agent_path.exists():
        test_instance = _find_adapter_in_path(agent_path)
    else:
        # Try common locations
        for candidate in [Path("."), Path("tests"), Path("test")]:
            if candidate.exists():
                test_instance = _find_adapter_in_path(candidate)
                if test_instance:
                    break

    if test_instance is None:
        console.print(
            "[red]Could not find an agent adapter. "
            "Provide a path to your test directory.[/red]"
        )
        console.print("[dim]Usage: agentbench record ./tests \"Your prompt\"[/dim]")
        raise typer.Exit(1)

    # Run the agent
    trajectory = test_instance.run(prompt)

    # Build recording
    recording_name = name or f"recording-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trajectory_data = trajectory.to_dict()
    trajectory_data["name"] = recording_name
    trajectory_data["recorded_at"] = datetime.now().isoformat()
    trajectory_data["prompt"] = prompt

    # Save
    output_path = Path(output or f".agentbench/trajectories/{recording_name}.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(trajectory_data, indent=2, default=str))

    console.print(f"[green]✓[/green] Trajectory saved to {output_path}")
    console.print(f"  Steps recorded: {trajectory.step_count}")
    console.print(f"  Completed: {trajectory.completed}")
    console.print(f"  Duration: {trajectory.total_latency_ms:.0f}ms")


@app.command()
def diff(
    golden: str = typer.Argument(..., help="Path to golden trajectory JSON"),
    current: str | None = typer.Option(
        None, "--current", "-c",
        help="Path to current trajectory (runs agent if not provided)"
    ),
    agent_path: str = typer.Option(".", "--agent", "-a",
                                    help="Path to agent test directory (for auto re-run)"),
) -> None:
    """Compare current agent run against a golden trajectory."""
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
        # Auto re-run: find the adapter and execute with the same prompt
        prompt = golden_data.get("prompt", golden_data.get("input_prompt", ""))
        if not prompt:
            console.print(
                "[red]Golden trajectory has no recorded prompt. "
                "Provide --current path.[/red]"
            )
            raise typer.Exit(1)

        test_instance = _find_adapter_in_path(Path(agent_path))
        if test_instance is None:
            console.print(
                f"[red]No agent adapter found in {agent_path}. "
                f"Provide --current path.[/red]"
            )
            raise typer.Exit(1)

        console.print(f"[dim]Re-running agent with prompt: {prompt!r}[/dim]")
        trajectory = test_instance.run(prompt)
        current_data = trajectory.to_dict()
        current_data["name"] = f"current-{datetime.now().strftime('%Y%m%d-%H%M%S')}"

    # Diff
    differ = TrajectoryDiff()
    result = differ.compare(golden_data, current_data)

    console.print(result.format_output())

    # Exit with error if critical differences found
    if result.has_critical:
        raise typer.Exit(1)


@app.command()
def init(
    name: str = typer.Argument("agent-tests", help="Name for the test project"),
    framework: str = typer.Option(
        "raw_api", "--framework", "-f",
        help="Agent framework: raw_api, langchain, openai, crewai, autogen, langgraph"
    ),
    path: str | None = typer.Option(None, "--path", "-p", help="Output directory"),
) -> None:
    """Scaffold a new AgentBench test project."""
    from agentbench.cli.scaffold import scaffold_project

    output_path = Path(path or name)
    scaffold_project(output_path, name, framework)
    console.print(f"[green]✓[/green] Created test project: {output_path}")
    console.print(f"\n  cd {output_path}")
    console.print("  agentbench run")


@app.command()
def watch(
    path: str = typer.Argument(".", help="Path to test suite or directory"),
    filter_pattern: str | None = typer.Option(
        None, "--filter", "-f", help="Filter tests by name pattern"
    ),
    config: str | None = typer.Option(
        None, "--config", "-c", help="Path to config YAML"
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show step-by-step output"),
) -> None:
    """Watch test files for changes and re-run automatically."""
    try:
        from watchdog.events import FileCreatedEvent, FileModifiedEvent, FileSystemEventHandler
        from watchdog.observers import Observer
    except ImportError:
        console.print(
            "[red]watchdog is required for watch mode. "
            "Install with: pip install agentbench[watch][/red]"
        )
        raise typer.Exit(1)

    from agentbench.core.config import AgentBenchConfig
    from agentbench.core.runner import TestRunner

    target = Path(path).resolve()
    if not target.exists():
        console.print(f"[red]Path does not exist: {path}[/red]")
        raise typer.Exit(1)

    bench_config = AgentBenchConfig.from_yaml(config) if config else AgentBenchConfig()

    runner = TestRunner(config={
        "verbose": verbose,
        "filter": filter_pattern,
        "bench_config": bench_config,
    })

    def _run_tests() -> None:
        console.print(Panel("🧪 AgentBench [dim]watch mode[/dim]"))
        result = runner.run(target)

        if result.total_tests == 0:
            console.print("[yellow]⚠ No tests found.[/yellow]")
            return

        for suite_result in result.suite_results:
            console.print(suite_result.summary())

        console.print(
            f"\n[bold]Total: {result.total_passed} passed, {result.total_failed} failed, "
            f"{result.total_tests} tests[/bold]"
        )
        console.print(f"Duration: {result.total_duration_ms / 1000:.1f}s")

        if result.total_failed > 0:
            console.print("[red]✗ Some tests failed[/red]")
        else:
            console.print("[green]✓ All tests passed[/green]")

    # Initial run
    _run_tests()
    console.print(f"\n[dim]Watching {target} for changes… (Ctrl+C to stop)[/dim]\n")

    class _TestChangeHandler(FileSystemEventHandler):
        def on_modified(self, event: FileModifiedEvent) -> None:
            if event.src_path.endswith(".py"):
                console.print(f"\n[blue]↻ Change detected: {event.src_path}[/blue]")
                _run_tests()
                console.print(f"\n[dim]Watching {target} for changes… (Ctrl+C to stop)[/dim]\n")

        def on_created(self, event: FileCreatedEvent) -> None:
            if event.src_path.endswith(".py"):
                console.print(f"\n[blue]↻ New file detected: {event.src_path}[/blue]")
                _run_tests()
                console.print(f"\n[dim]Watching {target} for changes… (Ctrl+C to stop)[/dim]\n")

    observer = Observer()
    watch_path = target if target.is_dir() else target.parent
    observer.schedule(_TestChangeHandler(), str(watch_path), recursive=True)
    observer.start()

    try:
        import threading
        stop_event = threading.Event()
        stop_event.wait()
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


@app.command()
def report(
    json_report: str = typer.Argument(..., help="Path to JSON report file"),
    output: str | None = typer.Option(None, "--output", "-o", help="Output HTML file path"),
) -> None:
    """Generate an HTML report from a saved JSON report."""
    from agentbench.cli.report import generate_html_report

    report_path = Path(json_report)
    if not report_path.exists():
        console.print(f"[red]Report file not found: {json_report}[/red]")
        raise typer.Exit(1)

    output_path = Path(output) if output else report_path.with_suffix(".html")
    generate_html_report(report_path, output_path)
    console.print(f"[green]✓[/green] HTML report generated: {output_path}")


@app.command(name="list")
def list_tests(
    path: str = typer.Argument(".", help="Path to test directory or file"),
    filter_pattern: str | None = typer.Option(
        None, "--filter", "-f", help="Filter by name pattern"
    ),
) -> None:
    """Discover and list all test suites and methods without running them."""
    from agentbench.core.runner import TestRunner

    target = Path(path)
    runner = TestRunner(config={"filter": filter_pattern})
    suites = runner.discover_suites(target)

    if not suites:
        console.print("[yellow]No test suites found.[/yellow]")
        raise typer.Exit(0)

    console.print(Panel("📋 AgentBench Test Discovery"))

    total_suites = 0
    total_methods = 0

    for suite_class in suites:
        total_suites += 1
        temp_instance = suite_class()
        test_methods = [
            name
            for name, method in inspect.getmembers(temp_instance, predicate=inspect.ismethod)
            if name.startswith("test_")
        ]

        # Apply filter
        if filter_pattern:
            import re
            pattern = re.compile(filter_pattern, re.IGNORECASE)
            test_methods = [m for m in test_methods if pattern.search(m)]

        if filter_pattern and not test_methods:
            continue

        total_methods += len(test_methods)

        suite_icon = "📦"
        console.print(
            f"\n{suite_icon} [bold]{suite_class.__name__}[/bold]  "
            f"[dim]({len(test_methods)} tests)[/dim]"
        )

        for method_name in test_methods:
            method = getattr(temp_instance, method_name)
            doc = inspect.getdoc(method)
            doc_text = f"  [dim]— {doc}[/dim]" if doc else ""
            console.print(f"  ○ {method_name}{doc_text}")

    console.print(
        f"\n[bold]Summary:[/bold] "
        f"{total_suites} suite(s), {total_methods} test method(s)"
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-h", help="Bind host"),
    port: int = typer.Option(8000, "--port", "-p", help="Bind port"),
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev only)"),
) -> None:
    """Start the AgentBench Cloud API server."""
    try:
        import uvicorn  # noqa: F401
    except ImportError:
        console.print(
            "[red]Server dependencies not installed. "
            "Install with: pip install agentbench[server][/red]"
        )
        raise typer.Exit(1)

    from agentbench.server.models import create_tables

    console.print(Panel("🌐 AgentBench API Server", subtitle=f"http://{host}:{port}"))
    create_tables()
    console.print("[dim]Database tables ready.[/dim]")

    import uvicorn as _uvicorn
    _uvicorn.run(
        "agentbench.server.app:app",
        host=host,
        port=port,
        reload=reload,
    )


if __name__ == "__main__":
    app()
