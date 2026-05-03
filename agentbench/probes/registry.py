"""Probe registry — loads and indexes all probe definitions."""

from pathlib import Path

from agentbench.probes.base import Domain, Probe
from agentbench.probes.yaml_loader import load_all_yaml_probes

_BUILTIN_DIR = Path(__file__).parent / "builtin"

_ALL_PROBES: list[Probe] | None = None


def get_all_probes() -> list[Probe]:
    """Return all probes, lazily loaded from YAML and cached."""
    global _ALL_PROBES
    if _ALL_PROBES is None:
        _ALL_PROBES = load_all_yaml_probes(_BUILTIN_DIR)
    return _ALL_PROBES


def get_probes_by_domain(domain: Domain) -> list[Probe]:
    """Return probes filtered to a single domain."""
    return [p for p in get_all_probes() if p.domain == domain]


def get_probe_by_id(probe_id: str) -> Probe | None:
    """Look up a single probe by ID."""
    for p in get_all_probes():
        if p.id == probe_id:
            return p
    return None


def get_probe_counts() -> dict[str, int]:
    """Return count of probes per domain."""
    counts: dict[str, int] = {}
    for p in get_all_probes():
        counts[p.domain.value] = counts.get(p.domain.value, 0) + 1
    return counts
