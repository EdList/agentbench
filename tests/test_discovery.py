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
    _read_json_limited,
    classify_tool_risk,
)


@pytest.mark.asyncio
async def test_discovery_json_stream_stops_at_response_cap(monkeypatch):
    import agentbench.discovery as discovery

    monkeypatch.setattr(discovery, "MAX_DISCOVERY_RESPONSE_SIZE", 5)
    chunks_read = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal chunks_read

        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self):
                nonlocal chunks_read
                for chunk in (b"1234", b"56", b"unread"):
                    chunks_read += 1
                    yield chunk

        return httpx.Response(200, stream=Stream())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="exceeds limit 5"):
            await _read_json_limited(client, "GET", "https://agent.test/openapi.json")

    assert chunks_read == 2


@pytest.mark.asyncio
async def test_library_discovery_rejects_api_key_over_plain_http():
    from agentbench.discovery import discover_agent

    with pytest.raises(ValueError, match="insecure HTTP"):
        await discover_agent("http://agent.test", api_key="target-secret")


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

    @pytest.mark.asyncio
    async def test_mcp_tool_count_is_capped(self, monkeypatch):
        import agentbench.discovery as discovery

        monkeypatch.setattr(discovery, "MAX_DISCOVERED_TOOLS", 2)
        payload = {
            "jsonrpc": "2.0", "id": 1,
            "result": {"tools": [
                {"name": f"tool_{index}", "description": "read", "inputSchema": {}}
                for index in range(10)
            ]},
        }

        async def handler(request):
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = await discovery._try_mcp_discovery("https://agent.test", client)

        assert len(tools) == 2

    @pytest.mark.asyncio
    async def test_mcp_initializes_session_before_retrying_tools_list(self):
        methods: list[str] = []

        async def handler(request: httpx.Request):
            body = __import__("json").loads(request.content)
            method = body["method"]
            methods.append(method)
            if method == "tools/list" and methods.count("tools/list") == 1:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "not initialized"}},
                )
            if method == "initialize":
                return httpx.Response(
                    200,
                    headers={"Mcp-Session-Id": "session-123"},
                    json={
                        "jsonrpc": "2.0", "id": 2,
                        "result": {"protocolVersion": "2025-03-26", "capabilities": {}},
                    },
                )
            if method == "notifications/initialized":
                assert request.headers["Mcp-Session-Id"] == "session-123"
                return httpx.Response(202)
            assert request.headers["Mcp-Session-Id"] == "session-123"
            return httpx.Response(
                200,
                json={
                    "jsonrpc": "2.0", "id": 3,
                    "result": {"tools": [{
                        "name": "search_docs", "description": "Search documents",
                        "inputSchema": {"type": "object", "properties": {}},
                    }]},
                },
            )

        from agentbench.discovery import _try_mcp_discovery

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            tools = await _try_mcp_discovery("https://agent.test/mcp", client)

        assert [tool.name for tool in tools] == ["search_docs"]
        assert methods == [
            "tools/list", "initialize", "notifications/initialized", "tools/list",
        ]


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
