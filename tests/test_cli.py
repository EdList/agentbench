"""Tests for the CLI."""

from typer.testing import CliRunner

from agentbench.cli import app

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
        assert "discover" in result.output.lower()

    def test_scan_help(self):
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "url" in result.output.lower()
        assert "--llm-analyzer" in result.output

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
