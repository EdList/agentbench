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


def _find_adapter_in_path(path: Path, agent_name: str | None = None) -> Any:
    """Discover an AgentTest class and extract its adapter from a Python file or directory."""
    from agentbench.core.test import AgentTest

    if path.is_file() and path.suffix == ".py":
        files = [path]
    elif path.is_dir():
        files = sorted(path.rglob("test_*.py"))
    else:
        return None

    first_match = None
    requested_name = agent_name.strip() if agent_name else None

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
                    if not instance.adapter:
                        continue
                    if requested_name is not None and instance.agent == requested_name:
                        return instance
                    if first_match is None:
                        first_match = instance
        except Exception:
            continue
    return first_match


def _discover_agent_instance(agent: str) -> Any:
    """Resolve either an explicit path or a named agent from common test locations."""
    agent_path = Path(agent)
    if agent_path.exists():
        return _find_adapter_in_path(agent_path)

    for candidate in [Path("."), Path("tests"), Path("test")]:
        if candidate.exists():
            test_instance = _find_adapter_in_path(candidate, agent_name=agent)
            if test_instance:
                return test_instance
    return None


def _discover_config_path(path: Path) -> Path | None:
    search_root = path if path.is_dir() else path.parent
    for filename in ("agentbench.yaml", "agentbench.yml"):
        candidate = search_root / filename
        if candidate.exists():
            return candidate
    return None


def _load_bench_config(config: str | None, path: Path):
    from agentbench.core.config import AgentBenchConfig

    if config:
        return AgentBenchConfig.from_yaml(config)

    discovered = _discover_config_path(path)
    if discovered is not None:
        return AgentBenchConfig.from_yaml(discovered)
    return AgentBenchConfig()


@app.command()
def run(
    path: str = typer.Argument(".", help="Path to test suite or directory"),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config YAML"),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show step-by-step output"),
    filter_pattern: str | None = typer.Option(
        None, "--filter", "-f", help="Filter tests by name pattern"
    ),
    parallel: int | None = typer.Option(
        None, "--parallel", "-p", help="Number of parallel workers"
    ),
    report: str | None = typer.Option(None, "--report", "-r", help="Output file for report (JSON)"),
) -> None:
    """Run agent test suites."""
    from agentbench.core.runner import TestRunner

    target_path = Path(path)
    bench_config = _load_bench_config(config, target_path)
    effective_parallel = parallel if parallel is not None else bench_config.parallel_workers
    bench_config.parallel_workers = effective_parallel

    # Discover and run
    console.print(Panel("🧪 AgentBench", subtitle="Testing what your agent actually does"))

    runner = TestRunner(
        config={
            "verbose": verbose,
            "filter": filter_pattern,
            "parallel": effective_parallel,
            "max_steps": bench_config.max_steps,
            "timeout_seconds": bench_config.timeout_seconds,
            "max_retries": bench_config.max_retries,
            "default_adapter": bench_config.default_adapter,
            "bench_config": bench_config,
        }
    )
    result = runner.run(Path(path))

    # Warn if no tests found
    if result.total_tests == 0:
        console.print(
            "[yellow]⚠ No tests found.[/yellow] Check your path and test file names (test_*.py)."
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
    console.print(
        f"\n[bold]Total: {result.total_passed} passed, {result.total_failed} failed, "
        f"{result.total_tests} tests[/bold]"
    )
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

    # Try to discover an adapter from the given path or requested agent name
    test_instance = _discover_agent_instance(agent)

    if test_instance is None:
        console.print(
            "[red]Could not find an agent adapter. Provide a path to your test directory.[/red]"
        )
        console.print('[dim]Usage: agentbench record ./tests "Your prompt"[/dim]')
        raise typer.Exit(1)

    # Run the agent
    trajectory = test_instance.run(prompt)

    # Build recording
    recording_name = name or f"recording-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    trajectory_data = trajectory.to_dict()
    trajectory_data["name"] = recording_name
    trajectory_data["recorded_at"] = datetime.now().isoformat()
    trajectory_data["prompt"] = prompt
    trajectory_data["agent_name"] = test_instance.agent

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
        None, "--current", "-c", help="Path to current trajectory (runs agent if not provided)"
    ),
    agent_path: str = typer.Option(
        ".", "--agent", "-a", help="Path to agent test directory (for auto re-run)"
    ),
) -> None:
    """Compare current agent run against a golden trajectory."""
    from agentbench.storage.trajectory import TrajectoryDiff

    # Load golden
    golden_path = Path(golden)
    if not golden_path.exists():
        console.print(f"[red]Golden trajectory not found: {golden}[/red]")
        raise typer.Exit(1)

    try:
        golden_data = json.loads(golden_path.read_text())
    except json.JSONDecodeError as exc:
        console.print(f"[red]Invalid JSON in golden trajectory: {exc}[/red]")
        raise typer.Exit(1) from exc

    # Load or run current
    if current:
        current_path = Path(current)
        if not current_path.exists():
            console.print(f"[red]Current trajectory not found: {current}[/red]")
            raise typer.Exit(1)
        try:
            current_data = json.loads(current_path.read_text())
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid JSON in current trajectory: {exc}[/red]")
            raise typer.Exit(1) from exc
    else:
        # Auto re-run: find the adapter and execute with the same prompt
        prompt = golden_data.get("prompt", golden_data.get("input_prompt", ""))
        if not prompt:
            console.print(
                "[red]Golden trajectory has no recorded prompt. Provide --current path.[/red]"
            )
            raise typer.Exit(1)

        test_instance = _find_adapter_in_path(
            Path(agent_path),
            agent_name=golden_data.get("agent_name"),
        )
        if test_instance is None:
            console.print(
                f"[red]No agent adapter found in {agent_path}. Provide --current path.[/red]"
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
        "raw_api",
        "--framework",
        "-f",
        help="Scaffold framework: raw_api, langchain",
    ),
    path: str | None = typer.Option(None, "--path", "-p", help="Output directory"),
) -> None:
    """Scaffold a new AgentBench test project."""
    from agentbench.cli.scaffold import scaffold_project

    output_path = Path(path or name)
    try:
        scaffold_project(output_path, name, framework)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    console.print(f"[green]✓[/green] Created test project: {output_path}")
    console.print(f"\n  cd {output_path}")
    console.print("  agentbench run")


