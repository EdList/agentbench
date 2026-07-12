"""Tests for the agent discovery system."""

from __future__ import annotations

import httpx
import pytest

from agentbench.discovery import (
    AgentProfile,
    DiscoveredTool,
    DiscoveryMethod,
    ToolRisk,
    _parse_json_schema_params,
    classify_tool_risk,
)


class TestClassifyToolRisk:
    def test_critical_sql(self):
        assert classify_tool_risk("execute_sql", "Run SQL queries") == ToolRisk.CRITICAL

    def test_critical_shell(self):
        assert classify_tool_risk("run_command", "Execute shell commands") == ToolRisk.CRITICAL

    def test_high_email(self):
        assert classify_tool_risk("send_email", "Send emails to users") == ToolRisk.HIGH

    def test_high_payment(self):
        assert classify_tool_risk("process_payment", "Process customer payments") == ToolRisk.HIGH

    def test_medium_write(self):
        assert classify_tool_risk("save_config", "Update configuration") == ToolRisk.MEDIUM

    def test_low_read(self):
        assert classify_tool_risk("search", "Search documents") == ToolRisk.LOW


class TestParseJsonSchemaParams:
    def test_basic_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "limit": {"type": "integer", "description": "Max results"},
            },
            "required": ["query"],
        }
        params = _parse_json_schema_params(schema)
        assert len(params) == 2
        assert params[0].name == "query"
        assert params[0].required is True
        assert params[1].name == "limit"
        assert params[1].required is False

    def test_empty_schema(self):
        params = _parse_json_schema_params({})
        assert params == []

    def test_enum_param(self):
        schema = {
            "properties": {
                "sort": {"type": "string", "enum": ["asc", "desc"]},
            },
            "required": ["sort"],
        }
        params = _parse_json_schema_params(schema)
        assert params[0].enum == ["asc", "desc"]


class TestMCPDiscovery:
    @pytest.mark.asyncio
    async def test_mcp_valid_response(self):
        """Test that MCP tools/list response is parsed correctly."""
        mcp_response = {
            "jsonrpc": "2.0",
            "result": {
                "tools": [
                    {
                        "name": "search_docs",
                        "description": "Search documents",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string"},
                            },
                            "required": ["query"],
                        },
                    },
                    {
                        "name": "execute_sql",
                        "description": "Execute SQL queries",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "sql": {"type": "string"},
                            },
                            "required": ["sql"],
                        },
                    },
                ],
            },
            "id": 1,
        }

        async def handler(request):
            return httpx.Response(200, json=mcp_response)

        # Mock the HTTP call by directly testing the parsing
        from agentbench.discovery import _try_mcp_discovery
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
        )
        try:
            tools = await _try_mcp_discovery("http://test.local", client)
            assert len(tools) == 2
            assert tools[0].name == "search_docs"
            assert tools[0].discovery_method == DiscoveryMethod.MCP
            assert tools[1].name == "execute_sql"
            assert tools[1].risk == ToolRisk.CRITICAL
        finally:
            await client.aclose()

    @pytest.mark.asyncio
    async def test_mcp_no_tools(self):
        async def handler(request):
            return httpx.Response(404)

        from agentbench.discovery import _try_mcp_discovery
        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            tools = await _try_mcp_discovery("http://test.local", client)
            assert tools == []
        finally:
            await client.aclose()


class TestAgentProfile:
    def test_attack_surface_summary(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="search", description="Search", risk=ToolRisk.LOW),
                DiscoveredTool(name="send_email", description="Send", risk=ToolRisk.HIGH),
                DiscoveredTool(name="exec_sql", description="SQL", risk=ToolRisk.CRITICAL),
            ],
        )
        summary = profile.attack_surface_summary
        assert "3" in summary
        assert "critical: 1" in summary
        assert "high: 1" in summary

    def test_high_risk_tools(self):
        profile = AgentProfile(
            endpoint="http://test.local",
            tools=[
                DiscoveredTool(name="search", description="Search", risk=ToolRisk.LOW),
                DiscoveredTool(name="send_email", description="Send", risk=ToolRisk.HIGH),
                DiscoveredTool(name="exec_sql", description="SQL", risk=ToolRisk.CRITICAL),
            ],
        )
        high = profile.high_risk_tools
        assert len(high) == 2
        assert all(t.risk in (ToolRisk.HIGH, ToolRisk.CRITICAL) for t in high)

    def test_empty_profile(self):
        profile = AgentProfile(endpoint="http://test.local")
        assert profile.tools == []
        assert profile.high_risk_tools == []
        assert profile.tool_names == []
