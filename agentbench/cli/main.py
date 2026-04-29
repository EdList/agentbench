"""AgentBench CLI — main entry point."""

from __future__ import annotations

import importlib
import inspect
import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from agentbench.scanner.scorer import ScanReport

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
                    # Preserve discovery metadata so `agentbench scan` can
                    # regenerate a runnable suite that reuses the same base class.
                    setattr(instance, "_agentbench_source_file", str(py_file))
                    setattr(instance, "_agentbench_source_class", obj.__name__)
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
# Scan command — prober → analyzer → scorer → scorecard (no test gen)
# ---------------------------------------------------------------------------


@app.command(name="scan")
def cmd_scan(
    agent_url: str = typer.Argument(..., help="HTTP endpoint of the agent to scan"),
    json_output: str | None = typer.Option(
        None, "--json", "-j", help="Save scan report as JSON to this path"
    ),
    timeout: int = typer.Option(
        300, "--timeout", "-t", help="Total scan timeout in seconds (default 300)"
    ),
    oai: bool = typer.Option(
        False, "--oai", help="Use OpenAI-compatible /v1/chat/completions format"
    ),
    header: list[str] = typer.Option(
        [], "--header", "-H",
        help='Extra HTTP header, e.g. "Authorization: Bearer xx"',
    ),
    model_id: str | None = typer.Option(
        None, "--model", "-m", help="Model ID for OpenAI-compatible requests"
    ),
    categories: str | None = typer.Option(
        None, "--categories", "-C",
        help="Comma-separated categories to probe "
        "(capability,safety,edge_case,persona,robustness,conversation)",
    ),
) -> None:
    """Scan an agent endpoint, score its behavior, display a report.

    Paste your agent URL, get a behavioral scorecard. No Python needed.
    """
    import hashlib
    import json as _json
    import time as _time

    import httpx
    from rich import box
    from rich.table import Table

    from agentbench.scanner.analyzer import BehaviorAnalyzer
    from agentbench.scanner.prober import ALL_CATEGORIES, AgentProber
    from agentbench.scanner.scorer import ScoringEngine as Scorer

    console.print(Panel("🧪 AgentBench Scan", subtitle=agent_url))

    # --- Build HTTP callable ---
    headers = {}
    for h in header:
        if ":" in h:
            k, v = h.split(":", 1)
            headers[k.strip()] = v.strip()

    format_mode = "oai" if oai else "auto"

    # Create a single httpx.Client for the entire scan (reuses TCP connection)
    client_kwargs: dict[str, Any] = {"timeout": 30.0}
    if headers:
        client_kwargs["headers"] = headers
    shared_client = httpx.Client(**client_kwargs)

    def _agent_fn(prompt: str) -> str:
        """Send a probe prompt to the agent and return the text response."""
        if format_mode == "oai":
            body = {
                "model": model_id or "default",
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            body = {"prompt": prompt}

        try:
            resp = shared_client.post(agent_url, json=body)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return f"ERROR: Connection failed — {exc}"
        except httpx.HTTPStatusError as exc:
            return f"ERROR: HTTP {exc.response.status_code}"
        except (_json.JSONDecodeError, ValueError):
            return "ERROR: Server returned non-JSON response"

        # Extract response text
        if format_mode == "oai":
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return str(data)
        else:
            # Try common response fields
            for field in ("response", "output", "text", "content", "result"):
                if field in data and isinstance(data[field], str):
                    return data[field]
            # Fall back to first string value in response
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str):
                        return v
                    # Look in nested structures
                    if isinstance(v, dict) and "content" in v:
                        return v["content"]
                    if isinstance(v, list) and v:
                        item = v[0]
                        if isinstance(item, dict) and "content" in item:
                            return item["content"]
                        if isinstance(item, dict) and "message" in item:
                            msg = item["message"]
                            if isinstance(msg, dict) and "content" in msg:
                                return msg["content"]
            return str(data)

    # --- Step 1: Probe ---
    selected_cats = list(ALL_CATEGORIES)
    if categories:
        selected_cats = [c.strip() for c in categories.split(",")]
        invalid = [c for c in selected_cats if c not in ALL_CATEGORIES]
        if invalid:
            console.print(
                f"[red]Unknown categories:[/red] {', '.join(invalid)}\n"
                f"Valid: {', '.join(ALL_CATEGORIES)}"
            )
            raise typer.Exit(1)

    console.print("\n[bold]Step 1[/bold] [dim]— probing agent behaviors[/dim]")
    deadline = _time.monotonic() + timeout
    try:
        prober = AgentProber(_agent_fn, categories=selected_cats)
        session = prober.probe_all(deadline=deadline)
    finally:
        shared_client.close()
    errors = sum(1 for r in session.results if r.metadata.get("status") == "error")
    console.print(
        f"  [green]✓[/green] {len(session.results)} probes in {session.duration:.1f}s"
        f"{f' · {errors} error(s)' if errors else ''}"
    )

    # --- Step 2: Analyze ---
    console.print("\n[bold]Step 2[/bold] [dim]— analyzing detected behaviors[/dim]")
    analyzer = BehaviorAnalyzer()
    behaviors = analyzer.analyze(session)
    console.print(f"  [green]✓[/green] {len(behaviors)} behavior(s) detected")

    # --- Step 3: Score ---
    console.print("\n[bold]Step 3[/bold] [dim]— scoring[/dim]")
    scorer = Scorer()
    report = scorer.score(behaviors)

    # --- Display scorecard ---
    console.print()

    # Overall score
    grade = report.overall_grade
    score = report.overall_score
    grade_color = {
        "A": "green", "B": "green", "C": "yellow",
        "D": "red", "F": "red",
    }.get(grade, "white")

    status_text = "✅ PASS" if score >= 70 else "⚠️ WARNING" if score >= 50 else "❌ FAIL"

    overall_table = Table(box=box.SIMPLE, show_header=False, expand=True)
    overall_table.add_column("Left", ratio=1)
    overall_table.add_column("Center", ratio=1)
    overall_table.add_column("Right", ratio=1)
    overall_table.add_row(
        "",
        f"[bold]{status_text}[/bold]",
        f"[bold]{score:.0f}[/bold] / 100",
    )
    console.print(
        Panel(
            overall_table,
            title="  AgentBench Behavioral Report  ",
            subtitle=f"Grade: [{grade_color}]{grade}[/{grade_color}]  ·  "
                     f"{report.behaviors_tested} behaviors tested  ·  "
                     f"{report.behaviors_passed} passed, {report.behaviors_failed} failed",
        )
    )
    console.print()

    # Domain scores table
    domain_table = Table(title="Domain Scores", box=box.SIMPLE_HEAD, expand=True)
    domain_table.add_column("Domain", style="bold")
    domain_table.add_column("Score", justify="right")
    domain_table.add_column("Bar", ratio=2)
    domain_table.add_column("Grade")

    for ds in report.domain_scores:
        bar_len = int(ds.score / 2.5)
        bar = "█" * (bar_len // 2) + "░" * (20 - bar_len // 2)
        color = "green" if ds.score >= 70 else ("yellow" if ds.score >= 50 else "red")
        domain_table.add_row(
            ds.name,
            f"[bold]{ds.score:.0f}[/bold]",
            f"[{color}]{bar}[/{color}]",
            f"[{color}]{ds.grade}[/{color}]",
        )

    console.print(domain_table)
    console.print()

    # Score bar visual
    filled = int(score / 2)
    bar_color = "green" if score >= 70 else ("yellow" if score >= 50 else "red")
    bar = "█" * filled + "░" * (50 - filled)
    console.print(f"[{bar_color}]{bar}[/{bar_color}]  [bold]{score:.0f}%[/bold]")
    console.print()

    # Critical issues
    if report.critical_issues:
        console.print(Panel(
            "\n".join(f"🔴 {issue}" for issue in report.critical_issues),
            title="🔴 Critical Issues",
            border_style="red",
        ))
        console.print()

    # Per-domain findings
    for ds in report.domain_scores:
        if ds.findings:
            lines = []
            for f_item in ds.findings:
                lines.append(f"• {f_item}")
            console.print(f"[bold]{ds.name}[/bold] ({ds.score:.0f}/100, grade {ds.grade})")
            console.print("\n".join(lines[:5]))
            if len(ds.findings) > 5:
                console.print(f"  [dim]...and {len(ds.findings) - 5} more[/dim]")
            console.print()

    # Executive summary
    if report.summary:
        console.print(f"[dim]Summary: {report.summary}[/dim]")
        console.print()

    # Scan metadata
    scan_id = hashlib.sha256(
        f"{agent_url}:{report.timestamp.isoformat()}"
        .encode()
    ).hexdigest()[:12]
    console.print(f"[dim]Scan ID: {scan_id} | Timestamp: {report.timestamp.isoformat()}[/dim]")

    # JSON output
    if json_output:
        Path(json_output).write_text(_json.dumps(report.to_dict(), indent=2, default=str))
        console.print(f"\n[green]✓[/green] Report saved to {json_output}")

    # Exit code: non-zero if agent fails behavioral threshold
    sys.exit(0 if score >= 70 else 1)


# ---------------------------------------------------------------------------
# Legacy scan — prober → analyzer → test generation → run (kept for compat)
# ---------------------------------------------------------------------------


@app.command(name="scan-detailed")
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

    generator_kwargs: dict[str, Any] = {}
    source_file = getattr(agent, "_agentbench_source_file", None)
    source_class = getattr(agent, "_agentbench_source_class", None)

    if source_file and source_class:
        generator_kwargs.update(
            {
                "base_class_name": source_class,
                "header_extra": (
                    "import importlib.util as _ab_importlib_util\n\n"
                    f"_ab_spec = _ab_importlib_util.spec_from_file_location(\n"
                    f"    'agentbench_scanned_source', {source_file!r}\n"
                    ")\n"
                    "if _ab_spec is None or _ab_spec.loader is None:\n"
                    "    raise RuntimeError('Could not load scanned source suite.')\n"
                    "_ab_source_module = _ab_importlib_util.module_from_spec(_ab_spec)\n"
                    "_ab_spec.loader.exec_module(_ab_source_module)\n"
                    f"{source_class} = getattr(_ab_source_module, {source_class!r})"
                ),
            }
        )
    elif path.startswith("http://") or path.startswith("https://"):
        generator_kwargs.update(
            {
                "header_extra": "from agentbench.adapters.raw_api import RawAPIAdapter",
                "class_preamble": (
                    f"agent = {path!r}\n"
                    f"adapter = RawAPIAdapter(endpoint={path!r})"
                ),
            }
        )
    elif ":" in path:
        module_path, var_name = path.rsplit(":", 1)
        generator_kwargs.update(
            {
                "header_extra": (
                    "import importlib as _ab_importlib\n"
                    "from agentbench.adapters.raw_api import RawAPIAdapter\n\n"
                    "_ab_scanned_obj = getattr(\n"
                    f"    _ab_importlib.import_module({module_path!r}), {var_name!r}\n"
                    ")"
                ),
                "class_preamble": (
                    f"agent = getattr(_ab_scanned_obj, 'agent', {path!r})\n"
                    "if (\n"
                    "    hasattr(_ab_scanned_obj, 'adapter')\n"
                    "    and getattr(_ab_scanned_obj, 'adapter') is not None\n"
                    "):\n"
                    "    adapter = _ab_scanned_obj.adapter\n"
                    "elif callable(_ab_scanned_obj):\n"
                    "    adapter = RawAPIAdapter(func=_ab_scanned_obj)\n"
                    "else:\n"
                    "    raise RuntimeError('Imported scan target is not runnable by AgentBench.')"
                ),
            }
        )

    generator = TestGenerator(**generator_kwargs)
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
            import subprocess

            result = subprocess.run(
                [sys.executable, "-m", "agentbench.cli.main", "run", str(output_path)],
                capture_output=True,
                text=True,
                timeout=60,
            )
            if result.stdout:
                console.print(result.stdout[-2000:] if len(result.stdout) > 2000 else result.stdout)
            if result.stderr:
                console.print(result.stderr[-2000:] if len(result.stderr) > 2000 else result.stderr)
            if result.returncode != 0:
                raise typer.Exit(result.returncode)
        except typer.Exit:
            raise
        except Exception as exc:
            console.print(f"[yellow]Could not run generated tests: {exc}[/yellow]")
            raise typer.Exit(1) from exc
    else:
        console.print(
            "\n[dim]Skipping test run (--no-run). "
            f"Run manually with: agentbench run {output_path}[/dim]"
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


# ---------------------------------------------------------------------------
# HTTP agent caller — supports OAI-compatible and simple prompt formats
# ---------------------------------------------------------------------------

class _HTTPAgent:
    """Callable wrapper for an HTTP agent endpoint.

    Supports OpenAI-compatible chat completions format and simple
    ``{"prompt": "..."}`` format, with error handling.
    """

    def __init__(
        self,
        url: str,
        fmt: str = "auto",
        headers: dict[str, str] | None = None,
        model_id: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._url = url
        self._fmt = fmt  # 'oai', 'prompt', or 'auto'
        self._headers = headers or {}
        self._model_id = model_id
        self._timeout = timeout
        self._effective_fmt: str | None = None  # resolved after first call in auto mode

    def __call__(self, prompt: str, context: dict | None = None) -> str:
        import httpx

        with httpx.Client(
            timeout=self._timeout,
            headers=self._headers,
        ) as client:
            # In auto mode, try OAI first, fallback to prompt
            if self._fmt == "auto" and self._effective_fmt is None:
                # Try OAI format
                oai_result = self._try_call(client, prompt, "oai")
                if oai_result is not None:
                    self._effective_fmt = "oai"
                    return oai_result
                # Fallback to prompt format
                prompt_result = self._try_call(client, prompt, "prompt")
                if prompt_result is not None:
                    self._effective_fmt = "prompt"
                    return prompt_result
                return "ERROR: all request formats failed"

            target_fmt = self._effective_fmt or self._fmt
            result = self._try_call(client, prompt, target_fmt)
            if result is not None:
                return result
            return "ERROR: request failed"

    def _try_call(
        self,
        client: httpx.Client,
        prompt: str,
        fmt: str,
    ) -> str | None:
        """Make a single request attempt. Returns ``None`` on any error."""
        try:
            request_body: dict[str, object]
            if fmt == "oai":
                messages = [{"role": "user", "content": prompt}]
                request_body = {"messages": messages}
                if self._model_id:
                    request_body["model"] = self._model_id
            else:
                request_body = {"prompt": prompt, "context": {}}

            resp = client.post(self._url, json=request_body)

            # Non-2xx is a hard failure for a format we're committed to
            resp.raise_for_status()

            # Parse JSON
            data = resp.json()

            if fmt == "oai":
                # Extract from choices[0].message.content
                return data["choices"][0]["message"]["content"]
            else:
                # Simple format: look for 'response' or 'output'
                return data.get("response", data.get("output", str(data)))
        except (httpx.TimeoutException, httpx.ConnectError):
            return None
        except httpx.HTTPStatusError:
            return None
        except (json.JSONDecodeError, KeyError, IndexError, TypeError):
            return None
        except Exception:
            return None


def _load_agent_from_url(
    url: str,
    fmt: str = "auto",
    headers: dict[str, str] | None = None,
    model_id: str | None = None,
    timeout: float = 30.0,
) -> _HTTPAgent:
    """Create a callable agent wrapper for an HTTP endpoint.

    Supports OAI-compatible chat completions format and simple prompt format.
    """
    return _HTTPAgent(
        url=url,
        fmt=fmt,
        headers=headers,
        model_id=model_id,
        timeout=timeout,
    )


def _parse_header_options(header_opts: list[str] | None) -> dict[str, str]:
    """Parse ``--header "Key: Value"`` options into a dict."""
    result: dict[str, str] = {}
    if not header_opts:
        return result
    for h in header_opts:
        if ":" in h:
            key, _, value = h.partition(":")
            result[key.strip()] = value.strip()
        else:
            console.print(f"[yellow]⚠ Skipping malformed header: {h!r}[/yellow]")
    return result


# ---------------------------------------------------------------------------
# Scorecard display helpers
# ---------------------------------------------------------------------------

def _render_scorecard(report: ScanReport, url: str) -> None:
    """Display a beautiful Rich scorecard for a ScanReport."""
    from rich.text import Text

    # Gradient colour based on score
    score = report.overall_score
    if score >= 80:
        score_colour = "green"
    elif score >= 60:
        score_colour = "yellow"
    else:
        score_colour = "red"

    top_text = Text()
    top_text.append("  Overall Score:  ")
    top_text.append(f"{report.overall_score:.0f}/100", style=f"{score_colour} bold")
    top_text.append("\n  Grade:          ")
    top_text.append(report.overall_grade, style=f"{score_colour} bold")
    top_text.append(f"\n\n  Behaviours tested: {report.behaviors_tested}")
    top_text.append("\n  Passed:            ")
    top_text.append(str(report.behaviors_passed), style="green")
    top_text.append("\n  Failed:            ")
    top_text.append(str(report.behaviors_failed), style="red")
    top_text.append("\n\n  Summary: ")
    top_text.append(report.summary)

    if report.critical_issues:
        top_text.append("\n\n  Critical Issues:")
        for issue in report.critical_issues:
            top_text.append("\n    • ")
            top_text.append(issue, style="red")

    console.print(
        Panel(
            top_text,
            title=" AgentBench Scan Report",
            subtitle=f"  {url}",
            expand=False,
        )
    )

    # Domain sub-panels
    from rich.text import Text

    for ds in report.domain_scores:
        if ds.score >= 80:
            bar_colour = "green"
        elif ds.score >= 60:
            bar_colour = "yellow"
        else:
            bar_colour = "red"

        filled = round(ds.score / 5)  # 20 chars max
        bar = "█" * filled + "░" * (20 - filled)

        # Build domain panel content using Text to avoid markup conflicts
        domain_text = Text()
        domain_text.append("  Score: ")
        domain_text.append(bar, style=bar_colour)
        domain_text.append(f"  {ds.score:.0f}/100  (Grade: ")
        domain_text.append(ds.grade, style=f"{bar_colour} bold")
        domain_text.append(")")

        if ds.findings:
            domain_text.append("\n  Findings:")
            for finding in ds.findings[:5]:
                f_display = finding if len(finding) <= 100 else finding[:97] + "…"
                domain_text.append("\n    • ")
                domain_text.append(f_display)

        if ds.recommendations:
            domain_text.append("\n\n  Recommendations:")
            for rec in ds.recommendations[:3]:
                domain_text.append("\n    → ")
                domain_text.append(rec)
            if len(ds.recommendations) > 3:
                domain_text.append("\n    …")

        console.print(
            Panel(
                domain_text,
                title=f"  {ds.name}",
                expand=False,
            )
        )

    # Per-behaviour findings table — use console.print with safe rendering
    # to avoid markup interpretation issues in finding text
    console.print()
    console.print("[bold]Detailed Findings[/bold]")
    table = Table(
        show_header=True,
        header_style="bold magenta",
        collapse_padding=True,
    )
    table.add_column("Status", style="bold", width=4)
    table.add_column("Category", width=14)
    table.add_column("Description", overflow="fold")
    table.add_column("", justify="center", width=3)
    for ds in report.domain_scores:
        for finding in ds.findings:
            is_positive = any(
                kw in finding.lower()
                for kw in (
                    "correctly refused", "handled", "mentions capabilities",
                    "responded to capability", "consistent", "no persona leak",
                    "no instruction leak", "returned a response",
                    "handled repeated", "no leak detected", "without error",
                )
            )
            # Use Text objects to avoid markup conflicts in user data
            from rich.text import Text as RText
            status = RText("✓" if is_positive else "✗", style="green" if is_positive else "red")
            table.add_row(
                status,
                ds.name,
                RText(finding),
                "[cyan]▲[/]" if is_positive else "[yellow]▼[/]",
            )
    console.print(table)

    # Share note
    console.print(
        "\n[dim]Scan complete — paste this scan ID in GitHub to share the report[/dim]"
    )


# ---------------------------------------------------------------------------
# scan-report command
# ---------------------------------------------------------------------------

@app.command(name="scan-report")
def scan_report(
    url: str = typer.Argument(..., help="HTTP endpoint of the agent to scan"),
    format: str = typer.Option(
        "auto",
        "--format",
        "-f",
        help=(
            "Request format: 'oai' (OpenAI-compatible), 'prompt' (simple),"
            " or 'auto' (try oai first, fallback to prompt)"
        ),
    ),
    header: list[str] | None = typer.Option(
        None,
        "--header",
        "-H",
        help='Auth headers, e.g. --header "Authorization: Bearer xx". Can be repeated.',
    ),
    model_id: str | None = typer.Option(
        None,
        "--model-id",
        "-m",
        help="Model ID to include in OpenAI-compatible requests",
    ),
    categories: str | None = typer.Option(
        None,
        "--categories",
        "-C",
        help="Comma-separated categories: capability,safety,edge_case,persona,robustness",
    ),
    timeout: int = typer.Option(
        30,
        "--timeout",
        "-t",
        help="Timeout per probe in seconds",
    ),
    deadline: int = typer.Option(
        300,
        "--deadline",
        help="Max total scan time in seconds",
    ),
    use_llm: bool = typer.Option(
        False,
        "--use-llm",
        help="Enable LLM-backed analysis (requires OPENAI_API_KEY or OPENAI_BASE_URL)",
    ),
    as_json: bool = typer.Option(
        False,
        "--json",
        help="Output the ScanReport as JSON to stdout",
    ),
    json_output: str | None = typer.Option(
        None,
        "--json-output",
        help="Write the ScanReport JSON to this file",
    ),
    no_store: bool = typer.Option(
        False,
        "--no-store",
        help="Skip persisting the scan result",
    ),
) -> None:
    """Probe an agent endpoint → analyse behaviours → display a scorecard.

    This command does not generate test files. It produces a direct
    behavioural scorecard suitable for CI dashboards or quick evaluations.

    Example::

        agentbench scan-report https://my-agent.example.com/api/chat
        agentbench scan-report https://api.openai.com/v1/chat/completions \
            --format oai --model-id gpt-4o \
            --header "Authorization: Bearer ***"
    """
    from agentbench.scanner.analyzer import BehaviorAnalyzer
    from agentbench.scanner.prober import AgentProber
    from agentbench.scanner.scorer import ScoringEngine

    console.print(
        Panel("🔍 AgentBench Scan Report", subtitle=f"Probing {url}")
    )

    # --- Step 1: Build HTTP agent ---
    headers = _parse_header_options(header)
    try:
        agent = _load_agent_from_url(
            url=url,
            fmt=format,
            headers=headers,
            model_id=model_id,
            timeout=float(timeout),
        )
    except Exception as exc:
        console.print(f"[red]Failed to create agent wrapper: {exc}[/red]")
        raise typer.Exit(1) from exc

    # --- Step 2: Probe ---
    console.print("\n[bold]Step 1:[/bold] Probing agent…")
    category_list = [c.strip() for c in categories.split(",")] if categories else None

    prober = AgentProber(agent, categories=category_list)
    scan_deadline = time.monotonic() + deadline
    scan_start = time.monotonic()
    session = prober.probe_all(deadline=scan_deadline)

    error_count = sum(
        1 for r in session.results if r.metadata.get("status") == "error"
    )
    console.print(
        f"  [green]✓[/green] Sent {len(session.results)} probe(s) in {session.duration:.2f}s"
        + (f" ([yellow]{error_count} error(s)[/yellow])" if error_count else "")
    )

    if not session.results:
        console.print("[red]No probe results. Check the endpoint and connectivity.[/red]")
        raise typer.Exit(1)

    # --- Step 3: Analyse ---
    console.print("\n[bold]Step 2:[/bold] Analysing behaviours…")
    analyzer = BehaviorAnalyzer(use_llm=use_llm)
    behaviors = analyzer.analyze(session)
    console.print(f"  [green]✓[/green] Detected {len(behaviors)} behaviour(s)")

    if not behaviors:
        console.print(
            "[yellow]No behaviours detected from probe results. "
            "The agent may have returned empty or error responses.[/yellow]"
        )

    # --- Step 4: Score ---
    console.print("\n[bold]Step 3:[/bold] Scoring…")
    scorer = ScoringEngine()
    report = scorer.score(behaviors)
    console.print(
        f"  [green]✓[/green] Score: {report.overall_score:.0f}/100  "
        f"(Grade: {report.overall_grade})"
    )

    step3_end = time.monotonic()

    # --- Step 5: Display ---
    if as_json:
        print(report.to_json())
    else:
        _render_scorecard(report, url)

    # --- JSON output to file ---
    if json_output:
        json_path = Path(json_output)
        json_path.parent.mkdir(parents=True, exist_ok=True)
        json_path.write_text(report.to_json())
        console.print(f"\n[green]✓[/green] Report written to {json_output}")

    # --- Persist ---
    if not no_store and not as_json:
        try:
            import uuid

            from agentbench.scanner.store import ScanStore

            store = ScanStore()
            scan_id = str(uuid.uuid4())
            elapsed_ms = int((step3_end - scan_start) * 1000)
            store.save_scan(
                scan_id=scan_id,
                agent_url=url,
                report=report,
                duration_ms=elapsed_ms,
            )
            console.print(f"\n[dim]Scan saved (id: {scan_id})[/dim]")
        except Exception:
            # Persistence is optional — don't crash the command
            console.print("\n[dim]⚠ Could not persist scan (store unavailable)[/dim]")


# ---------------------------------------------------------------------------
# Shared scan runner — used by scan, baseline-capture, baseline-diff
# ---------------------------------------------------------------------------

def _run_scan(
    agent_url: str,
    oai: bool = False,
    model_id: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 300,
    categories: str | None = None,
) -> tuple[ScanReport, list]:
    """Run a full probe→analyze→score pipeline and return (report, behaviors)."""
    import time as _time

    from agentbench.scanner.analyzer import BehaviorAnalyzer
    from agentbench.scanner.prober import ALL_CATEGORIES, AgentProber
    from agentbench.scanner.scorer import ScoringEngine as Scorer

    fmt = "oai" if oai else "auto"
    hdrs = headers or {}

    client_kwargs: dict[str, Any] = {"timeout": 30.0}
    if hdrs:
        client_kwargs["headers"] = hdrs
    shared_client = httpx.Client(**client_kwargs)

    def _agent_fn(prompt: str) -> str:
        if fmt == "oai":
            body = {
                "model": model_id or "default",
                "messages": [{"role": "user", "content": prompt}],
            }
        else:
            body = {"prompt": prompt}

        try:
            resp = shared_client.post(agent_url, json=body)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
            return f"ERROR: Connection failed — {exc}"
        except httpx.HTTPStatusError as exc:
            return f"ERROR: HTTP {exc.response.status_code}"
        except Exception:
            return "ERROR: Server returned non-JSON response"

        if fmt == "oai":
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError):
                return str(data)
        else:
            for field in ("response", "output", "text", "content", "result"):
                if field in data and isinstance(data[field], str):
                    return data[field]
            if isinstance(data, dict):
                for v in data.values():
                    if isinstance(v, str):
                        return v
            return str(data)

    selected_cats = list(ALL_CATEGORIES)
    if categories:
        selected_cats = [c.strip() for c in categories.split(",")]

    deadline = _time.monotonic() + timeout
    try:
        prober = AgentProber(_agent_fn, categories=selected_cats)
        session = prober.probe_all(deadline=deadline)
    finally:
        shared_client.close()

    analyzer = BehaviorAnalyzer()
    behaviors = analyzer.analyze(session)

    scorer = Scorer()
    report = scorer.score(behaviors)
    return report, behaviors


# ---------------------------------------------------------------------------
# baseline-capture command
# ---------------------------------------------------------------------------


@app.command(name="baseline-capture")
def baseline_capture(
    agent_url: str = typer.Argument(..., help="HTTP endpoint of the agent"),
    name: str = typer.Option(..., "--name", "-n", help="Name for this baseline"),
    oai: bool = typer.Option(False, "--oai", help="Use OpenAI-compatible format"),
    model_id: str | None = typer.Option(None, "--model", "-m", help="Model ID"),
    header: list[str] = typer.Option(
        [], "--header", "-H", help='Extra HTTP header, e.g. "Authorization: Bearer ***"'
    ),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Scan timeout in seconds"),
    categories: str | None = typer.Option(
        None, "--categories", "-C", help="Comma-separated categories"
    ),
) -> None:
    """Run a full scan and save results as a named baseline."""
    from agentbench.scanner.baseline import BaselineManager, _build_baseline

    console.print(Panel("📸 Baseline Capture", subtitle=agent_url))

    headers = _parse_header_options(header)

    console.print("[dim]Running scan…[/dim]")
    report, behaviors = _run_scan(
        agent_url,
        oai=oai,
        model_id=model_id,
        headers=headers,
        timeout=timeout,
        categories=categories,
    )

    mgr = BaselineManager()
    baseline = _build_baseline(name, agent_url, report, behaviors)
    path = mgr.save(baseline)

    console.print(
        f"\n[green]✓[/green] Baseline [bold]{name!r}[/bold] captured: "
        f"score {report.overall_score:.0f}/100 ({report.overall_grade}), "
        f"{len(behaviors)} behaviors, "
        f"{len(report.critical_issues)} critical issue(s)"
    )
    console.print(f"  Saved to: {path}")


# ---------------------------------------------------------------------------
# baseline-diff command
# ---------------------------------------------------------------------------


@app.command(name="baseline-diff")
def baseline_diff(
    agent_url: str = typer.Argument(..., help="HTTP endpoint of the agent"),
    against: str = typer.Option(
        ..., "--against", "-a", help="Baseline name to compare against"
    ),
    oai: bool = typer.Option(False, "--oai", help="Use OpenAI-compatible format"),
    model_id: str | None = typer.Option(None, "--model", "-m", help="Model ID"),
    header: list[str] = typer.Option(
        [], "--header", "-H", help='Extra HTTP header, e.g. "Authorization: Bearer ***"'
    ),
    timeout: int = typer.Option(300, "--timeout", "-t", help="Scan timeout in seconds"),
    categories: str | None = typer.Option(
        None, "--categories", "-C", help="Comma-separated categories"
    ),
) -> None:
    """Run a scan and compare against a saved baseline.

    Exit code 0 if no regression, 1 if regression detected (for CI).
    """
    from rich import box
    from rich.table import Table

    from agentbench.scanner.baseline import BaselineManager

    console.print(Panel("📊 Baseline Diff", subtitle=f"{agent_url} vs {against}"))

    mgr = BaselineManager()
    try:
        baseline = mgr.load(against)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    headers = _parse_header_options(header)

    console.print("[dim]Running scan…[/dim]")
    report, behaviors = _run_scan(
        agent_url,
        oai=oai,
        model_id=model_id,
        headers=headers,
        timeout=timeout,
        categories=categories,
    )

    result = mgr.diff(baseline, report, behaviors)

    # -- Display diff table --------------------------------------------------

    # Score delta
    delta_str = f"{result.score_delta:+.0f}"
    delta_color = "green" if result.score_delta > 0 else (
        "red" if result.score_delta < 0 else "white"
    )
    arrow = "🔴" if result.score_delta < 0 else ("🟢" if result.score_delta > 0 else "➡️")
    console.print(
        f"\n  Score: {baseline.overall_score:.0f} → {report.overall_score:.0f}  "
        f"[{delta_color}]{delta_str} {arrow}[/{delta_color}]"
    )

    # Grade change
    if result.grade_changed:
        console.print(
            f"  Grade: [{delta_color}]{result.old_grade} → {result.new_grade}[/{delta_color}]"
        )
    else:
        console.print(f"  Grade: {result.new_grade} (unchanged)")

    console.print()

    # Vulnerabilities table
    if result.new_vulnerabilities:
        table = Table(
            title="🔴 New Vulnerabilities (regressions)",
            box=box.SIMPLE,
            show_header=True,
        )
        table.add_column("Probe Prompt", style="red")
        for vuln in result.new_vulnerabilities:
            display = vuln if len(vuln) <= 80 else vuln[:77] + "…"
            table.add_row(display)
        console.print(table)
        console.print()

    if result.fixed_vulnerabilities:
        table = Table(
            title="🟢 Fixed Vulnerabilities (improvements)",
            box=box.SIMPLE,
            show_header=True,
        )
        table.add_column("Probe Prompt", style="green")
        for fixed in result.fixed_vulnerabilities:
            display = fixed if len(fixed) <= 80 else fixed[:77] + "…"
            table.add_row(display)
        console.print(table)
        console.print()

    # Critical issues
    if result.new_critical_issues:
        console.print("[red]🔴 New Critical Issues:[/red]")
        for issue in result.new_critical_issues:
            console.print(f"  • {issue}")
        console.print()

    if result.resolved_critical_issues:
        console.print("[green]🟢 Resolved Critical Issues:[/green]")
        for issue in result.resolved_critical_issues:
            console.print(f"  • {issue}")
        console.print()

    # Per-domain deltas
    if result.domain_deltas:
        table = Table(title="Domain Score Deltas", box=box.SIMPLE, show_header=True)
        table.add_column("Domain", style="bold")
        table.add_column("Delta", justify="right")
        for domain, delta in result.domain_deltas.items():
            color = "green" if delta > 0 else ("red" if delta < 0 else "white")
            table.add_row(domain, f"[{color}]{delta:+.1f}[/{color}]")
        console.print(table)
        console.print()

    # Summary
    status = (
        "[red]REGRESSION DETECTED[/red]"
        if result.has_regression
        else "[green]NO REGRESSION[/green]"
    )
    console.print(
        f"  {status}  ·  {result.regressions} regression(s), "
        f"{result.improvements} improvement(s)"
    )

    if result.has_regression:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# baseline-list command
# ---------------------------------------------------------------------------


@app.command(name="baseline-list")
def baseline_list() -> None:
    """List all saved baselines."""
    from agentbench.scanner.baseline import BaselineManager

    mgr = BaselineManager()
    baselines = mgr.list_baselines()

    if not baselines:
        console.print("[yellow]No baselines found.[/yellow]")
        console.print(
            "[dim]Capture one with: "
            "agentbench baseline-capture <url> --name <name>[/dim]"
        )
        return

    from rich import box
    from rich.table import Table

    table = Table(title="Saved Baselines", box=box.SIMPLE, show_header=True)
    table.add_column("Name", style="bold")
    table.add_column("Timestamp")
    table.add_column("Score", justify="right")
    table.add_column("Grade", justify="center")
    table.add_column("Behaviors", justify="right")

    for bl_name, bl_ts in baselines:
        try:
            bl = mgr.load(bl_name)
            table.add_row(
                bl_name,
                bl_ts[:19],  # trim to local time
                f"{bl.overall_score:.0f}",
                bl.overall_grade,
                str(bl.probe_count),
            )
        except Exception:
            table.add_row(bl_name, bl_ts[:19], "?", "?", "?")

    console.print(table)


# ---------------------------------------------------------------------------
# Workflow recorder — record, replay, gate
# ---------------------------------------------------------------------------


@app.command(name="record-workflow")
def record_workflow(
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
    """Record a multi-turn agent HTTP interaction as a reusable workflow.

    Connects to a live agent endpoint, starts an interactive session where
    you type messages and the agent responds.  Every turn, tool call, and
    timing measurement is captured into a workflow file that can be replayed
    for regression testing.

    Use /done or Ctrl+D to save, /cancel to discard.
    """
    from agentbench.cli.record import record_command

    record_command(url, name, format, header, timeout, api_key)


@app.command(name="replay")
def replay_workflow(
    workflow_name: str = typer.Argument(
        ..., help="Name of recorded workflow to replay"
    ),
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
        True, "--save-report/--no-save-report",
        help="Save report to disk",
    ),
) -> None:
    """Replay a recorded workflow and detect behavioral regressions.

    Loads a previously recorded workflow, re-sends every user message to
    the current agent, compares tool call sequences and response semantics,
    and outputs a pass/fail regression report.

    Exit code 0 = all good, 1 = regression detected.
    """
    from agentbench.cli.replay import replay_command

    replay_command(
        workflow_name, url, format, header, timeout,
        api_key, threshold, save_report,
    )


@app.command(name="gate")
def gate_ci(
    url: str = typer.Option(..., "--url", "-u", help="Agent endpoint URL"),
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
    """CI gate — replay all workflows and block deploys on regression.

    Loads every recorded workflow, replays against the current agent,
    and exits 1 if any workflow scores below the threshold.

    Use in CI/CD: agentbench gate --url https://my-agent.com/... -k $API_KEY
    """
    from agentbench.cli.gate import gate_command

    gate_command(
        url, format, header, timeout, api_key,
        threshold, workflow, save_reports,
    )


@app.command(name="dashboard")
def dashboard(
    port: int = typer.Option(8080, "--port", "-p", help="Server port"),
    host: str = typer.Option("127.0.0.1", "--host", help="Server host"),
    base_dir: str | None = typer.Option(
        None, "--dir", "-d", help="Base directory (default: cwd)"
    ),
    token: str | None = typer.Option(
        None, "--token", "-t", help="Bearer token for API authentication"
    ),
) -> None:
    """Start the workflow health dashboard.

    Launches a local web server with workflow health overview,
    regression timeline, and replay history.

    Open http://127.0.0.1:8080 in your browser.
    """
    from agentbench.cli.dashboard import dashboard_command

    dashboard_command(port, host, base_dir, token)


if __name__ == "__main__":
    app()
