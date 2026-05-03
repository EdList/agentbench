"""Tests for probe updater safety."""

from __future__ import annotations

from types import SimpleNamespace

from agentbench import updater

VALID_OLD = """\
version: "1.0"
probes:
  - id: old-probe
    domain: safety
    category: test
    description: old
    prompt: old
    check: check
    expected: expected
    severity: warning
"""

VALID_NEW = """\
version: "1.0"
probes:
  - id: new-probe
    domain: safety
    category: test
    description: new
    prompt: new
    check: check
    expected: expected
    severity: warning
"""


def test_pull_updates_validates_before_replacing(tmp_path, monkeypatch):
    local = tmp_path / "safety.yaml"
    local.write_text(VALID_OLD, encoding="utf-8")

    monkeypatch.setattr(updater, "_BUILTIN_DIR", tmp_path)
    monkeypatch.setattr(
        updater.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text="probes: not-a-list\n"),
    )

    assert updater.pull_updates(["safety.yaml"]) == []
    assert local.read_text(encoding="utf-8") == VALID_OLD
    assert not (tmp_path / "safety.yaml.bak").exists()


def test_pull_updates_invalid_yaml_syntax_does_not_replace(tmp_path, monkeypatch):
    local = tmp_path / "safety.yaml"
    local.write_text(VALID_OLD, encoding="utf-8")

    monkeypatch.setattr(updater, "_BUILTIN_DIR", tmp_path)
    monkeypatch.setattr(
        updater.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text="probes: [\n"),
    )

    assert updater.pull_updates(["safety.yaml"]) == []
    assert local.read_text(encoding="utf-8") == VALID_OLD
    assert not (tmp_path / "safety.yaml.bak").exists()


def test_pull_updates_atomically_replaces_valid_yaml_and_keeps_backup(tmp_path, monkeypatch):
    local = tmp_path / "safety.yaml"
    local.write_text(VALID_OLD, encoding="utf-8")

    monkeypatch.setattr(updater, "_BUILTIN_DIR", tmp_path)
    monkeypatch.setattr(
        updater.httpx,
        "get",
        lambda *args, **kwargs: SimpleNamespace(status_code=200, text=VALID_NEW),
    )

    assert updater.pull_updates(["safety.yaml"]) == ["safety.yaml"]
    assert local.read_text(encoding="utf-8") == VALID_NEW
    assert (tmp_path / "safety.yaml.bak").read_text(encoding="utf-8") == VALID_OLD
