"""Tests for AgentBench CLI — scaffold_project and _find_adapter_in_path."""

from __future__ import annotations

import json
import textwrap

import pytest
from typer.testing import CliRunner

from agentbench.cli.main import _find_adapter_in_path, app
from agentbench.cli.scaffold import TEMPLATES, scaffold_project
from agentbench.core.test import AgentTest

runner = CliRunner()


def _write_agent_test_file(
    tmp_path, filename: str, agent_name: str, response_prefix: str
) -> None:
    test_file = tmp_path / filename
    _agent_return = (
        f'{{"response": "{response_prefix}: " + prompt, '
        '"steps": [{"action": "llm_response", '
        f'"response": "{response_prefix}: " + prompt}}]}}'
    )
    test_file.write_text(
        textwrap.dedent(f"""\
        from agentbench.core.test import AgentTest
        from agentbench.adapters.raw_api import RawAPIAdapter

        def _agent_fn(prompt, context=None):
            return {_agent_return}

        class {agent_name.title().replace("-", "").replace("_", "")}Test(AgentTest):
            agent = "{agent_name}"
            adapter = RawAPIAdapter(func=_agent_fn)
    """)
    )


# ─── scaffold_project ───


class TestScaffoldProject:
    def test_creates_raw_api_project(self, tmp_path):
        project_dir = tmp_path / "my-tests"
        scaffold_project(project_dir, "my-tests", "raw_api")

        assert project_dir.exists()
        assert (project_dir / "test_agent.py").exists()
        assert (project_dir / "agentbench.yaml").exists()
        assert (project_dir / "requirements.txt").exists()
        assert (project_dir / ".agentbench" / "trajectories").exists()

    def test_creates_langchain_project(self, tmp_path):
        project_dir = tmp_path / "lc-tests"
        scaffold_project(project_dir, "lc-tests", "langchain")

        assert project_dir.exists()
        assert (project_dir / "test_agent.py").exists()
        assert (project_dir / "agentbench.yaml").exists()

    def test_unknown_framework_is_rejected(self, tmp_path):
        project_dir = tmp_path / "fallback"
        with pytest.raises(ValueError, match="Unsupported scaffold framework"):
            scaffold_project(project_dir, "fallback", "nonexistent_framework")

    def test_config_file_has_correct_adapter(self, tmp_path):
        project_dir = tmp_path / "cfg-test"
        scaffold_project(project_dir, "cfg-test", "langchain")

        config = (project_dir / "agentbench.yaml").read_text()
        assert "default_adapter: langchain" in config

    def test_config_file_raw_api(self, tmp_path):
        project_dir = tmp_path / "cfg-raw"
        scaffold_project(project_dir, "cfg-raw", "raw_api")

        config = (project_dir / "agentbench.yaml").read_text()
        assert "default_adapter: raw_api" in config

    def test_config_file_unknown_framework_is_rejected(self, tmp_path):
        project_dir = tmp_path / "cfg-fallback"
        with pytest.raises(ValueError, match="Unsupported scaffold framework"):
            scaffold_project(project_dir, "cfg-fallback", "unknown")

    def test_requirements_txt(self, tmp_path):
        project_dir = tmp_path / "req-test"
        scaffold_project(project_dir, "req-test", "raw_api")

        reqs = (project_dir / "requirements.txt").read_text()
        assert "agentbench" in reqs

    def test_raw_api_template_content(self, tmp_path):
        project_dir = tmp_path / "content-test"
        scaffold_project(project_dir, "content-test", "raw_api")

        content = (project_dir / "test_agent.py").read_text()
        assert "AgentTest" in content
        assert "RawAPIAdapter" in content
        assert "my-agent" in content

    def test_langchain_template_content(self, tmp_path):
        project_dir = tmp_path / "lc-content"
        scaffold_project(project_dir, "lc-content", "langchain")

        content = (project_dir / "test_agent.py").read_text()
        assert "AgentTest" in content
        assert "LangChainAdapter" in content or "langchain" in content

    def test_trajectories_dir_created(self, tmp_path):
        project_dir = tmp_path / "traj-dir"
        scaffold_project(project_dir, "traj-dir", "raw_api")
        traj_dir = project_dir / ".agentbench" / "trajectories"
        assert traj_dir.exists()
        assert traj_dir.is_dir()

    def test_idempotent_create(self, tmp_path):
        project_dir = tmp_path / "idempotent"
        scaffold_project(project_dir, "idempotent", "raw_api")
        # Call again — should not raise
        scaffold_project(project_dir, "idempotent", "raw_api")
        assert (project_dir / "test_agent.py").exists()


# ─── TEMPLATES dict ───


class TestTemplates:
    def test_raw_api_template_exists(self):
        assert "raw_api" in TEMPLATES
        assert "test_agent.py" in TEMPLATES["raw_api"]

    def test_langchain_template_exists(self):
        assert "langchain" in TEMPLATES
        assert "test_agent.py" in TEMPLATES["langchain"]


class TestCliHelp:
    def test_adversarial_command_is_labeled_experimental(self):
        result = runner.invoke(app, ["adversarial", "--help"])

        assert result.exit_code == 0
        assert "experimental" in result.output.lower()


# ─── _find_adapter_in_path ───


