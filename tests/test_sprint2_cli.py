"""Tests for Sprint 2 CLI features: watch, report, list commands."""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest
from typer.testing import CliRunner

from agentbench.cli.main import app
from agentbench.cli.report import generate_html_report, _render_html

runner = CliRunner()


# ─── Fixtures ───

@pytest.fixture()
def sample_suite(tmp_path):
    """Create a minimal test suite file."""
    test_file = tmp_path / "test_demo.py"
    test_file.write_text(textwrap.dedent("""\
        from agentbench.core.test import AgentTest
        from agentbench.adapters.raw_api import RawAPIAdapter

        def echo_agent(prompt, context=None):
            return {"response": "echo: " + prompt, "steps": []}

        class DemoTest(AgentTest):
            agent = "demo"
            adapter = RawAPIAdapter(func=echo_agent)

            def test_basic(self):
                \"\"\"Agent responds to basic input.\"\"\"
                result = self.run("hello")
                expect(result).to_complete()

            def test_fast(self):
                \"\"\"Agent completes quickly.\"\"\"
                result = self.run("hi")
                expect(result).to_complete_within(steps=5)
    """))
    return tmp_path


@pytest.fixture()
def sample_json_report(tmp_path):
    """Create a sample JSON report file for HTML report tests."""
    report = {
        "total_tests": 4,
        "passed": 3,
        "failed": 1,
        "duration_ms": 1234.5,
        "suites": [
            {
                "name": "CheckoutTest",
                "passed": 2,
                "failed": 1,
                "tests": [
                    {
                        "name": "test_completes",
                        "passed": True,
                        "duration_ms": 200.0,
                        "error": None,
                        "assertions": [
                            {"passed": True, "message": "completed", "type": "to_complete"},
                        ],
                    },
                    {
                        "name": "test_fast",
                        "passed": True,
                        "duration_ms": 100.0,
                        "error": None,
                        "assertions": [
                            {"passed": True, "message": "within 5 steps", "type": "to_complete_within"},
                        ],
                    },
                    {
                        "name": "test_no_errors",
                        "passed": False,
                        "duration_ms": 300.0,
                        "error": "AssertionError: expected no errors",
                        "assertions": [
                            {"passed": True, "message": "completed", "type": "to_complete"},
                            {"passed": False, "message": "no errors", "type": "to_have_no_errors"},
                        ],
                    },
                ],
            },
            {
                "name": "SearchTest",
                "passed": 1,
                "failed": 0,
                "tests": [
                    {
                        "name": "test_search",
                        "passed": True,
                        "duration_ms": 150.0,
                        "error": None,
                        "assertions": [
                            {"passed": True, "message": "completed", "type": "to_complete"},
                        ],
                    },
                ],
            },
        ],
    }
    report_path = tmp_path / "report.json"
    report_path.write_text(json.dumps(report, indent=2))
    return report_path


# ─── List Command Tests ───

class TestListCommand:
    def test_lists_suites_and_methods(self, sample_suite):
        result = runner.invoke(app, ["list", str(sample_suite)])
        assert result.exit_code == 0
        assert "DemoTest" in result.output
        assert "test_basic" in result.output
        assert "test_fast" in result.output

    def test_shows_docstrings(self, sample_suite):
        result = runner.invoke(app, ["list", str(sample_suite)])
        assert result.exit_code == 0
        assert "Agent responds to basic input" in result.output

    def test_summary_counts(self, sample_suite):
        result = runner.invoke(app, ["list", str(sample_suite)])
        assert result.exit_code == 0
        assert "1 suite(s)" in result.output
        assert "2 test method(s)" in result.output

    def test_filter_flag(self, sample_suite):
        result = runner.invoke(app, ["list", str(sample_suite), "--filter", "basic"])
        assert result.exit_code == 0
        assert "test_basic" in result.output
        # test_fast should be filtered out
        assert "test_fast" not in result.output

    def test_empty_directory(self, tmp_path):
        result = runner.invoke(app, ["list", str(tmp_path)])
        assert result.exit_code == 0
        assert "No test suites found" in result.output

    def test_single_file(self, sample_suite):
        test_file = sample_suite / "test_demo.py"
        result = runner.invoke(app, ["list", str(test_file)])
        assert result.exit_code == 0
        assert "DemoTest" in result.output

    def test_filter_no_match(self, sample_suite):
        result = runner.invoke(app, ["list", str(sample_suite), "--filter", "nonexistent"])
        assert result.exit_code == 0
        assert "0 test method(s)" in result.output


# ─── Report Command Tests ───

class TestReportCommand:
    def test_generates_html_file(self, sample_json_report, tmp_path):
        output = tmp_path / "report.html"
        result = runner.invoke(app, ["report", str(sample_json_report), "--output", str(output)])
        assert result.exit_code == 0
        assert output.exists()
        content = output.read_text()
        assert "<!DOCTYPE html>" in content

    def test_default_output_path(self, sample_json_report):
        result = runner.invoke(app, ["report", str(sample_json_report)])
        assert result.exit_code == 0
        expected = sample_json_report.with_suffix(".html")
        assert expected.exists()
        content = expected.read_text()
        assert "AgentBench Report" in content

    def test_missing_json_file(self, tmp_path):
        fake_path = tmp_path / "nonexistent.json"
        result = runner.invoke(app, ["report", str(fake_path)])
        assert result.exit_code == 1
        assert "not found" in result.output


