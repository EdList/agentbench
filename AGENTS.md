# AGENTS.md — AgentBench

Guide for AI coding assistants working in this repository. Also see `README.md` for the user-facing product overview and `CONTRIBUTING.md` for PR conventions.

## What this is

AgentBench is a black-box security scanner for AI **agent systems** (not models). Point it at an agent endpoint (MCP, OpenAI-compatible, or OpenAPI); it discovers exposed tool schemas, generates targeted probes, and flags exploitable response behavior. Published on PyPI as `agentbench-cli`.

## Stack

- **Language**: Python 3.11+
- **Build**: hatch (`hatch build`)
- **Test**: pytest with pytest-asyncio (`pytest tests/`)
- **Lint**: ruff, line-length 100, rules E/F/I/N/W/UP (E501 ignored — probe prompts are intentionally long)
- **Publish**: twine → PyPI (`agentbench-cli`)
- **Entry point**: `agentbench = agentbench.cli:app`

## Layout

```
agentbench/          # core SDK
  cli/               # Typer CLI — scan, scan-detailed, scan-report
  core/              # engine, fixtures, sandbox, runner
  adapters/          # MCP, OpenAI, RawAPI/HTTP
  server/            # FastAPI server + routes (if running hosted)
probes/              # probe templates by category
tests/               # pytest suite (~140 tests)
examples/            # sample projects and demos
docs/                # documentation
action.yml           # GitHub Action manifest
```

## Common commands

```bash
# Install for development
python -m pip install -e ".[dev]"

# Run tests
pytest tests/ -q

# Lint
ruff check .

# Build and publish
hatch build
twine upload dist/*
```

## Conventions

- **Behavior contracts over snapshots.** Tests assert invariants, not frozen values. Don't write change-detector tests.
- **E501 is ignored** — probe prompts are intentionally long natural-language strings.
- **Adapters are peers.** MCP, OpenAI, and RawAPI/HTTP adapters are all first-class. Don't favor one in core code.
- **Black-box only.** AgentBench observes externally visible behavior; don't add white-box instrumentation to core.

## Safety

- AgentBench is an **active** scanner. Some probes ask agents to use tools. Only run against systems you own or are authorized to test.
- Don't add probes that could cause real-world side effects (emails, payments, destructive DB ops) without clear warnings and opt-in.

## Agent workflow

- For bug fixes: reproduce on current `main`, point to exact line, fix the whole bug class (sibling paths included).
- For new probes: add to `probes/` with a clear risk category, test it against the mock adapter in `tests/`.
- Run `ruff check . && pytest tests/ -q` before any commit.
