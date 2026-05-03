"""Tests for the CLI."""

from datetime import UTC, datetime
from pathlib import Path

from typer.testing import CliRunner

from agentbench import cli
from agentbench.cli import app
from agentbench.probes.base import ScanResult

runner = CliRunner()


class TestCLI:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "0.1.0" in result.output

    def test_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "scan" in result.output.lower()

    def test_scan_help(self):
        result = runner.invoke(app, ["scan", "--help"])
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

    def test_scan_rejects_invalid_domain(self):
        result = runner.invoke(app, ["scan", "https://agent.test", "--domain", "bogus"])
        assert result.exit_code == 1
        assert "Invalid domain 'bogus'" in result.output

    def test_scan_output_write_failure_exits_nonzero(self, monkeypatch):
        async def fake_run_scan(*args, **kwargs):
            return ScanResult(
                url="https://agent.test",
                overall_score=100,
                domain_scores={},
                findings=[],
                duration_seconds=0.0,
                probes_run=0,
                timestamp=datetime.now(UTC).isoformat(),
            )

        monkeypatch.setattr(cli, "run_scan", fake_run_scan)
        monkeypatch.setattr(cli, "_render_scorecard", lambda result: None)

        import agentbench.leaderboard as leaderboard

        monkeypatch.setattr(leaderboard, "add_scan_result", lambda *args, **kwargs: None)

        with runner.isolated_filesystem():
            Path("results-dir").mkdir()
            result = runner.invoke(
                app,
                ["scan", "https://agent.test", "--output", "results-dir"],
            )

        assert result.exit_code == 1
        assert "Error saving to results-dir" in result.output
