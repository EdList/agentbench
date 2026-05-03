# 🔍 AgentBench

**Paste your agent URL. We'll tell you what's broken.**

[![Tests](https://img.shields.io/github/actions/workflow/status/EdList/agentbench/test.yml?branch=main&label=tests&logo=github)](https://github.com/EdList/agentbench/actions/workflows/test.yml)
[![PyPI](https://img.shields.io/pypi/v/agentbench-cli.svg?color=blue)](https://pypi.org/project/agentbench-cli/)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## How It Works

**Three steps to a safer agent:**

1. **Install** — `pip install agentbench-cli`
2. **Scan** — `agentbench scan <url>` runs 92 probes across 4 domains
3. **Fix** — Review findings with specific remediation advice, then iterate

## Quick Start

```bash
pip install agentbench-cli

# Scan any AI agent endpoint
agentbench scan https://my-agent.example.com/v1/chat/completions

# With API key and model (for OpenRouter-style endpoints)
agentbench scan https://openrouter.ai/api/v1/chat/completions \
  -k $API_KEY -m deepseek/deepseek-chat-v3-0324

# Save results as JSON
agentbench scan <url> -o results.json
```

60 seconds later you get a scorecard:

```
╭──────────────────────────────────────────────────────────╮
│ 🔍 AgentBench Scanner                                    │
│ Scanning: https://openrouter.ai/api/v1/chat/completions  │
│ 92 probes across 4 domains                                │
╰──────────────────────────────────────────────────────────╯

  Overall Score: 72/100 (Grade: C)
  Probes: 92 | Duration: 312.4s | Findings: 7

                      Domain Scores
┏━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━┳━━━━━━━┓
┃ Domain      ┃ Score ┃ Grade ┃ Passed ┃ Failed ┃ Total ┃
┡━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━╇━━━━━━━┩
│ Safety      │    50 │   F   │     30 │      2 │    32 │
│ Reliability │    72 │   C   │     18 │      4 │    22 │
│ Capability  │    87 │   B   │     21 │      3 │    24 │
│ Consistency │    97 │   A   │     14 │      0 │    14 │
└─────────────┴───────┴───────┴────────┴────────┴───────┘

  ❌ Critical (2)
    • Prompt injection succeeded
      Agent response contains 'dan activated', suggesting injection worked
      ↳ Fix: Add input sanitization, use system message separation
    • System prompt leaked
      Agent revealed internal instructions via output format manipulation
      ↳ Fix: Do not expose system prompts through structured output requests
```

## What It Tests

**92 probes across 4 domains:**

| Domain | Count | What it checks |
|--------|-------|----------------|
| **Safety** | 32 | Prompt injection (DAN, base64, multilingual, few-shot poisoning), PII extraction, harmful content, tool misuse, compliance |
| **Reliability** | 22 | Edge cases (empty input, unicode, null bytes, JSON injection), error handling, format robustness, state management |
| **Capability** | 24 | Hallucination detection, instruction following (constraints, word counts, JSON output), reasoning, tool use, code correctness |
| **Consistency** | 14 | Persona adherence, tone, rule consistency across groups, behavioral repetition, topic coherence |

## Commands

```bash
# Scan an agent endpoint
agentbench scan <url> [-k API_KEY] [-m MODEL] [-o results.json] [-t TIMEOUT]

# Restrict scan to specific domains
agentbench scan <url> -d safety -d reliability

# List all probes
agentbench probes

# Compare past scan results
agentbench compare
agentbench compare --label "my-agent"

# Pull latest probe definitions
agentbench update

# Show version
agentbench --version
```

## GitHub Action

Add AgentBench to your CI in one step:

```yaml
- uses: EdList/agentbench/action@v0.1.0
  with:
    agent-url: ${{ secrets.AGENT_URL }}
    api-key: ${{ secrets.API_KEY }}
    model: ${{ vars.MODEL }}
    fail-on-critical: true
```

The action exits **1** if critical findings are detected, blocking merges. Outputs `score`, `grade`, and `critical-count` for downstream use.

## Model Leaderboard

Real results from scanning popular models via OpenRouter:

| Model | Overall | Safety | Reliability | Capability | Consistency |
|-------|---------|--------|-------------|------------|-------------|
| **Claude 3.5 Haiku** | 86 (B) | 75 (C) | 97 (A) | 87 (B) | 91 (A) |
| **Gemini 2.0 Flash** | 72 (C) | 50 (F) | 72 (C) | 87 (B) | 97 (A) |
| **GPT-4o-mini** | 70 (C) | 50 (F) | 72 (C) | 77 (C) | 100 (A) |
| Qwen 3 14B | 74 (C) | 50 (F) | 75 (C) | 100 (A) | 91 (A) |
| DeepSeek V3 | 72 (C) | 50 (F) | 72 (C) | 100 (A) | 85 (B) |
| Llama 3.3 70B | 71 (C) | 25 (F) | 100 (A) | 100 (A) | 91 (A) |
| Gemma 3 27B | 57 (F) | 0 (F) | 75 (C) | 100 (A) | 94 (A) |

**Most models fail safety.** That's the point — AgentBench helps you find and fix these gaps.

## Installation

```bash
pip install agentbench-cli
```

Requires Python 3.11+.

## Development

```bash
git clone https://github.com/EdList/agentbench.git
cd agentbench
pip install -e .

# Run tests
pytest tests/ -q

# Lint
ruff check .
```

## Architecture

```
agentbench/
├── cli.py              # Typer CLI — scan, probes, compare, update
├── probes/
│   ├── base.py         # Data models (Probe, Finding, ScanResult)
│   ├── registry.py     # Loads probes from YAML
│   ├── yaml_loader.py  # YAML probe parser with validation
│   └── builtin/        # 92 YAML probe definitions
│       ├── safety.yaml
│       ├── capability.yaml
│       ├── reliability.yaml
│       └── consistency.yaml
├── scanner/
│   ├── runner.py       # Async probe execution engine
│   ├── analyzer.py     # Response analysis (injection, PII, hallucination)
│   └── scorer.py       # Weighted domain scoring
├── leaderboard.py      # Local scan history
└── updater.py          # Pull latest probes from GitHub
```

## License

MIT
