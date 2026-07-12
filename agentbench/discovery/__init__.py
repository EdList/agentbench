"""Agent discovery — auto-detect tools, capabilities, and attack surface.

Supports multiple discovery protocols:
  1. MCP (Model Context Protocol) — tools/list JSON-RPC
  2. OpenAI function calling schema — parse /v1/models or function definitions
  3. OpenAPI/Swagger — scan REST endpoints the agent can reach
  4. Heuristic — send probe messages and infer tools from responses
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class DiscoveryMethod(StrEnum):
    MCP = "mcp"
    OPENAI_FUNCTIONS = "openai_functions"
    OPENAPI = "openapi"
    HEURISTIC = "heuristic"


class ToolRisk(StrEnum):
    """Risk level of a discovered tool — drives probe selection."""
    LOW = "low"           # read-only, no side effects (search, lookup)
    MEDIUM = "medium"     # writes to user-scoped data (save preference, create task)
    HIGH = "high"         # external side effects (send email, make payment, call API)
    CRITICAL = "critical" # system-level access (execute SQL, run code, file system, shell)


# Keywords that indicate elevated risk
_RISK_KEYWORDS: dict[ToolRisk, list[str]] = {
    ToolRisk.CRITICAL: [
        "sql", "database", "query", "execute", "shell", "command",
        "code", "eval", "exec", "file", "filesystem", "delete",
        "drop", "truncate", "admin", "root", "system",
    ],
    ToolRisk.HIGH: [
        "email", "send", "post", "publish", "payment", "pay",
        "transfer", "order", "buy", "purchase", "notify",
        "webhook", "callback", "message", "sms", "call",
    ],
    ToolRisk.MEDIUM: [
        "create", "update", "write", "save", "set", "modify",
        "edit", "change", "submit", "upload", "store",
    ],
}


@dataclass
class ToolParameter:
    """A single parameter of a discovered tool."""
    name: str
    type: str = "string"
    description: str = ""
    required: bool = True
    enum: list[str] | None = None


@dataclass
class DiscoveredTool:
    """A tool/function the agent can invoke."""
    name: str
    description: str
    parameters: list[ToolParameter] = field(default_factory=list)
    risk: ToolRisk = ToolRisk.LOW
    discovery_method: DiscoveryMethod = DiscoveryMethod.HEURISTIC
    raw_schema: dict[str, Any] | None = None

    @property
    def has_external_effects(self) -> bool:
        return self.risk in (ToolRisk.HIGH, ToolRisk.CRITICAL)


@dataclass
class AgentProfile:
    """Complete discovered profile of an agent's attack surface."""
    endpoint: str
    discovery_methods_tried: list[DiscoveryMethod] = field(default_factory=list)
    discovery_methods_succeeded: list[DiscoveryMethod] = field(default_factory=list)
    tools: list[DiscoveredTool] = field(default_factory=list)
    system_prompt_hints: list[str] = field(default_factory=list)
    rag_indicators: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def tool_names(self) -> list[str]:
        return [t.name for t in self.tools]

    @property
    def high_risk_tools(self) -> list[DiscoveredTool]:
        return [t for t in self.tools if t.risk in (ToolRisk.HIGH, ToolRisk.CRITICAL)]

    @property
    def attack_surface_summary(self) -> str:
        """Human-readable summary of the agent's attack surface."""
        lines: list[str] = []
        lines.append(f"Tools discovered: {len(self.tools)}")
        by_risk: dict[ToolRisk, int] = {}
        for t in self.tools:
            by_risk[t.risk] = by_risk.get(t.risk, 0) + 1
        for risk in ToolRisk:
            count = by_risk.get(risk, 0)
            if count:
                lines.append(f"  {risk.value}: {count}")
        if self.rag_indicators:
            lines.append(f"RAG sources detected: {len(self.rag_indicators)}")
        if self.system_prompt_hints:
            lines.append(f"System prompt indicators: {len(self.system_prompt_hints)}")
        return "\n".join(lines)


def classify_tool_risk(name: str, description: str) -> ToolRisk:
    """Classify a tool's risk level based on its name and description."""
    text = f"{name} {description}".lower()
    for risk in (ToolRisk.CRITICAL, ToolRisk.HIGH, ToolRisk.MEDIUM):
        for keyword in _RISK_KEYWORDS.get(risk, []):
            if keyword in text:
                return risk
    return ToolRisk.LOW