class TestFindAdapterInPath:
    def test_discovers_agent_in_file(self, tmp_path):
        """Write a test file with an AgentTest subclass and find it."""
        test_file = tmp_path / "test_my_agent.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest
            from agentbench.adapters.raw_api import RawAPIAdapter

            def my_agent(prompt, context=None):
                return {"response": "echo: " + prompt, "steps": []}

            class FoundTest(AgentTest):
                agent = "found-agent"
                adapter = RawAPIAdapter(func=my_agent)
        """)
        )

        result = _find_adapter_in_path(test_file)
        assert result is not None
        assert isinstance(result, AgentTest)
        assert result.agent == "found-agent"

    def test_discovers_agent_in_directory(self, tmp_path):
        test_file = tmp_path / "test_discovery.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest
            from agentbench.adapters.raw_api import RawAPIAdapter

            def agent_fn(prompt, context=None):
                return {"response": "ok", "steps": []}

            class DirTest(AgentTest):
                agent = "dir-agent"
                adapter = RawAPIAdapter(func=agent_fn)
        """)
        )

        result = _find_adapter_in_path(tmp_path)
        assert result is not None
        assert result.agent == "dir-agent"

    def test_no_test_files_returns_none(self, tmp_path):
        other_file = tmp_path / "utils.py"
        other_file.write_text("def helper(): pass")
        assert _find_adapter_in_path(tmp_path) is None

    def test_nonexistent_path_returns_none(self, tmp_path):
        fake_path = tmp_path / "does_not_exist.py"
        assert _find_adapter_in_path(fake_path) is None

    def test_file_without_adapter_returns_none(self, tmp_path):
        test_file = tmp_path / "test_no_adapter.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest

            class NoAdapterTest(AgentTest):
                agent = "no-adapter"
                adapter = None
        """)
        )

        result = _find_adapter_in_path(test_file)
        assert result is None

    def test_non_python_file_returns_none(self, tmp_path):
        txt_file = tmp_path / "notes.txt"
        txt_file.write_text("not python")
        assert _find_adapter_in_path(txt_file) is None

    def test_skips_base_class(self, tmp_path):
        """Should not return AgentTest itself."""
        test_file = tmp_path / "test_base.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest
            # Only the base class — should be skipped
        """)
        )

        result = _find_adapter_in_path(test_file)
        assert result is None

    def test_handles_syntax_error_gracefully(self, tmp_path):
        bad_file = tmp_path / "test_broken.py"
        bad_file.write_text("def broken(:\n  pass")

        # Should not raise, returns None
        assert _find_adapter_in_path(bad_file) is None

    def test_multiple_test_classes_finds_first(self, tmp_path):
        test_file = tmp_path / "test_multi.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest
            from agentbench.adapters.raw_api import RawAPIAdapter

            def fn1(p, c=None):
                return {"response": "one", "steps": []}
            def fn2(p, c=None):
                return {"response": "two", "steps": []}

            class FirstTest(AgentTest):
                agent = "first"
                adapter = RawAPIAdapter(func=fn1)

            class SecondTest(AgentTest):
                agent = "second"
                adapter = RawAPIAdapter(func=fn2)
        """)
        )

        result = _find_adapter_in_path(test_file)
        assert result is not None
        # Should find one of them
        assert result.agent in ("first", "second")

    def test_can_select_exact_agent_name(self, tmp_path):
        test_file = tmp_path / "test_multi.py"
        test_file.write_text(
            textwrap.dedent("""\
            from agentbench.core.test import AgentTest
            from agentbench.adapters.raw_api import RawAPIAdapter

            def fn1(p, c=None):
                return {"response": "one", "steps": []}
            def fn2(p, c=None):
                return {"response": "two", "steps": []}

            class FirstTest(AgentTest):
                agent = "first"
                adapter = RawAPIAdapter(func=fn1)

            class SecondTest(AgentTest):
                agent = "second"
                adapter = RawAPIAdapter(func=fn2)
        """)
        )

        result = _find_adapter_in_path(test_file, agent_name="second")
        assert result is not None
        assert result.agent == "second"


class TestRecordAndDiff:
    def test_record_uses_requested_agent_name_in_common_locations(self, tmp_path, monkeypatch):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        _write_agent_test_file(tests_dir, "test_alpha.py", "alpha-agent", "alpha")
        _write_agent_test_file(tests_dir, "test_beta.py", "beta-agent", "beta")
        monkeypatch.chdir(tmp_path)

        output_path = tmp_path / "golden.json"
        result = runner.invoke(app, ["record", "beta-agent", "hello", "--output", str(output_path)])

        assert result.exit_code == 0, result.output
        payload = json.loads(output_path.read_text())
        assert payload["agent_name"] == "beta-agent"
        assert payload["final_response"] == "beta: hello"
        assert payload["prompt"] == "hello"

    def test_diff_auto_rerun_uses_recorded_agent_name(self, tmp_path, monkeypatch):
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        _write_agent_test_file(tests_dir, "test_alpha.py", "alpha-agent", "alpha")
        _write_agent_test_file(tests_dir, "test_beta.py", "beta-agent", "beta")
        monkeypatch.chdir(tmp_path)

        golden_path = tmp_path / "golden.json"
        record_result = runner.invoke(
            app, ["record", "beta-agent", "hello", "--output", str(golden_path)]
        )
        assert record_result.exit_code == 0, record_result.output

        diff_result = runner.invoke(app, ["diff", str(golden_path), "--agent", str(tests_dir)])
        assert diff_result.exit_code == 0, diff_result.output
