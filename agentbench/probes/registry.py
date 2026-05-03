"""Probe registry — loads and indexes all probe definitions."""

from agentbench.probes.base import Domain, Probe
from agentbench.probes.capability import CAPABILITY_PROBES
from agentbench.probes.consistency import CONSISTENCY_PROBES
from agentbench.probes.reliability import RELIABILITY_PROBES
from agentbench.probes.safety import SAFETY_PROBES

_ALL_PROBES: list[Probe] | None = None


def get_all_probes() -> list[Probe]:
    """Return all probes, lazily loaded and cached."""
    global _ALL_PROBES
    if _ALL_PROBES is None:
        _ALL_PROBES = (
            SAFETY_PROBES + RELIABILITY_PROBES + CAPABILITY_PROBES + CONSISTENCY_PROBES
        )
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
