"""Tests for the CLI."""

from typer.testing import CliRunner

from agentbench.cli import _scan_exit_code, app
from agentbench.discovery import AgentProfile
from agentbench.probes.base import Domain, DomainScore, Finding, Severity, Verdict
from agentbench.scanner.agent_runner import AgentScanResult

runner = CliRunner()


def _result_with_finding(finding: Finding | None = None) -> AgentScanResult:
    score = DomainScore(domain=Domain.SAFETY, score=100, passed=1, total=1)
    if finding and finding.verdict == Verdict.ERROR:
        score = DomainScore(domain=Domain.SAFETY, score=0, errored=1, total=1)
    return AgentScanResult(
        url="https://agent.test",
        timestamp="2026-01-01T00:00:00Z",
        duration_seconds=1,
        profile=AgentProfile(endpoint="https://agent.test"),
        probes_run=1,
        findings=[finding] if finding else [],
        domain_scores={"safety": score},
    )


def test_scan_exit_code_distinguishes_incomplete_vulnerable_and_clean_runs():
    error = Finding(
        probe_id="p1", domain=Domain.SAFETY, category="test",
        severity=Severity.CRITICAL, verdict=Verdict.ERROR,
        title="Request error", detail="network failed", evidence="TimeoutError",
    )
    critical = Finding(
        probe_id="p1", domain=Domain.SAFETY, category="test",
        severity=Severity.CRITICAL, verdict=Verdict.FAIL,
        title="Exploit succeeded", detail="unsafe", evidence="unsafe",
    )

    assert _scan_exit_code(_result_with_finding(error)) == 2
    assert _scan_exit_code(_result_with_finding(critical)) == 1
    assert _scan_exit_code(_result_with_finding()) == 0


class TestCLI:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output.lower()
        assert "discover" in result.output.lower()

    def test_scan_help(self):
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "url" in result.output.lower()
        assert "llm" in result.output.lower()

    def test_discover_help(self):
        result = runner.invoke(app, ["discover", "--help"])
        assert result.exit_code == 0
        assert "url" in result.output.lower()

    def test_probes_command(self):
        result = runner.invoke(app, ["probes"])
        assert result.exit_code == 0
        assert "Safety" in result.output or "safety" in result.output.lower()

    def test_scan_rejects_non_positive_timeout(self):
        result = runner.invoke(app, ["scan", "https://agent.test", "--timeout", "0"])
        assert result.exit_code == 1
        assert "Timeout must be positive" in result.output

    def test_scan_rejects_insecure_http_with_key(self):
        result = runner.invoke(
            app,
            ["scan", "http://agent.test", "--api-key", "secret"],
        )
        assert result.exit_code == 1
        assert "insecure" in result.output.lower()

    def test_scan_rejects_embedded_credentials(self):
        result = runner.invoke(
            app,
            ["scan", "https://user:pass@agent.test"],
        )
        assert result.exit_code == 1
        assert "credentials" in result.output.lower()

    def test_llm_analyzer_requires_separate_analyzer_key(self):
        result = runner.invoke(
            app,
            [
                "scan", "https://agent.test", "--api-key", "target-only-key",
                "--llm-analyzer",
            ],
            env={"ANALYZER_API_KEY": ""},
        )

        assert result.exit_code == 1
        assert "separate analyzer key" in result.output.lower()
        assert "--analyzer-key" in result.output

    def test_scan_output_write_failure_exits_incomplete(self, monkeypatch, tmp_path):
        import agentbench.scanner.agent_runner as agent_runner

        async def clean_scan(*args, **kwargs):
            return _result_with_finding()

        monkeypatch.setattr(agent_runner, "run_agent_scan", clean_scan)
        missing_parent = tmp_path / "missing" / "result.json"

        result = runner.invoke(
            app,
            ["scan", "https://agent.test", "--output", str(missing_parent)],
        )

        assert result.exit_code == 2
        assert "error saving" in result.output.lower()
