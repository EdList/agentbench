"""Tests for local leaderboard storage."""

from __future__ import annotations

import json

from agentbench import leaderboard


def test_load_leaderboard_rejects_non_list_json(tmp_path, monkeypatch):
    monkeypatch.setattr(leaderboard, "_DEFAULT_DIR", tmp_path)
    (tmp_path / "leaderboard.json").write_text('{"entries": []}', encoding="utf-8")

    assert leaderboard.load_leaderboard() == []


def test_load_leaderboard_rejects_non_dict_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(leaderboard, "_DEFAULT_DIR", tmp_path)
    (tmp_path / "leaderboard.json").write_text(json.dumps([{"ok": True}, 1]), encoding="utf-8")

    assert leaderboard.load_leaderboard() == []
    assert leaderboard.compare_results(label="anything") == []


def test_get_recent_zero_and_negative_return_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(leaderboard, "_DEFAULT_DIR", tmp_path)
    entries = [{"id": 1}, {"id": 2}]
    (tmp_path / "leaderboard.json").write_text(json.dumps(entries), encoding="utf-8")

    assert leaderboard.get_recent(0) == []
    assert leaderboard.get_recent(-1) == []
