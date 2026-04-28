# AgentBench — Path A: Make the CLI Genuinely Useful

**Goal:** An open-source CLI tool that developers actually use to test their AI agents.
**Timeline:** 3 months (May–July 2026)
**Success metric:** 100+ GitHub stars, real users running `agentbench scan` weekly

---

## Phase 1: Make It Work For Real (Week 1-2)

*Right now the scanner has 38 prompts and string-matching analysis. Let's make it actually useful against real agents.*

### 1.1 Validate against real endpoints
- [ ] Test `agentbench scan` against OpenAI GPT-4o endpoint
- [ ] Test against Anthropic Claude endpoint (if accessible)
- [ ] Test against a LangChain agent
- [ ] Test against a simple Flask/FastAPI agent
- [ ] Document what breaks, what scores wrong, what's missing
- **Deliverable:** A test matrix showing real results from real agents

### 1.2 Expand probes from 38 → 150+
- [ ] Safety: 8 → 40 prompts (injection variants, PII extraction, jailbreaks)
- [ ] Capability: 8 → 30 prompts (tool use, multi-step reasoning, code gen)
- [ ] Edge cases: 8 → 30 prompts (unicode, empty, adversarial formats, rate limits)
- [ ] Persona/consistency: 6 → 25 prompts (role adherence, instruction following)
- [ ] Robustness: 8 → 25 prompts (consistency, retry behavior, timeout handling)
- [ ] NEW: Conversation state — 15 prompts (multi-turn, context retention, session drift)
- **Deliverable:** 150+ probes covering real-world agent failure modes

### 1.3 Fix analysis to go beyond string matching
- [ ] Add response length analysis (too short = incomplete, too long = hallucination)
- [ ] Add structured output parsing (JSON validation, schema compliance)
- [ ] Add tool call validation (did the agent use the right tools in the right order?)
- [ ] Add PII detection regex patterns (SSN, credit card, email, phone)
- **Deliverable:** Analyzer that catches real issues, not just keyword matches

---

## Phase 2: Regression Testing (Week 3-4)

*This is the killer feature. "Did my agent change?" is the question people will pay for.*

### 2.1 Baseline capture
- [ ] `agentbench scan <url> --save-baseline <name>` — saves full probe results + scores
- [ ] Baselines stored in `.agentbench/baselines/` as JSON
- [ ] Include: probe prompts, responses, behaviors detected, domain scores, overall score

### 2.2 Diffing
- [ ] `agentbench scan <url> --baseline <name>` — compare current scan against baseline
- [ ] Show: score changes (±N per domain), new behaviors, removed behaviors, changed responses
- [ ] Exit code: 0 if within tolerance, 1 if regression detected
- [ ] `--threshold` flag: max acceptable score delta before failing

### 2.3 CI integration
- [ ] Write a GitHub Action that runs `agentbench scan --baseline main` on PRs
- [ ] Post scan diff as PR comment (score changes, new issues)
- [ ] Configurable pass/fail thresholds

---

## Phase 3: Ship It (Week 5-6)

### 3.1 PyPI package
- [ ] Clean up `pyproject.toml` for public publishing
- [ ] Add proper CLI entry point: `pip install agentbench` → `agentbench` command
- [ ] Add `--version`, `--help` with examples
- [ ] Test on a fresh Python 3.11+ environment

### 3.2 Documentation
- [ ] README with 30-second quickstart
- [ ] Tutorial 1: "Scan your first agent in 60 seconds"
- [ ] Tutorial 2: "Set up regression testing in CI"
- [ ] Tutorial 3: "Custom probes for your agent"
- [ ] API reference for probe configuration

### 3.3 Distribution
- [ ] Post on r/LocalLLaMA, r/MachineLearning, Hacker News
- [ ] Write a blog post: "We tested 5 popular AI agents. Here's what broke."
- [ ] Submit to Awesome lists (awesome-ai-agents, awesome-testing)

---

## Phase 4: Grow (Week 7-12)

### 4.1 Custom probes
- [ ] YAML probe config: users define their own test prompts and expectations
- [ ] `agentbench scan --probes ./my-probes.yaml`
- [ ] Domain-specific probe packs (customer-support, coding, sales, etc.)

### 4.2 Community
- [ ] Contributing guide
- [ ] Issue templates for probe suggestions
- [ ] Discord/Slack community
- [ ] Regular "probe pack" releases

---

## What We're NOT Building (Yet)
- Web dashboard
- User accounts / auth
- Cloud API / SaaS
- Team management
- Enterprise features

These come after the CLI has real users.

---

*Last updated: 2026-04-28*
