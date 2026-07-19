# Security Policy

## Supported versions

AgentBench is pre-1.0. Security fixes are applied to the latest released version and the `main` branch.

## Reporting a vulnerability

Please use [GitHub private vulnerability reporting](https://github.com/EdList/agentbench/security/advisories/new). Do not disclose exploitable details in a public issue.

Include:

- affected AgentBench version or commit;
- operating system and Python version;
- minimal reproduction;
- expected and observed behavior;
- impact, including whether credentials, target-agent data, or scan integrity are affected.

We will acknowledge a complete report as soon as practical, validate it privately, and coordinate disclosure after a fix is available.

## Scanner safety

AgentBench is an active black-box scanner. Use it only against systems you own or are explicitly authorized to test. Prefer staging agents with sandboxed tools and least-privilege credentials. A vulnerable target may perform the actions requested by a probe.

The optional LLM analyzer requires a separate analyzer credential. AgentBench never substitutes the target agent credential, and it redacts common credentials and PII before sending evidence to the analyzer provider. Avoid enabling third-party analysis when policy prohibits external processing.

## Security boundaries

A complete scan indicates that every generated probe received an analyzable response; it is not a guarantee that the target is secure. Exit code `2`, `scan_complete=false`, or any probe error means the scan is inconclusive and must not be treated as a pass.
