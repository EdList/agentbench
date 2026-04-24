"""Tests for AgentBench storage — TrajectoryStore and TrajectoryDiff."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from agentbench.storage.trajectory import (
    DiffResult,
    StepDiff,
    TrajectoryDiff,
    TrajectoryStore,
)

# ─── Helpers ───

SAMPLE_TRAJECTORY = {
    "name": "test-run",
    "response": "Final answer",
    "steps": [
        {
            "action": "tool_call",
            "tool_name": "search",
            "tool_input": {"q": "test"},
            "tool_output": "results",
        },
        {"action": "llm_response", "response": "Found it"},
    ],
}


# ─── TrajectoryStore: Init ───

class TestTrajectoryStoreInit:
    def test_creates_base_dir(self, tmp_path):
        base = tmp_path / "my_trajs"
        _store = TrajectoryStore(base_dir=base)
        assert base.exists()

    def test_default_base_dir(self):
        store = TrajectoryStore()
        assert store._base_dir == Path(".agentbench/trajectories")

    def test_accepts_string_path(self, tmp_path):
        base = str(tmp_path / "str_path")
        _store = TrajectoryStore(base_dir=base)
        assert Path(base).exists()


# ─── TrajectoryStore: Save ───

class TestTrajectoryStoreSave:
    def test_save_with_explicit_name(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        path = store.save(SAMPLE_TRAJECTORY, name="my-run")
        assert path.exists()
        assert path.name == "my-run.json"

        data = json.loads(path.read_text())
        assert data["name"] == "test-run"

    def test_save_auto_generates_name_from_data(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        traj = {"name": "auto-named", "response": "ok"}
        path = store.save(traj)
        assert path.name == "auto-named.json"
        assert path.exists()

    def test_save_generates_timestamp_name_if_no_name(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        traj = {"response": "ok"}
        path = store.save(traj)
        assert path.name.startswith("run-")
        assert path.exists()

    def test_save_returns_path(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        path = store.save({"data": True}, name="ret-path")
        assert isinstance(path, Path)
        assert path == tmp_path / "ret-path.json"

    def test_save_complex_data(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        complex_data = {
            "name": "complex",
            "steps": [{"action": "tool_call", "nested": {"a": 1}}],
            "tags": ["smoke", "regression"],
        }
        path = store.save(complex_data)
        loaded = json.loads(path.read_text())
        assert loaded["tags"] == ["smoke", "regression"]


# ─── TrajectoryStore: Load ───

class TestTrajectoryStoreLoad:
    def test_load_existing(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        store.save(SAMPLE_TRAJECTORY, name="load-test")
        data = store.load("load-test")
        assert data["name"] == "test-run"
        assert len(data["steps"]) == 2

    def test_load_nonexistent_raises(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        with pytest.raises(FileNotFoundError, match="Trajectory not found"):
            store.load("does-not-exist")


# ─── TrajectoryStore: List ───

class TestTrajectoryStoreList:
    def test_list_empty(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        assert store.list() == []

    def test_list_returns_saved_names(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        store.save({"name": "a"}, name="run-a")
        store.save({"name": "b"}, name="run-b")
        store.save({"name": "c"}, name="run-c")
        names = store.list()
        assert set(names) == {"run-a", "run-b", "run-c"}

    def test_list_ignores_non_json(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        store.save({"name": "x"}, name="real-json")
        (tmp_path / "notes.txt").write_text("not a trajectory")
        assert store.list() == ["real-json"]


# ─── TrajectoryStore: Delete ───

class TestTrajectoryStoreDelete:
    def test_delete_existing(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        store.save({"name": "del-me"}, name="to-delete")
        assert "to-delete" in store.list()

        store.delete("to-delete")
        assert "to-delete" not in store.list()

    def test_delete_nonexistent_no_error(self, tmp_path):
        store = TrajectoryStore(base_dir=tmp_path)
        # Should not raise
        store.delete("ghost")


# ─── TrajectoryStore: Path Traversal ───

class TestTrajectoryStorePathTraversal:
    def test_sanitize_replaces_special_chars(self):
        clean = TrajectoryStore._sanitize_name("my run (v2)")
        assert clean == "my_run__v2_"

    def test_sanitize_dotfiles_preserved(self):
        clean = TrajectoryStore._sanitize_name(".hidden")
        assert clean == ".hidden"

    def test_sanitize_dashes_preserved(self):
        clean = TrajectoryStore._sanitize_name("my-run-v2")
        assert clean == "my-run-v2"

    def test_sanitize_path_traversal_replaces_slashes(self):
        """Slashes are replaced but dots are preserved (allowed by regex)."""
        clean = TrajectoryStore._sanitize_name("../../etc/passwd")
        assert "/" not in clean
        # Dots are allowed by the regex [\w\-.], so '..' stays
        assert clean == ".._.._etc_passwd"

    def test_sanitize_empty_raises(self):
        with pytest.raises(ValueError, match="Invalid trajectory name"):
            TrajectoryStore._sanitize_name("")

    def test_sanitize_only_special_chars_becomes_underscores(self):
        """$$$ sanitizes to '___' which is a valid name (not just '_')."""
        clean = TrajectoryStore._sanitize_name("$$$")
        assert clean == "___"  # valid — not equal to '_'

    def test_save_sanitized_name_stays_in_dir(self, tmp_path):
        """Path traversal characters are sanitized, result stays in base_dir."""
        store = TrajectoryStore(base_dir=tmp_path)
        # ../../etc/passwd gets sanitized to .._.._etc_passwd which is safe
        path = store.save({"data": True}, name="../../etc/passwd")
        assert path.resolve().is_relative_to(tmp_path.resolve())


# ─── TrajectoryDiff: Compare ───

class TestTrajectoryDiffCompare:
    def _make_traj(self, steps=None, response="ok", name="test"):
        return {
            "name": name,
            "steps": steps or [],
            "response": response,
        }

    def test_identical_trajectories(self):
        golden = self._make_traj(
            steps=[
                {
                    "action": "tool_call",
                    "tool_name": "search",
                    "tool_input": {"q": "x"},
                    "tool_output": "r",
                },
                {"action": "llm_response", "response": "Found"},
            ],
            response="Found",
            name="golden",
        )
        current = self._make_traj(
            steps=[
                {
                    "action": "tool_call",
                    "tool_name": "search",
                    "tool_input": {"q": "x"},
                    "tool_output": "r",
                },
                {"action": "llm_response", "response": "Found"},
            ],
            response="Found",
            name="current",
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert not result.has_critical
        assert not result.has_warnings
        # Should have match entries
        assert result.summary.get("match", 0) >= 1

    def test_different_tool_names_is_critical(self):
        golden = self._make_traj(
            steps=[{
                "action": "tool_call",
                "tool_name": "search",
                "tool_input": {},
                "tool_output": "r",
            }],
        )
        current = self._make_traj(
            steps=[{
                "action": "tool_call",
                "tool_name": "database",
                "tool_input": {},
                "tool_output": "r",
            }],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert result.has_critical
        critical_diffs = [d for d in result.step_diffs if d.severity == "critical"]
        assert any(d.field == "tool_name" for d in critical_diffs)

    def test_different_action_types_is_critical(self):
        golden = self._make_traj(steps=[{"action": "tool_call", "tool_name": "search"}])
        current = self._make_traj(steps=[{"action": "llm_response", "response": "text"}])

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert result.has_critical
        critical_diffs = [d for d in result.step_diffs if d.severity == "critical"]
        assert any(d.field == "action" for d in critical_diffs)

    def test_different_step_count_is_warning(self):
        golden = self._make_traj(
            steps=[
                {"action": "llm_response", "response": "a"},
                {"action": "llm_response", "response": "b"},
            ],
        )
        current = self._make_traj(
            steps=[{"action": "llm_response", "response": "a"}],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert result.has_warnings
        warning_diffs = [d for d in result.step_diffs if d.severity == "warning"]
        assert any(d.field == "step_count" for d in warning_diffs)

    def test_different_tool_input_is_warning(self):
        golden = self._make_traj(
            steps=[{
                "action": "tool_call",
                "tool_name": "search",
                "tool_input": {"q": "old"},
                "tool_output": "r",
            }],
        )
        current = self._make_traj(
            steps=[{
                "action": "tool_call",
                "tool_name": "search",
                "tool_input": {"q": "new"},
                "tool_output": "r",
            }],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert result.has_warnings
        warning_diffs = [d for d in result.step_diffs if d.severity == "warning"]
        assert any(d.field == "tool_input" for d in warning_diffs)

    def test_new_error_is_critical(self):
        golden = self._make_traj(
            steps=[{"action": "tool_call", "tool_name": "search", "tool_output": "ok"}],
        )
        current = self._make_traj(
            steps=[{
                "action": "tool_call",
                "tool_name": "search",
                "tool_output": "ok",
                "error": "crashed",
            }],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert result.has_critical
        critical_diffs = [d for d in result.step_diffs if d.severity == "critical"]
        assert any(d.field == "error" for d in critical_diffs)

    def test_different_response_is_info(self):
        golden = self._make_traj(
            steps=[{"action": "llm_response", "response": "Hello world"}],
            response="Hello world",
        )
        current = self._make_traj(
            steps=[{"action": "llm_response", "response": "Goodbye world"}],
            response="Goodbye world",
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        info_diffs = [d for d in result.step_diffs if d.severity == "info"]
        assert any(d.field == "response" for d in info_diffs)

    def test_extra_step_in_current_is_warning(self):
        golden = self._make_traj(
            steps=[{"action": "llm_response", "response": "a"}],
        )
        current = self._make_traj(
            steps=[
                {"action": "llm_response", "response": "a"},
                {"action": "tool_call", "tool_name": "extra"},
            ],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        warning_diffs = [d for d in result.step_diffs if d.severity == "warning"]
        assert any(d.field == "extra_step" for d in warning_diffs)

    def test_missing_step_in_current_is_warning(self):
        golden = self._make_traj(
            steps=[
                {"action": "llm_response", "response": "a"},
                {"action": "tool_call", "tool_name": "search"},
            ],
        )
        current = self._make_traj(
            steps=[{"action": "llm_response", "response": "a"}],
        )

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        warning_diffs = [d for d in result.step_diffs if d.severity == "warning"]
        assert any(d.field == "missing_step" for d in warning_diffs)

    def test_empty_trajectories(self):
        diff = TrajectoryDiff()
        result = diff.compare(
            {"name": "g", "steps": []},
            {"name": "c", "steps": []},
        )
        assert not result.has_critical
        assert not result.has_warnings
        assert len(result.step_diffs) == 0

    def test_summary_counts(self):
        golden = {
            "name": "g",
            "steps": [{
                "action": "tool_call",
                "tool_name": "a",
                "tool_input": {"x": 1},
                "tool_output": "r",
            }],
        }
        current = {
            "name": "c",
            "steps": [{
                "action": "tool_call",
                "tool_name": "a",
                "tool_input": {"x": 2},
                "tool_output": "r",
            }],
        }

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        assert isinstance(result.summary, dict)
        assert "critical" in result.summary
        assert "warning" in result.summary
        assert "info" in result.summary
        assert "match" in result.summary

    def test_different_final_response_is_info(self):
        golden = {"name": "g", "response": "Answer A", "steps": []}
        current = {"name": "c", "response": "Answer B", "steps": []}

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        info_diffs = [d for d in result.step_diffs if d.severity == "info"]
        assert any(d.field == "final_response" for d in info_diffs)

    def test_compare_uses_final_response_key(self):
        """When 'final_response' key is used instead of 'response'."""
        golden = {"name": "g", "final_response": "A", "steps": []}
        current = {"name": "c", "final_response": "B", "steps": []}

        diff = TrajectoryDiff()
        result = diff.compare(golden, current)
        info_diffs = [d for d in result.step_diffs if d.severity == "info"]
        assert any(d.field == "final_response" for d in info_diffs)


# ─── DiffResult ───

class TestDiffResult:
    def test_has_critical(self):
        result = DiffResult(golden_name="g", current_name="c")
        result.step_diffs.append(StepDiff(
            step_number=0, severity="critical", field="action",
            golden_value="a", current_value="b", message="diff",
        ))
        assert result.has_critical

    def test_has_warnings(self):
        result = DiffResult(golden_name="g", current_name="c")
        result.step_diffs.append(StepDiff(
            step_number=0, severity="warning", field="step_count",
            golden_value=1, current_value=2, message="diff",
        ))
        assert result.has_warnings

    def test_no_critical(self):
        result = DiffResult(golden_name="g", current_name="c")
        assert not result.has_critical

    def test_no_warnings(self):
        result = DiffResult(golden_name="g", current_name="c")
        assert not result.has_warnings

    def test_format_output_no_diffs(self):
        result = DiffResult(golden_name="g", current_name="c")
        output = result.format_output()
        assert "match perfectly" in output

    def test_format_output_with_diffs(self):
        result = DiffResult(golden_name="g", current_name="c")
        result.step_diffs.append(StepDiff(
            step_number=0, severity="critical", field="tool_name",
            golden_value="search", current_value="db", message="Different tool",
        ))
        result.summary = {"critical": 1, "warning": 0, "info": 0, "match": 0}
        output = result.format_output()
        assert "Summary" in output