class TestReportHTMLContent:
    def test_contains_summary(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "3" in content  # passed count
        assert "1" in content  # failed count
        assert "1234" in content or "1.2s" in content  # duration

    def test_contains_suite_names(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "CheckoutTest" in content
        assert "SearchTest" in content

    def test_contains_test_names(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "test_completes" in content
        assert "test_fast" in content
        assert "test_no_errors" in content
        assert "test_search" in content

    def test_pass_fail_colors(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        # Should have green for passed, red for failed
        assert "✅" in content or "pass" in content.lower()
        assert "❌" in content or "fail" in content.lower()

    def test_error_display(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "AssertionError" in content

    def test_assertion_details(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "to_complete" in content
        assert "to_have_no_errors" in content

    def test_self_contained_no_external_deps(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        # No external CSS/JS links
        assert 'href="http' not in content
        assert 'src="http' not in content
        # All styles are inline
        assert "<style>" in content

    def test_expandable_suites(self, sample_json_report, tmp_path):
        output = tmp_path / "out.html"
        generate_html_report(sample_json_report, output)
        content = output.read_text()
        assert "suite-header" in content
        assert "suite-body" in content
        assert "collapsed" in content
        # Toggle JS
        assert "addEventListener" in content or "toggle" in content


# ─── _render_html unit tests ───

class TestRenderHTML:
    def test_all_passed_status(self):
        data = {"total_tests": 2, "passed": 2, "failed": 0, "duration_ms": 500, "suites": []}
        html_output = _render_html(data)
        assert "PASSED" in html_output
        assert "#22c55e" in html_output  # green

    def test_some_failed_status(self):
        data = {"total_tests": 2, "passed": 1, "failed": 1, "duration_ms": 500, "suites": []}
        html_output = _render_html(data)
        assert "FAILED" in html_output
        assert "#ef4444" in html_output  # red

    def test_empty_report(self):
        data = {"total_tests": 0, "passed": 0, "failed": 0, "duration_ms": 0, "suites": []}
        html_output = _render_html(data)
        assert "PASSED" in html_output
        assert "<!DOCTYPE html>" in html_output

    def test_html_escaping(self):
        data = {
            "total_tests": 1,
            "passed": 0,
            "failed": 1,
            "duration_ms": 100,
            "suites": [{
                "name": "<script>alert('xss')</script>",
                "passed": 0,
                "failed": 1,
                "tests": [{
                    "name": "test_evil",
                    "passed": False,
                    "duration_ms": 100,
                    "error": "<img onerror=alert(1)>",
                    "assertions": [],
                }],
            }],
        }
        html_output = _render_html(data)
        # Suite name and error should be escaped (the template has its own <script> tags for JS)
        assert "&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;" in html_output
        assert "&lt;img onerror=alert(1)&gt;" in html_output


# ─── Watch Command Tests (import / arg validation only) ───

class TestWatchCommand:
    def test_nonexistent_path_exits(self, tmp_path):
        result = runner.invoke(app, ["watch", str(tmp_path / "nope")])
        assert result.exit_code == 1
        assert "does not exist" in result.output

    def test_watchdog_import_error_message(self, tmp_path, monkeypatch):
        """Verify graceful error when watchdog is not installed."""
        import importlib
        # Patch the watchdog import to fail
        import sys
        watchdog_mod = sys.modules.pop("watchdog", None)
        sys.modules["watchdog"] = None  # type: ignore
        sys.modules["watchdog.observers"] = None  # type: ignore
        try:
            result = runner.invoke(app, ["watch", str(tmp_path)])
            assert result.exit_code == 1
            assert "watchdog" in result.output.lower()
        finally:
            # Restore
            sys.modules.pop("watchdog", None)
            sys.modules.pop("watchdog.observers", None)
            if watchdog_mod:
                sys.modules["watchdog"] = watchdog_mod


# ─── Integration: run → report pipeline ───

class TestRunReportPipeline:
    def test_run_generates_valid_json_for_report(self, sample_suite, tmp_path):
        """Run with --report, then generate HTML from that report."""
        json_report = tmp_path / "results.json"
        result = runner.invoke(app, ["run", str(sample_suite), "--report", str(json_report)])
        # exit code is 0 if all pass
        assert json_report.exists()

        data = json.loads(json_report.read_text())
        assert "total_tests" in data
        assert "suites" in data

        # Now generate HTML
        html_report = tmp_path / "results.html"
        result2 = runner.invoke(app, ["report", str(json_report), "--output", str(html_report)])
        assert result2.exit_code == 0
        assert html_report.exists()
        html_content = html_report.read_text()
        assert "<!DOCTYPE html>" in html_content