async def discover_agent(
    endpoint: str,
    *,
    api_key: str | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = 15.0,
) -> AgentProfile:
    """Auto-discover an agent's tools and attack surface.

    Tries multiple discovery protocols in order of richness:
      1. MCP tools/list
      2. OpenAI function definitions
      3. OpenAPI spec at common paths
      4. Heuristic probing (always runs as a fallback)

    Returns a complete AgentProfile regardless of which methods succeed.
    """
    profile = AgentProfile(endpoint=endpoint)
    base_headers = {"Content-Type": "application/json"}
    if api_key:
        base_headers["Authorization"] = f"Bearer {api_key}"
    if headers:
        base_headers.update(headers)

    async with httpx.AsyncClient(timeout=timeout, headers=base_headers) as client:
        # Try MCP discovery
        profile.discovery_methods_tried.append(DiscoveryMethod.MCP)
        mcp_tools = await _try_mcp_discovery(endpoint, client)
        if mcp_tools:
            profile.discovery_methods_succeeded.append(DiscoveryMethod.MCP)
            profile.tools.extend(mcp_tools)
            logger.info("MCP discovery found %d tools", len(mcp_tools))

        # Try OpenAI function definitions
        profile.discovery_methods_tried.append(DiscoveryMethod.OPENAI_FUNCTIONS)
        oai_tools = await _try_openai_functions_discovery(endpoint, client)
        if oai_tools:
            profile.discovery_methods_succeeded.append(DiscoveryMethod.OPENAI_FUNCTIONS)
            profile.tools.extend(oai_tools)
            logger.info("OpenAI functions discovery found %d tools", len(oai_tools))

        # Try OpenAPI spec
        profile.discovery_methods_tried.append(DiscoveryMethod.OPENAPI)
        oapi_tools = await _try_openapi_discovery(endpoint, client)
        if oapi_tools:
            profile.discovery_methods_succeeded.append(DiscoveryMethod.OPENAPI)
            profile.tools.extend(oapi_tools)
            logger.info("OpenAPI discovery found %d tools", len(oapi_tools))

    # Deduplicate tools by name (first discovery wins)
    seen: set[str] = set()
    deduped: list[DiscoveredTool] = []
    for tool in profile.tools:
        if tool.name not in seen:
            seen.add(tool.name)
            deduped.append(tool)
    profile.tools = deduped

    # If nothing was discovered, mark heuristic as needed
    if not profile.tools:
        profile.discovery_methods_tried.append(DiscoveryMethod.HEURISTIC)
        logger.info("No tools discovered via protocols; heuristic probing will be used")

    return profile


# ---------------------------------------------------------------------------
# MCP Discovery
# ---------------------------------------------------------------------------

