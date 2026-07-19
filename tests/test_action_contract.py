from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow_steps(path: Path) -> list[dict]:
    data = yaml.safe_load(path.read_text())
    if "runs" in data:
        return data["runs"]["steps"]
    jobs = data.get("jobs", {})
    return [step for job in jobs.values() for step in job.get("steps", [])]


def test_publishable_composite_action_matches_cli_contract():
    action_path = ROOT / "action.yml"
    assert action_path.exists(), "GitHub composite actions must expose action.yml at repo root"

    action = yaml.safe_load(action_path.read_text())
    assert action["runs"]["using"] == "composite"
    assert "domains" not in action.get("inputs", {}), "scan CLI has no --domain option"

    scripts = "\n".join(str(step.get("run", "")) for step in _workflow_steps(action_path))
    assert "--domain" not in scripts
    assert "steps.run-scan.outputs.scan-exit-code" in scripts
    assert "SCAN_EXIT" in scripts
    assert "scan_complete" in scripts
    assert "github.action_path" in scripts
    assert "pip install agentbench-cli" not in scripts
    assert "EdList/agentbench" in scripts
    assert "github.com/agentbench/agentbench" not in scripts


def test_repository_scan_workflow_does_not_pass_removed_domain_option():
    scripts = "\n".join(
        str(step.get("run", ""))
        for step in _workflow_steps(ROOT / ".github" / "workflows" / "agentbench-scan.yml")
    )
    assert "--domain" not in scripts
