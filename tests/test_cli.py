"""Tests for the CLI."""

from typer.testing import CliRunner

from agentbench.cli import app

runner = CliRunner()


class TestCLI:
    def test_version(self):
        result = runner.invoke(app, ["--version"])
        assert result.exit_code == 0
        assert "1.0.0" in result.output

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