@app.command()
def watch(
    path: str = typer.Argument(".", help="Path to test suite or directory"),
    filter_pattern: str | None = typer.Option(
        None, "--filter", "-f", help="Filter tests by name pattern"
    ),
    config: str | None = typer.Option(None, "--config", "-c", help="Path to config YAML"),
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

    from agentbench.core.runner import TestRunner

    target = Path(path).resolve()
    if not target.exists():
        console.print(f"[red]Path does not exist: {path}[/red]")
        raise typer.Exit(1)

    bench_config = _load_bench_config(config, target)

    runner = TestRunner(
        config={
            "verbose": verbose,
            "filter": filter_pattern,
            "bench_config": bench_config,
        }
    )

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
        f"\n[bold]Summary:[/bold] {total_suites} suite(s), {total_methods} test method(s)"
    )


@app.command()
def serve(
    host: str = typer.Option("0.0.0.0", "--host", "-H", help="Bind host"),
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


# ---------------------------------------------------------------------------
# Adversarial sub-command
# ---------------------------------------------------------------------------

adversarial_app = typer.Typer(
    name="adversarial",
    help="Experimental adversarial test generation and variant tooling.",
    no_args_is_help=True,
)
app.add_typer(adversarial_app, name="adversarial")


@adversarial_app.command("generate")
def adversarial_generate(
    path: str = typer.Argument(..., help="Path to test file or directory"),
    strategy: str = typer.Option(
        "all",
        "--strategy",
        "-s",
        help="Strategy to use: jailbreak, pii_leak, tool_confusion, context_overflow, or all",
    ),
    count: int = typer.Option(10, "--count", "-n", help="Number of variants per strategy"),
    intensity: int = typer.Option(1, "--intensity", "-i", help="Intensity level (1-5)"),
    output: str | None = typer.Option(
        None, "--output", "-o", help="Output file for generated tests"
    ),
    seed: int | None = typer.Option(None, "--seed", help="Random seed for reproducibility"),
) -> None:
    """Generate adversarial test files from a base test suite (experimental)."""
    console.print(
        "[bold yellow]⚠ Experimental:[/bold yellow]"
        " Adversarial tooling is alpha. APIs may change.\n"
    )
    from agentbench.adversarial.discovery import AdversarialTestGenerator
    from agentbench.adversarial.strategies import (
        STRATEGY_REGISTRY,
        get_strategy,
    )
    from agentbench.core.runner import TestRunner

    target = Path(path)
    if not target.exists():
        console.print(f"[red]Path not found: {path}[/red]")
        raise typer.Exit(1)

    # Discover test classes
    runner = TestRunner()
    suites = runner.discover_suites(target)

    if not suites:
        console.print(f"[yellow]No test suites found in {path}[/yellow]")
        raise typer.Exit(1)

    # Determine strategies
    if strategy == "all":
        strategy_names = list(STRATEGY_REGISTRY.keys())
    else:
        strategy_names = [s.strip() for s in strategy.split(",")]

    for sname in strategy_names:
        if sname not in STRATEGY_REGISTRY:
            console.print(f"[red]Unknown strategy: {sname}[/red]")
            console.print(f"Available: {', '.join(STRATEGY_REGISTRY.keys())}")
            raise typer.Exit(1)

    console.print(
        Panel(
            "🛡️ Adversarial Test Generation",
            subtitle=(
                f"Strategies: {', '.join(strategy_names)} | Count: {count} | Intensity: {intensity}"
            ),
        )
    )

    total_variants = 0

    for suite_class in suites:
        strategies = [
            get_strategy(s, intensity=intensity, count=count, seed=seed) for s in strategy_names
        ]

        generator = AdversarialTestGenerator(
            suite_class,
            strategies=strategies,
            seed=seed,
        )
        generated_class = generator.generate_class()

        console.print(
            f"\n📦 [bold]{suite_class.__name__}[/bold] → [green]{generated_class.__name__}[/green]"
        )

        # Count test methods
        gen_methods = [
            name
            for name in dir(generated_class)
            if name.startswith("test_") and callable(getattr(generated_class, name))
        ]
        total_variants += len(gen_methods)
        console.print(f"  Generated {len(gen_methods)} adversarial test methods")

        if output:
            # Write the generated class to a file
            output_path = Path(output)
            _write_adversarial_file(output_path, suite_class, generated_class, strategy_names)

    console.print(
        f"\n[bold green]✓[/bold green] Generated {total_variants} adversarial variants total"
    )

    if output:
        console.print(f"  Written to: {output}")


@adversarial_app.command("list-strategies")
def adversarial_list_strategies() -> None:
    """List all available adversarial strategies (experimental)."""
    console.print(
        "[bold yellow]⚠ Experimental:[/bold yellow]"
        " Adversarial tooling is alpha. APIs may change.\n"
    )
    from agentbench.adversarial.strategies import list_strategies

    strategies = list_strategies()
    console.print(Panel("🛡️ Available Adversarial Strategies"))

    for info in strategies:
        console.print(f"  • [bold]{info['name']}[/bold]: {info['description']}")

    console.print("\nUse with: [dim]agentbench adversarial generate <path> --strategy <name>[/dim]")


def _write_adversarial_file(
    output_path: Path,
    base_class: type,
    generated_class: type,
    strategy_names: list[str],
) -> None:
    """Write a generated adversarial test class to a Python file."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        '"""Auto-generated adversarial tests — DO NOT EDIT MANUALLY."""',
        "",
        "from agentbench.core.test import AgentTest",
        "from agentbench.core.assertions import expect",
        "",
        "",
        f"class {generated_class.__name__}({base_class.__name__}):",
        f'    """Adversarial variants generated from {base_class.__name__}.',
        f"    Strategies: {', '.join(strategy_names)}",
        '    """',
        "",
        "    # Auto-generated test methods",
    ]

    for method_name in sorted(dir(generated_class)):
        if method_name.startswith("test_") and callable(getattr(generated_class, method_name)):
            method = getattr(generated_class, method_name)
            doc = getattr(method, "__doc__", "") or ""
            lines.append(f"    def {method_name}(self):")
            if doc:
                lines.append(f'        """{doc}"""')
            lines.append("        pass  # adversarial placeholder — implement or use run()")
            lines.append("")

    output_path.write_text("\n".join(lines))


