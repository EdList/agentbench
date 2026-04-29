# 🔄 AgentBench

**Record agent workflows → Replay after changes → Block regressions.**

AgentBench is a behavioral regression testing framework for AI agents. Record live multi-turn interactions, replay them after deployments, and get automatic pass/fail verdicts when agent behavior changes.

[![Tests](https://img.shields.io/github/actions/workflow/status/EdList/agentbench/test.yml?branch=main&label=tests&logo=github)](https://github.com/EdList/agentbench/actions/workflows/test.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

---

## Quick Start

```bash
pip install agentbench-cli

# Record a workflow
agentbench record-workflow https://my-agent.com/v1/chat/completions \
  -n checkout-flow -k $API_KEY

# Replay after changes
agentbench replay checkout-flow -k $API_KEY

# CI gate — block deploys on regression
agentbench gate --url https://my-agent.com/v1/chat/completions -k $API_KEY
```

## How It Works

**Record → Replay → Diff → Gate**

1. **Record**: Capture live agent interactions (messages, tool calls, timing) into reusable workflow files
2. **Replay**: Re-send the same messages to the current agent, collect new responses
3. **Diff**: Compare tool call sequences, arguments, and response semantics — score each turn
4. **Gate**: Aggregate scores across all workflows, exit 1 on regression

## What You Get

### Recording

```
$ agentbench record-workflow https://my-agent.com/v1/chat/completions \
    -n checkout-flow -k $API_KEY

🎬 Recording workflow: checkout-flow
   Agent: https://my-agent.com/v1/chat/completions

You: Buy me a blue shirt, size M
Agent (1.2s): I'll search for that.
  🔧 product_search({"query": "blue shirt size M"})
  🔧 add_to_cart({"product_id": "SHIRT-M-BLUE"})

You: Check out
Agent (0.8s): Order confirmed! #12345
  🔧 payment({"amount": 29.99})

You: /done

✅ Workflow saved: checkout-flow (2 turns, 3 tool calls, 2.0s total)
```

### Replay

```
$ agentbench replay checkout-flow -k $API_KEY

🔄 Replaying workflow: checkout-flow
   Baseline: 2 turns, 3 tool calls

✅ PASSED  Score: 95% (threshold: 80%)  Turns: 2/2 passed

┌───┬──────────────────┬────────────────────────┬───────┬─────────┐
│ # │ User Message     │ Tools (orig→replay)     │ Score │ Verdict │
├───┼──────────────────┼────────────────────────┼───────┼─────────┤
│ 0 │ buy shirt        │ search → search         │  98%  │ PASS    │
│ 1 │ checkout         │ payment → payment       │  92%  │ PASS    │
└───┴──────────────────┴────────────────────────┴───────┴─────────┘
```

### CI Gate

```bash
# In your CI pipeline:
agentbench gate --url $AGENT_URL -k $API_KEY --threshold 0.8

# Exit code 0 = all clear, 1 = regression detected
```

### Dashboard

```bash
agentbench dashboard --port 8080
# Opens web UI with score timeline, workflow health, regression history
```

## Scoring

Each replayed turn is scored on 3 dimensions:

| Dimension | Weight | What it checks |
|-----------|--------|----------------|
| Tool sequence | 40% | Same tools called in same order |
| Tool arguments | 30% | Structural key-level comparison |
| Response semantics | 30% | String similarity (LLM judge optional) |

Overall threshold is configurable (default 80%). Turns with tool calls are weighted 2x.

## Project Structure

```
agentbench/
├── recorder/       # Phase 1: Capture live interactions
│   ├── workflow.py # Workflow, Turn, ToolCall data models
│   └── recorder.py # SessionRecorder (OpenAI + raw HTTP)
├── replayer/       # Phase 2: Replay + diff
│   ├── replayer.py # ReplayEngine
│   ├── diff.py     # WorkflowDiffer (3-axis comparison)
│   └── report.py   # ReplayReport with per-turn verdicts
├── gate/           # Phase 3: CI gate
│   └── runner.py   # GateRunner (aggregate pass/fail)
├── dashboard/      # Phase 4: Web UI
│   ├── app.py      # FastAPI endpoints
│   └── templates/  # Dashboard HTML
├── scanner/        # Agent security scanner (226 probes)
├── core/           # Test framework engine
└── server/         # Cloud API server
```

## Installation

```bash
# Core (recorder, replayer, gate, scanner)
pip install agentbench-cli

# With dashboard server
pip install 'agentbench-cli[server]'

# With adapter support
pip install 'agentbench-cli[langchain]'
pip install 'agentbench-cli[openai]'
```

## Requirements

- Python 3.11+
- An AI agent with an HTTP endpoint (OpenAI-compatible or raw JSON)

## Development

```bash
git clone https://github.com/EdList/agentbench.git
cd agentbench
pip install -e '.[dev]'

# Run tests
pytest tests/ -q

# Lint
ruff check .
```

## License

MIT