async def _try_mcp_discovery(
    endpoint: str,
    client: httpx.AsyncClient,
) -> list[DiscoveredTool]:
    """Attempt MCP tools/list JSON-RPC call.

    MCP servers expose tools via JSON-RPC 2.0. The client sends:
      {"jsonrpc": "2.0", "method": "tools/list", "id": 1}
    And receives:
      {"jsonrpc": "2.0", "result": {"tools": [...]}, "id": 1}
    """
    tools: list[DiscoveredTool] = []

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": 1,
    }

    try:
        resp = await client.post(endpoint, json=payload)
        if resp.status_code != 200:
            return tools

        data = resp.json()
        if data.get("jsonrpc") != "2.0" or "result" not in data:
            return tools

        raw_tools = data.get("result", {}).get("tools", [])
        for raw in raw_tools:
            name = raw.get("name", "")
            description = raw.get("description", "")
            schema = raw.get("inputSchema", {})

            params = _parse_json_schema_params(schema)
            risk = classify_tool_risk(name, description)

            tools.append(DiscoveredTool(
                name=name,
                description=description,
                parameters=params,
                risk=risk,
                discovery_method=DiscoveryMethod.MCP,
                raw_schema=raw,
            ))

    except (httpx.HTTPError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("MCP discovery failed: %s", exc)

    return tools


# ---------------------------------------------------------------------------
# OpenAI Functions Discovery
# ---------------------------------------------------------------------------

async def _try_openai_functions_discovery(
    endpoint: str,
    client: httpx.AsyncClient,
) -> list[DiscoveredTool]:
    """Attempt to discover tools from an OpenAI-compatible endpoint.

    Some agent platforms expose their function definitions via:
      - A /tools or /functions endpoint
      - The model's response containing tool definitions
      - An OpenAI-assistant /v1/assistants/{id}/tools endpoint
    """
    tools: list[DiscoveredTool] = []

    # Try common tool-listing endpoints
    tool_endpoints = [
        "/tools",
        "/functions",
        "/v1/tools",
        "/api/tools",
        "/.well-known/agent.json",
    ]

    # Normalize base URL (strip /chat/completions etc.)
    base_url = endpoint.rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/chat"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break

    for path in tool_endpoints:
        url = f"{base_url}{path}"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue

            data = resp.json()

            # Handle agent.json well-known format
            if path == "/.well-known/agent.json":
                raw_tools = data.get("tools", [])
            else:
                raw_tools = data if isinstance(data, list) else data.get("tools",
                    data.get("functions", []))

            if not isinstance(raw_tools, list):
                continue

            for raw in raw_tools:
                tool = _parse_openai_function(raw)
                if tool:
                    tools.append(tool)

            if tools:
                break

        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.debug("OpenAI functions discovery at %s failed: %s", path, exc)
            continue

    return tools


def _parse_openai_function(raw: dict[str, Any]) -> DiscoveredTool | None:
    """Parse an OpenAI function definition into a DiscoveredTool."""
    # OpenAI function format:
    # {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    func = raw.get("function", raw)
    name = func.get("name", "")
    if not name:
        return None

    description = func.get("description", "")
    schema = func.get("parameters", {})
    params = _parse_json_schema_params(schema)
    risk = classify_tool_risk(name, description)

    return DiscoveredTool(
        name=name,
        description=description,
        parameters=params,
        risk=risk,
        discovery_method=DiscoveryMethod.OPENAI_FUNCTIONS,
        raw_schema=raw,
    )


# ---------------------------------------------------------------------------
# OpenAPI Discovery
# ---------------------------------------------------------------------------

async def _try_openapi_discovery(
    endpoint: str,
    client: httpx.AsyncClient,
) -> list[DiscoveredTool]:
    """Attempt to discover agent-accessible APIs from an OpenAPI spec."""
    tools: list[DiscoveredTool] = []

    base_url = endpoint.rstrip("/")
    for suffix in ("/chat/completions", "/completions", "/chat", "/api"):
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break

    spec_paths = ["/openapi.json", "/swagger.json", "/api/openapi.json", "/v1/openapi.json"]

    for path in spec_paths:
        url = f"{base_url}{path}"
        try:
            resp = await client.get(url)
            if resp.status_code != 200:
                continue

            spec = resp.json()
            if spec.get("openapi") is None and spec.get("swagger") is None:
                continue

            paths = spec.get("paths", {})
            for route, methods in paths.items():
                for method, details in methods.items():
                    if method.upper() not in ("GET", "POST", "PUT", "PATCH", "DELETE"):
                        continue

                    name = details.get("operationId") or f"{method}_{route}"
                    description = details.get("summary", "") or details.get("description", "")

                    # Parse parameters
                    params: list[ToolParameter] = []
                    for param in details.get("parameters", []):
                        params.append(ToolParameter(
                            name=param.get("name", ""),
                            type=param.get("schema", {}).get("type", "string"),
                            description=param.get("description", ""),
                            required=param.get("required", False),
                        ))

                    # Parse request body
                    body = details.get("requestBody", {})
                    if body:
                        schema_ref = body.get("content", {}).get("application/json",
                            {}).get("schema", {})
                        if schema_ref:
                            params.append(ToolParameter(
                                name="body",
                                type=schema_ref.get("type", "object"),
                                description="Request body",
                                required=True,
                            ))

                    risk = classify_tool_risk(name, f"{method} {route} {description}")

                    tools.append(DiscoveredTool(
                        name=name,
                        description=f"{method.upper()} {route} — {description}",
                        parameters=params,
                        risk=risk,
                        discovery_method=DiscoveryMethod.OPENAPI,
                        raw_schema=details,
                    ))

            if tools:
                break

        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            logger.debug("OpenAPI discovery at %s failed: %s", path, exc)
            continue

    return tools


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _parse_json_schema_params(schema: dict[str, Any]) -> list[ToolParameter]:
    """Parse a JSON Schema object into ToolParameters."""
    params: list[ToolParameter] = []
    properties = schema.get("properties", {})
    required = set(schema.get("required", []))

    for name, prop in properties.items():
        params.append(ToolParameter(
            name=name,
            type=prop.get("type", "string"),
            description=prop.get("description", ""),
            required=name in required,
            enum=prop.get("enum"),
        ))

    return params
