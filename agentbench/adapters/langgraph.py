"""LangGraph adapter — test LangGraph graph executions with AgentBench."""

from __future__ import annotations

import time
from typing import Any

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import (
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)

# Lazy import: langgraph is an optional dependency.
try:
    import langgraph as _langgraph_module  # type: ignore[import-untyped]
except ImportError:
    _langgraph_module = None  # type: ignore[assignment]


def _require_langgraph() -> None:
    """Raise a helpful error if the langgraph package is not installed."""
    if _langgraph_module is None:
        raise ImportError(
            "The 'langgraph' package is required for the LangGraphAdapter. "
            "Install it with:  pip install agentbench[langgraph]"
        )


class LangGraphAdapter(AgentAdapter):
    """Adapter for LangGraph compiled graphs.

    Wraps a compiled LangGraph graph and records every node execution
    — including tool calls — as an AgentBench trajectory step.

    The adapter intercepts graph execution by using LangGraph's stream
    mode to capture each node's output.  It supports failure injection
    and latency injection.

    Usage::

        from langgraph.graph import StateGraph, END
        from langgraph.prebuilt import create_react_agent

        # Option 1: Simple prebuilt agent
        graph = create_react_agent(model, tools)

        # Option 2: Custom graph
        builder = StateGraph(State)
        builder.add_node("agent", agent_fn)
        builder.add_node("tools", tool_node)
        builder.add_edge("agent", "tools")
        builder.add_edge("tools", "agent")
        graph = builder.compile()

        adapter = LangGraphAdapter(graph, tools=["search", "calculator"])
        trajectory = adapter.run("What's the weather?", trajectory)

    Args:
        graph: A compiled LangGraph graph (result of ``builder.compile()``).
        tools: Optional explicit list of tool names available in the graph.
        node_name_map: Optional mapping from LangGraph node names to
            semantic tool names.  Useful when tool node names differ from
            actual tool names.  E.g., ``{"tools": "search"}``.
    """

    def __init__(
        self,
        graph: Any,
        tools: list[str] | None = None,
        node_name_map: dict[str, str] | None = None,
    ) -> None:
        _require_langgraph()
        self._graph = graph
        self._tools = tools
        self._node_name_map = node_name_map or {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_available_tools(self) -> list[str]:
        """Return tool names available in the graph.

        If tools were provided at construction time, those are returned.
        Otherwise the adapter attempts to introspect from the graph's
        node definitions.
        """
        if self._tools is not None:
            return list(self._tools)

        # Introspect from the graph
        try:
            names: list[str] = []
            # Try to get nodes from the compiled graph
            nodes = []
            if hasattr(self._graph, "nodes"):
                nodes = self._graph.nodes
            elif hasattr(self._graph, "get_graph"):
                graph_obj = self._graph.get_graph()
                nodes = getattr(graph_obj, "nodes", {})

            if isinstance(nodes, dict):
                for node_name, node_data in nodes.items():
                    # Skip built-in nodes
                    if node_name in ("__start__", "__end__", "__router__"):
                        continue
                    names.append(node_name)
            elif isinstance(nodes, (list, tuple)):
                for node in nodes:
                    if isinstance(node, str):
                        if node not in ("__start__", "__end__"):
                            names.append(node)
                    elif hasattr(node, "name"):
                        names.append(node.name)

            return names
        except Exception:
            return []

    def run(
        self,
        prompt: str,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection] | None = None,
        latency_injections: list[ToolLatencyInjection] | None = None,
        max_steps: int = 50,
        timeout_seconds: float = 120.0,
        context: dict[str, Any] | None = None,
    ) -> AgentTrajectory:
        """Execute the LangGraph graph and record its trajectory.

        Runs ``graph.invoke()`` or ``graph.stream()`` and captures each
        node execution as a step.
        """
        start = time.time()
        failure_injections = list(failure_injections) if failure_injections else []
        latency_injections = latency_injections or []

        try:
            # Try streaming mode first for step-by-step capture
            input_data = self._build_input(prompt, context)

            try:
                result = self._run_streaming(
                    input_data,
                    trajectory,
                    start,
                    failure_injections,
                    latency_injections,
                    max_steps,
                )
            except (TypeError, AttributeError):
                # Fallback to invoke if stream is not supported
                result = self._run_invoke(
                    input_data,
                    trajectory,
                    start,
                    failure_injections,
                    latency_injections,
                    max_steps,
                )

            trajectory.completed = True
            trajectory.total_latency_ms = (time.time() - start) * 1000

            # Extract final response from result
            trajectory.final_response = self._extract_final_response(result)

        except Exception as exc:
            self._record_step(
                trajectory,
                action="error",
                error=str(exc),
                latency_ms=(time.time() - start) * 1000,
            )
            trajectory.completed = False
            trajectory.error = str(exc)
            trajectory.total_latency_ms = (time.time() - start) * 1000

        return trajectory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_input(self, prompt: str, context: dict[str, Any] | None) -> dict[str, Any]:
        """Build the input dict for the graph.

        LangGraph graphs typically expect a ``messages`` key with a list
        of message dicts.
        """
        messages = [{"role": "user", "content": prompt}]
        input_data: dict[str, Any] = {"messages": messages}
        if context:
            input_data.update(context)
        return input_data

    def _run_streaming(
        self,
        input_data: dict[str, Any],
        trajectory: AgentTrajectory,
        start_time: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        max_steps: int,
    ) -> Any:
        """Run the graph in streaming mode and record each node as a step."""
        final_result: dict[str, Any] = {}

        for event in self._graph.stream(input_data):
            if trajectory.step_count >= max_steps:
                break

            step_start = time.time()

            # Each event in stream mode is {node_name: node_output}
            if isinstance(event, dict):
                for node_name, node_output in event.items():
                    self._process_node_output(
                        node_name=node_name,
                        node_output=node_output,
                        trajectory=trajectory,
                        step_start=step_start,
                        failure_injections=failure_injections,
                        latency_injections=latency_injections,
                    )
                final_result.update(event)
            else:
                # Non-dict event — record as-is
                self._record_step(
                    trajectory,
                    action="llm_response",
                    response=str(event),
                    latency_ms=(time.time() - step_start) * 1000,
                )
                final_result["output"] = event

        return final_result

    def _run_invoke(
        self,
        input_data: dict[str, Any],
        trajectory: AgentTrajectory,
        start_time: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        max_steps: int,
    ) -> Any:
        """Run the graph via invoke and parse the result into steps."""
        result = self._graph.invoke(input_data)

        step_start = time.time()

        if isinstance(result, dict):
            # Process each key as a potential node output
            for key, value in result.items():
                if trajectory.step_count >= max_steps:
                    break
                if key == "messages":
                    # Process messages specially
                    self._process_messages(
                        value,
                        trajectory,
                        step_start,
                        failure_injections,
                        latency_injections,
                    )
                else:
                    self._process_node_output(
                        node_name=key,
                        node_output=value,
                        trajectory=trajectory,
                        step_start=step_start,
                        failure_injections=failure_injections,
                        latency_injections=latency_injections,
                    )
        else:
            self._record_step(
                trajectory,
                action="llm_response",
                response=str(result),
                latency_ms=(time.time() - step_start) * 1000,
            )

        return result

    def _process_node_output(
        self,
        node_name: str,
        node_output: Any,
        trajectory: AgentTrajectory,
        step_start: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Process a single node's output and record it as a step."""
        # Map node name to semantic tool name if configured
        effective_name = self._node_name_map.get(node_name, node_name)

        # Check if this node output contains messages (agent/tool nodes)
        if isinstance(node_output, dict) and "messages" in node_output:
            self._process_messages(
                node_output["messages"],
                trajectory,
                step_start,
                failure_injections,
                latency_injections,
            )
            return

        # Check if the output looks like tool call results
        if isinstance(node_output, dict) and "tool_calls" in node_output:
            self._process_tool_calls_dict(
                node_output["tool_calls"],
                node_output,
                trajectory,
                step_start,
                failure_injections,
                latency_injections,
            )
            return

        # Check if output is a list of messages
        if isinstance(node_output, (list, tuple)):
            self._process_messages(
                node_output,
                trajectory,
                step_start,
                failure_injections,
                latency_injections,
            )
            return

        # Determine if this is a tool node based on name conventions
        is_tool_node = "tool" in node_name.lower() or node_name in self._node_name_map

        if is_tool_node:
            # Check failure injection
            fail_msg = self._should_inject_failure(effective_name, failure_injections)
            if fail_msg is not None:
                self._record_step(
                    trajectory,
                    action="error",
                    tool_name=effective_name,
                    error=fail_msg,
                    latency_ms=(time.time() - step_start) * 1000,
                )
                return

            # Apply latency injection
            for inj in latency_injections:
                if inj.tool_name == effective_name:
                    time.sleep(inj.delay_ms / 1000)

            self._record_step(
                trajectory,
                action="tool_call",
                tool_name=effective_name,
                tool_output=node_output,
                latency_ms=(time.time() - step_start) * 1000,
            )
        else:
            # Agent/reasoning node
            self._record_step(
                trajectory,
                action="llm_response",
                response=str(node_output),
                reasoning=f"Node: {node_name}",
                latency_ms=(time.time() - step_start) * 1000,
            )

    def _process_messages(
        self,
        messages: Any,
        trajectory: AgentTrajectory,
        step_start: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Process a list of LangChain/LangGraph messages into steps."""
        if not isinstance(messages, (list, tuple)):
            messages = [messages]

        for msg in messages:
            # Handle both dict-style and object-style messages
            if isinstance(msg, dict):
                msg_type = msg.get("type", msg.get("role", ""))
                content = msg.get("content", "")
                tool_calls = msg.get("tool_calls", [])
                name = msg.get("name", "")
            else:
                msg_type = getattr(msg, "type", "") or getattr(msg, "role", "")
                content = getattr(msg, "content", "")
                tool_calls = getattr(msg, "tool_calls", []) or []
                name = getattr(msg, "name", "")

            # Process tool calls from the message
            if tool_calls:
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        tc_name = tc.get("name", "unknown")
                        tc_args = tc.get("args", {})
                        _tc_id = tc.get("id", "")
                    else:
                        tc_name = getattr(tc, "name", "unknown")
                        tc_args = getattr(tc, "args", {})
                        _tc_id = getattr(tc, "id", "")

                    # Check failure injection
                    fail_msg = self._should_inject_failure(tc_name, failure_injections)
                    if fail_msg is not None:
                        self._record_step(
                            trajectory,
                            action="error",
                            tool_name=tc_name,
                            tool_input=(
                                tc_args if isinstance(tc_args, dict) else {"input": str(tc_args)}
                            ),
                            error=fail_msg,
                            latency_ms=(time.time() - step_start) * 1000,
                        )
                        continue

                    # Apply latency injection
                    for inj in latency_injections:
                        if inj.tool_name == tc_name:
                            time.sleep(inj.delay_ms / 1000)

                    self._record_step(
                        trajectory,
                        action="tool_call",
                        tool_name=tc_name,
                        tool_input=(
                            tc_args if isinstance(tc_args, dict) else {"input": str(tc_args)}
                        ),
                        latency_ms=(time.time() - step_start) * 1000,
                    )

            # Record the message content as a response step
            if content and str(content).strip():
                # Check if this is a tool result message
                if msg_type in ("tool", "function"):
                    tool_name = name or "unknown"
                    fail_msg = self._should_inject_failure(tool_name, failure_injections)
                    if fail_msg is not None:
                        self._record_step(
                            trajectory,
                            action="error",
                            tool_name=tool_name,
                            tool_output=str(content),
                            error=fail_msg,
                            latency_ms=(time.time() - step_start) * 1000,
                        )
                        continue

                    # Apply latency injection
                    for inj in latency_injections:
                        if inj.tool_name == tool_name:
                            time.sleep(inj.delay_ms / 1000)

                    self._record_step(
                        trajectory,
                        action="tool_call",
                        tool_name=tool_name,
                        tool_output=str(content),
                        latency_ms=(time.time() - step_start) * 1000,
                    )
                else:
                    # Regular assistant/ai message
                    reasoning = None
                    if name:
                        reasoning = f"Agent: {name}"
                    self._record_step(
                        trajectory,
                        action="llm_response",
                        response=str(content),
                        reasoning=reasoning,
                        latency_ms=(time.time() - step_start) * 1000,
                    )

    def _process_tool_calls_dict(
        self,
        tool_calls: Any,
        node_output: Any,
        trajectory: AgentTrajectory,
        step_start: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Process a dict containing tool_calls key."""
        if not isinstance(tool_calls, (list, tuple)):
            return

        for tc in tool_calls:
            if isinstance(tc, dict):
                tc_name = tc.get("name", "unknown")
                tc_args = tc.get("args", tc.get("arguments", {}))
            else:
                tc_name = getattr(tc, "name", "unknown")
                tc_args = getattr(tc, "args", getattr(tc, "arguments", {}))

            fail_msg = self._should_inject_failure(tc_name, failure_injections)
            if fail_msg is not None:
                self._record_step(
                    trajectory,
                    action="error",
                    tool_name=tc_name,
                    tool_input=tc_args if isinstance(tc_args, dict) else {"input": str(tc_args)},
                    error=fail_msg,
                    latency_ms=(time.time() - step_start) * 1000,
                )
                continue

            for inj in latency_injections:
                if inj.tool_name == tc_name:
                    time.sleep(inj.delay_ms / 1000)

            self._record_step(
                trajectory,
                action="tool_call",
                tool_name=tc_name,
                tool_input=tc_args if isinstance(tc_args, dict) else {"input": str(tc_args)},
                latency_ms=(time.time() - step_start) * 1000,
            )

    def _extract_final_response(self, result: Any) -> str:
        """Extract the final response text from the graph result."""
        if not isinstance(result, dict):
            return str(result)

        # Try to get the last message content
        messages = result.get("messages", [])
        if messages:
            last_msg = messages[-1]
            if isinstance(last_msg, dict):
                return str(last_msg.get("content", ""))
            elif hasattr(last_msg, "content"):
                return str(last_msg.content)

        # Fallback to any output key
        for key in ("output", "response", "result"):
            if key in result:
                return str(result[key])

        return str(result)
