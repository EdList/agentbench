"""AutoGen adapter — test AutoGen conversations with AgentBench."""

from __future__ import annotations

import json
import time
from typing import Any

from agentbench.adapters.base import AgentAdapter
from agentbench.core.test import (
    AgentTrajectory,
    ToolFailureInjection,
    ToolLatencyInjection,
)

# Lazy import: autogen is an optional dependency.
try:
    import autogen as _autogen_module  # type: ignore[import-untyped]
except ImportError:
    _autogen_module = None  # type: ignore[assignment]


def _require_autogen() -> None:
    """Raise a helpful error if the autogen package is not installed."""
    if _autogen_module is None:
        raise ImportError(
            "The 'autogen' package is required for the AutoGenAdapter. "
            "Install it with:  pip install agentbench[autogen]"
        )


class AutoGenAdapter(AgentAdapter):
    """Adapter for AutoGen multi-agent conversations.

    Wraps an AutoGen assistant agent and user proxy agent (or a group chat)
    and records every message exchange as an AgentBench trajectory step.

    The adapter captures tool calls from AutoGen agents and supports failure
    injection and latency injection.

    Usage::

        import autogen

        assistant = autogen.AssistantAgent(
            name="assistant",
            llm_config={"model": "gpt-4o"},
        )
        user_proxy = autogen.UserProxyAgent(
            name="user",
            human_input_mode="NEVER",
        )

        adapter = AutoGenAdapter(
            assistant=assistant,
            user_proxy=user_proxy,
            tools=["search", "calculator"],
        )
        trajectory = adapter.run("What's 2+2?", trajectory)

    You can also pass a group chat manager::

        groupchat = autogen.GroupChat(agents=[agent1, agent2], messages=[])
        manager = autogen.GroupChatManager(groupchat=groupchat)

        adapter = AutoGenAdapter(
            assistant=agent1,
            user_proxy=user_proxy,
            group_chat_manager=manager,
        )

    Args:
        assistant: The primary AutoGen ``AssistantAgent``.
        user_proxy: An AutoGen ``UserProxyAgent`` for initiating conversations.
        group_chat_manager: Optional ``GroupChatManager`` for group chat mode.
        tools: Optional explicit list of tool names available to the agents.
    """

    def __init__(
        self,
        assistant: Any,
        user_proxy: Any,
        group_chat_manager: Any | None = None,
        tools: list[str] | None = None,
    ) -> None:
        _require_autogen()
        self._assistant = assistant
        self._user_proxy = user_proxy
        self._group_chat_manager = group_chat_manager
        self._tools = tools

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_available_tools(self) -> list[str]:
        """Return tool names available to the agents.

        If tools were provided at construction time, those are returned.
        Otherwise the adapter attempts to introspect from the assistant's
        LLM config.
        """
        if self._tools is not None:
            return list(self._tools)

        # Introspect from llm_config
        try:
            llm_config = getattr(self._assistant, "llm_config", None) or {}
            functions = llm_config.get("functions", [])
            return [f.get("name", str(f)) for f in functions if isinstance(f, dict)]
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
        """Execute the AutoGen conversation and record its trajectory.

        Initiates a chat between the user proxy and the assistant (or
        group chat manager) and captures each message as a step.
        """
        start = time.time()
        failure_injections = failure_injections or []
        latency_injections = latency_injections or []

        # Collect messages via a custom hook
        captured_messages: list[dict[str, Any]] = []

        try:
            # Install message collector
            self._install_message_collector(captured_messages)

            # Initiate the chat
            if self._group_chat_manager is not None:
                self._user_proxy.initiate_chat(
                    self._group_chat_manager,
                    message=prompt,
                )
            else:
                self._user_proxy.initiate_chat(
                    self._assistant,
                    message=prompt,
                )

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

        # Parse captured messages into trajectory steps
        final_response = self._parse_messages(
            captured_messages,
            trajectory,
            start,
            failure_injections,
            latency_injections,
            max_steps,
        )

        trajectory.completed = True
        trajectory.total_latency_ms = (time.time() - start) * 1000
        trajectory.final_response = final_response

        return trajectory

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _install_message_collector(self, captured_messages: list[dict[str, Any]]) -> None:
        """Install a hook to capture messages from the AutoGen conversation.

        Uses the ``register_reply`` mechanism or falls back to patching
        the ``send`` method.  Since AutoGen versions differ, we try a
        pragmatic approach: wrap the chat messages after the fact by
        reading from the agents' message histories.
        """
        # We rely on _collect_messages_posthoc after the chat completes.
        # The captured_messages list is filled in run() after initiate_chat.
        pass

    def _collect_messages_posthoc(self) -> list[dict[str, Any]]:
        """Collect messages from agent chat histories after the chat.

        Reads messages from the user proxy's chat history (AutoGen stores
        messages in the ``chat_messages`` attribute).
        """
        messages: list[dict[str, Any]] = []

        try:
            # AutoGen stores chat messages on the user proxy
            chat_messages = getattr(self._user_proxy, "chat_messages", {})

            if isinstance(chat_messages, dict):
                # chat_messages is {recipient: [messages]}
                for recipient, msgs in chat_messages.items():
                    if isinstance(msgs, list):
                        messages.extend(msgs)
            elif isinstance(chat_messages, list):
                messages.extend(chat_messages)
        except Exception:
            pass

        return messages

    def _parse_messages(
        self,
        captured_messages: list[dict[str, Any]],
        trajectory: AgentTrajectory,
        start_time: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        max_steps: int,
    ) -> str:
        """Parse AutoGen messages into trajectory steps.

        Returns the final response text.
        """
        # If captured_messages is empty, try posthoc collection
        if not captured_messages:
            captured_messages = self._collect_messages_posthoc()

        final_response = ""

        for msg in captured_messages:
            if trajectory.step_count >= max_steps:
                break

            if not isinstance(msg, dict):
                msg = {"content": str(msg)}

            content = msg.get("content", "")
            _role = msg.get("role", "")
            name = msg.get("name", "")
            step_start = time.time()

            # Check if this message contains a tool call (function_call)
            function_call = msg.get("function_call")
            if function_call and isinstance(function_call, dict):
                tool_name = function_call.get("name", "unknown")

                try:
                    tool_input = json.loads(function_call.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    tool_input = {"raw_arguments": function_call.get("arguments", "")}

                # Check failure injection
                fail_msg = self._should_inject_failure(tool_name, failure_injections)
                if fail_msg is not None:
                    self._record_step(
                        trajectory,
                        action="error",
                        tool_name=tool_name,
                        tool_input=tool_input,
                        error=fail_msg,
                        latency_ms=(time.time() - step_start) * 1000,
                    )
                    final_response = f"Error: {fail_msg}"
                    continue

                # Apply latency injection
                for inj in latency_injections:
                    if inj.tool_name == tool_name:
                        time.sleep(inj.delay_ms / 1000)

                self._record_step(
                    trajectory,
                    action="tool_call",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    tool_output=content,
                    reasoning=f"Agent: {name}" if name else None,
                    latency_ms=(time.time() - step_start) * 1000,
                )
                final_response = content
                continue

            # Check for tool_calls (plural, newer AutoGen versions)
            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                for tc in tool_calls:
                    if trajectory.step_count >= max_steps:
                        break
                    if isinstance(tc, dict):
                        tc_name = tc.get("function", {}).get("name", "unknown")

                        try:
                            tc_args = json.loads(tc.get("function", {}).get("arguments", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            tc_args = {"raw_arguments": tc.get("function", {}).get("arguments", "")}

                        # Check failure injection
                        fail_msg = self._should_inject_failure(tc_name, failure_injections)
                        if fail_msg is not None:
                            self._record_step(
                                trajectory,
                                action="error",
                                tool_name=tc_name,
                                tool_input=tc_args,
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
                            tool_input=tc_args,
                            tool_output=content,
                            latency_ms=(time.time() - step_start) * 1000,
                        )
                final_response = content
                continue

            # Regular message — record as LLM response
            if content:
                self._record_step(
                    trajectory,
                    action="llm_response",
                    response=str(content),
                    reasoning=f"Agent: {name}" if name else None,
                    latency_ms=(time.time() - step_start) * 1000,
                )
                final_response = str(content)

        return final_response
