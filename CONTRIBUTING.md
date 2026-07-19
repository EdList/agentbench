# Contributing to AgentBench

Thanks for helping make agent security testing more reliable.

## Development setup

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -e ".[dev]"
```

## Required checks

Run all checks before opening a pull request:

```bash
pytest -q
ruff check .
mypy agentbench
python -m build
twine check dist/*
```

Changes to analyzer behavior, network handling, scoring, discovery, or exit codes must include a regression test that fails before the production change. Prefer concrete behavioral evidence over broad keyword matching.

## Probe and analyzer guidelines

- A refusal plus concrete leak/action evidence is still a failure.
- Request, transport, and required follow-up errors are inconclusive—not passes.
- Critical findings must never be suppressed by the optional LLM analyzer.
- Never reuse target credentials for external services.
- Redact credentials and PII before external analysis.
- Keep response, discovery, tool, and generated-probe bounds explicit.
- Use reserved `.invalid` addresses in probes and tests; never target a real third party.

## Pull requests

Keep changes focused and describe:

1. the user-visible problem;
2. the reproduction or failing test;
3. the fix and security implications;
4. verification commands and results;
5. documentation or compatibility impact.

By contributing, you agree that your contribution is licensed under the repository's MIT License.
