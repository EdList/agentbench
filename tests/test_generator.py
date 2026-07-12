"""Tests for the agent-specific probe generator."""

from __future__ import annotations

from agentbench.discovery import AgentProfile, DiscoveredTool, ToolRisk
from agentbench.probes.base import Domain, Severity
from agentbench.probes.generator import generate_probes


class TestGenerateProbes:
    def test_no_tools_generates_baseline(self):
        profile = AgentProfile(endpoint="http://test.local")
        probes = generate_probes(profile)
        # Should have baseline + no-tool probes
        assert len(probes) >= 4
        ids = [gp.probe.id for gp in probes]
        assert "baseline_prompt_extraction" in ids
        assert "baseline_prompt_extraction_multi" in ids
        assert "notool_prompt_extraction" in ids
        assert "notool_instruction_override" in ids

    def test_critical_tool_generates_probes(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(
                    name="execute_sql",
                    description="Execute SQL queries on the database",
                    risk=ToolRisk.CRITICAL,
                ),
            ],
        )
        probes = generate_probes(profile)
        ids = [gp.probe.id for gp in probes]
        assert "sql_injection_execute_sql" in ids
        # All tool-specific probes should target execute_sql
        for gp in probes:
            if gp.target_tool:
                assert gp.target_tool == "execute_sql"

    def test_high_risk_tool_generates_probes(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(
                    name="send_email",
                    description="Send email to customers",
                    risk=ToolRisk.HIGH,
                ),
            ],
        )
        probes = generate_probes(profile)
        ids = [gp.probe.id for gp in probes]
        assert "tool_output_injection_send_email" in ids
        assert "exfil_send_email" in ids
        assert "social_eng_send_email" in ids

    def test_medium_risk_tool_generates_probes(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(
                    name="update_record",
                    description="Update user records",
                    risk=ToolRisk.MEDIUM,
                ),
            ],
        )
        probes = generate_probes(profile)
        ids = [gp.probe.id for gp in probes]
        assert "unauthorized_write_update_record" in ids

    def test_probes_sorted_by_severity(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="exec", description="Execute code", risk=ToolRisk.CRITICAL),
                DiscoveredTool(name="update", description="Update record", risk=ToolRisk.MEDIUM),
            ],
        )
        probes = generate_probes(profile)
        severities = [gp.probe.severity for gp in probes]
        # Critical should come before warning/info
        for i in range(len(severities) - 1):
            weight = {Severity.CRITICAL: 0, Severity.WARNING: 1, Severity.INFO: 2}
            assert weight[severities[i]] <= weight[severities[i + 1]]

    def test_multiple_tools(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="exec_sql", description="SQL", risk=ToolRisk.CRITICAL),
                DiscoveredTool(name="send_email", description="Email", risk=ToolRisk.HIGH),
                DiscoveredTool(name="search", description="Search docs", risk=ToolRisk.LOW),
            ],
        )
        probes = generate_probes(profile)
        # Low-risk tools don't generate specific probes
        target_tools = {gp.target_tool for gp in probes if gp.target_tool}
        assert "search" not in target_tools
        assert "exec_sql" in target_tools
        assert "send_email" in target_tools

    def test_all_probes_have_safety_domain(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="exec", description="Execute", risk=ToolRisk.CRITICAL),
            ],
        )
        probes = generate_probes(profile)
        for gp in probes:
            assert gp.probe.domain == Domain.SAFETY

    def test_all_probes_have_remediation(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="exec_sql", description="SQL", risk=ToolRisk.CRITICAL),
            ],
        )
        probes = generate_probes(profile)
        for gp in probes:
            assert gp.probe.remediation, f"Probe {gp.probe.id} missing remediation"
