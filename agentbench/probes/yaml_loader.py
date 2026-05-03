"""YAML-based probe loader for AgentBench.

Loads probe definitions from YAML files, mapping string values to the
Domain and Severity enums and validating probe uniqueness.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from agentbench.probes.base import Domain, Probe, Severity

# Mapping from lowercase YAML string → enum member name
_DOMAIN_MAP: dict[str, Domain] = {
    "safety": Domain.SAFETY,
    "reliability": Domain.RELIABILITY,
    "capability": Domain.CAPABILITY,
    "consistency": Domain.CONSISTENCY,
}

_SEVERITY_MAP: dict[str, Severity] = {
    "critical": Severity.CRITICAL,
    "warning": Severity.WARNING,
    "info": Severity.INFO,
}

# Fields that must be present in every probe entry
_REQUIRED_FIELDS = (
    "id",
    "domain",
    "category",
    "description",
    "prompt",
    "check",
    "expected",
    "severity",
)


def _parse_probe(entry: dict, *, path: Path, index: int) -> Probe:
    """Convert a single YAML dict into a Probe object."""
    # Validate required fields
    missing = [f for f in _REQUIRED_FIELDS if f not in entry]
    if missing:
        probe_label = entry.get("id", f"index {index}")
        raise ValueError(
            f"{path}: probe {probe_label!r} is missing required fields: {', '.join(missing)}"
        )

    domain_str = entry["domain"]
    if domain_str not in _DOMAIN_MAP:
        raise ValueError(
            f"{path}: probe {entry['id']!r} has unknown domain '{domain_str}'. "
            f"Expected one of: {', '.join(_DOMAIN_MAP)}"
        )

    severity_str = entry["severity"]
    if severity_str not in _SEVERITY_MAP:
        raise ValueError(
            f"{path}: probe {entry['id']!r} has unknown severity '{severity_str}'. "
            f"Expected one of: {', '.join(_SEVERITY_MAP)}"
        )

    return Probe(
        id=entry["id"],
        domain=_DOMAIN_MAP[domain_str],
        category=entry["category"],
        description=entry["description"],
        prompt=entry["prompt"],
        system_prompt=entry.get("system_prompt"),
        follow_ups=entry.get("follow_ups") or [],
        severity=_SEVERITY_MAP[severity_str],
        tags=entry.get("tags") or [],
        check=entry["check"],
        expected=entry["expected"],
        remediation=entry.get("remediation", ""),
        explanation=entry.get("explanation", ""),
    )


def load_probes_from_yaml(path: str | Path) -> list[Probe]:
    """Load probes from a single YAML file.

    Parameters
    ----------
    path:
        Path to a YAML file conforming to the AgentBench probe schema.

    Returns
    -------
    list[Probe]
        Parsed Probe objects.

    Raises
    ------
    ValueError
        If probe IDs are duplicated within the file.
    FileNotFoundError
        If *path* does not exist.
    """
    path = Path(path)
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)

    if data is None:
        return []

    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML document must be a mapping")

    raw_probes = data.get("probes")
    if raw_probes is None:
        raw_probes = []
    elif not isinstance(raw_probes, list):
        raise ValueError(f"{path}: 'probes' must be a list when present")

    probes: list[Probe] = []
    seen_ids: set[str] = set()

    for index, entry in enumerate(raw_probes):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: probe entry at index {index} must be a mapping")

        probe = _parse_probe(entry, path=path, index=index)
        if probe.id in seen_ids:
            raise ValueError(f"{path}: Duplicate probe ID: '{probe.id}'")
        seen_ids.add(probe.id)
        probes.append(probe)

    return probes


def load_all_yaml_probes(directory: str | Path) -> list[Probe]:
    """Load probes from every ``.yaml`` file in *directory*.

    Parameters
    ----------
    directory:
        Directory to scan for YAML probe definitions.

    Returns
    -------
    list[Probe]
        All probes found across every YAML file, in lexicographic file order.

    Raises
    ------
    ValueError
        If any probe ID is duplicated across files.
    """
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(f"Directory not found: {directory}")

    all_probes: list[Probe] = []
    seen_ids: set[str] = set()

    yaml_files = sorted(directory.glob("*.yaml"))

    for yaml_file in yaml_files:
        probes = load_probes_from_yaml(yaml_file)
        for probe in probes:
            if probe.id in seen_ids:
                raise ValueError(f"Duplicate probe ID '{probe.id}' found across YAML files")
            seen_ids.add(probe.id)
        all_probes.extend(probes)

    return all_probes