# ---------------------------------------------------------------------------
# Scan sub-command
# ---------------------------------------------------------------------------


@app.command()
def scan(
    path: str = typer.Argument(..., help="Python file, HTTP endpoint, or module:var path to agent"),
    output: str = typer.Option(
        "tests/auto_test.py", "--output", "-o", help="Output file for generated tests"
    ),
    categories: str | None = typer.Option(
        None,
        "--categories",
        "-C",
        help="Comma-separated categories: capability,safety,edge_case,persona,robustness",
    ),
    no_run: bool = typer.Option(
        False, "--no-run", help="Skip running generated tests after writing"
    ),
) -> None:
    """Scan an agent and auto-generate behavioral tests."""
    from agentbench.scanner.analyzer import BehaviorAnalyzer
    from agentbench.scanner.generator import TestGenerator
    from agentbench.scanner.prober import AgentProber

    console.print(
        Panel("🔍 AgentBench Scanner", subtitle="Auto-detect agent behaviors and generate tests")
    )

    # --- Step 1: Load agent ---
    console.print("\n[bold]Step 1:[/bold] Loading agent…")
    agent = None
    agent_path = Path(path)

    if agent_path.exists():
        # Try to discover agent from file/directory
        agent = _find_adapter_in_path(agent_path)
        if agent is None:
            # Try importing as module:var
            console.print("[dim]No adapter discovered from path, trying module import…[/dim]")
            agent = _load_agent_from_module(path)
    elif path.startswith("http://") or path.startswith("https://"):
        # HTTP endpoint — create a callable wrapper
        agent = _load_agent_from_url(path)
    elif ":" in path:
        agent = _load_agent_from_module(path)
    else:
        agent = _load_agent_from_module(path)

    if agent is None:
        console.print(f"[red]Could not load agent from: {path}[/red]")
        console.print("[dim]Provide a Python file, module:var, or HTTP endpoint.[/dim]")
        raise typer.Exit(1)

    console.print(f"  [green]✓[/green] Agent loaded: {type(agent).__name__}")

    # --- Step 2: Probe ---
    console.print("\n[bold]Step 2:[/bold] Probing agent…")
    category_list = [c.strip() for c in categories.split(",")] if categories else None

    # Build a callable agent_fn from the loaded agent
    if hasattr(agent, "run"):

        def _agent_fn(prompt: str) -> str:
            trajectory = agent.run(prompt)
            return trajectory.final_response or ""
    elif callable(agent):

        def _agent_fn(prompt: str) -> str:
            result = agent(prompt)
            return result if isinstance(result, str) else str(result)
    else:

        def _agent_fn(prompt: str) -> str:
            return ""

    prober = AgentProber(_agent_fn, categories=category_list)
    session = prober.probe_all()
    console.print(
        f"  [green]✓[/green] Sent {len(session.results)} probe(s) in {session.duration:.2f}s"
    )

    # --- Step 3: Analyze ---
    console.print("\n[bold]Step 3:[/bold] Analyzing behaviors…")
    analyzer = BehaviorAnalyzer()
    behaviors = analyzer.analyze(session)
    console.print(f"  [green]✓[/green] Detected {len(behaviors)} behavior(s)")

    if not behaviors:
        console.print("[yellow]No behaviors detected. Try different categories.[/yellow]")
        raise typer.Exit(0)

    # --- Step 4: Generate ---
    console.print("\n[bold]Step 4:[/bold] Generating test file…")
    generator = TestGenerator()
    code = generator.generate(behaviors)
    console.print(f"  [green]✓[/green] Generated {len(behaviors)} test method(s)")

    # --- Step 5: Write file ---
    output_path = Path(output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(code)
    console.print(f"  [green]✓[/green] Written to: {output_path}")

    # --- Summary ---
    console.print(Panel("📊 Scan Summary"))
    console.print(f"  Probes sent:     {len(session.results)}")
    console.print(f"  Behaviors found: {len(behaviors)}")
    by_category: dict[str, int] = {}
    for b in behaviors:
        by_category[b.category] = by_category.get(b.category, 0) + 1
    for cat, count in sorted(by_category.items()):
        console.print(f"    • {cat}: {count}")
    console.print(f"  Output file:     {output_path}")

    # --- Optionally run ---
    if not no_run:
        console.print("\n[bold]Step 5:[/bold] Running generated tests…")
        try:
            import shutil
            import subprocess

            pytest_bin = shutil.which("pytest") or shutil.which("py.test")
            if pytest_bin is None:
                console.print(
                    "[yellow]pytest not found. Install it with: pip install pytest[/yellow]\n"
                    f"[dim]Run manually with: pytest {output_path}[/dim]"
                )
            else:
                result = subprocess.run(
                    [pytest_bin, str(output_path), "-v"],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                console.print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
        except Exception as exc:
            console.print(f"[yellow]Could not run tests: {exc}[/yellow]")
    else:
        console.print(
            f"\n[dim]Skipping test run (--no-run). Run manually with: pytest {output_path}[/dim]"
        )


def _load_agent_from_module(path: str) -> Any:
    """Load an agent from a module:var path like 'my_module:agent_func'."""
    if ":" not in path:
        return None
    module_path, var_name = path.rsplit(":", 1)
    try:
        import importlib

        module = importlib.import_module(module_path)
        return getattr(module, var_name, None)
    except Exception:
        return None


def _load_agent_from_url(url: str) -> Any:
    """Create a callable agent wrapper for an HTTP endpoint."""
    import httpx

    def _call(prompt: str, context: dict | None = None) -> str:
        payload = {"prompt": prompt, "context": context or {}}
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return data.get("response", data.get("output", str(data)))

    return _call


if __name__ == "__main__":
    app()
