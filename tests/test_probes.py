"""Tests for the probe registry."""

from agentbench.probes.base import Domain
from agentbench.probes.registry import (
    get_all_probes,
    get_probe_by_id,
    get_probe_counts,
    get_probes_by_domain,
)


class TestRegistry:
    def test_all_probes_load(self):
        probes = get_all_probes()
        assert len(probes) >= 50  # Should have substantial coverage

    def test_probe_ids_unique(self):
        probes = get_all_probes()
        ids = [p.id for p in probes]
        assert len(ids) == len(set(ids)), f"Duplicate IDs: {[x for x in ids if ids.count(x) > 1]}"

    def test_domains_covered(self):
        probes = get_all_probes()
        domains = {p.domain for p in probes}
        assert Domain.SAFETY in domains
        assert Domain.RELIABILITY in domains
        assert Domain.CAPABILITY in domains
        assert Domain.CONSISTENCY in domains

    def test_get_by_domain(self):
        safety = get_probes_by_domain(Domain.SAFETY)
        assert len(safety) > 0
        assert all(p.domain == Domain.SAFETY for p in safety)

    def test_get_by_id(self):
        probes = get_all_probes()
        first = probes[0]
        found = get_probe_by_id(first.id)
        assert found is not None
        assert found.id == first.id

    def test_get_by_id_not_found(self):
        assert get_probe_by_id("nonexistent") is None

    def test_probe_counts(self):
        counts = get_probe_counts()
        assert "safety" in counts
        assert "reliability" in counts
        assert "capability" in counts
        assert "consistency" in counts
        total = sum(counts.values())
        assert total == len(get_all_probes())
