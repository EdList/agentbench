"""Replay engine — replay a recorded workflow against a live agent.

Loads a saved Workflow, replays each user message against the current
agent endpoint, collects the new responses, and returns a new Workflow
that can be diffed against the original.

Usage::

    from agentbench.recorder.workflow import Workflow
    from agentbench.replayer.replayer import ReplayEngine

    baseline = Workflow.load("checkout-flow")
    engine = ReplayEngine(agent_url="https://my-agent.com/v1/chat/completions")
    replayed = engine.replay(baseline)
    replayed.save(base_dir=Path.cwd())
"""

from __future__ import annotations

import time
from typing import Any

import httpx

from agentbench.recorder.workflow import ToolCall, Turn, Workflow


class ReplayError(Exception):
    """Replay failed."""


class ReplayEngine:
    """Replays a recorded workflow against a live agent endpoint.

    Re-sends every ``user_message`` from the original workflow and captures
    the new responses (text, tool calls, timing) into a fresh Workflow.
    """

    def __init__(
        self,
        agent_url: str | None = None,
        agent_format: str = "openai",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        stop_on_error: bool = False,
    ) -> None:
        # If agent_url is None, reuse the original workflow's URL
        self.agent_url = agent_url
        self.agent_format = agent_format
        self.stop_on_error = stop_on_error
        self._headers = headers or {}
        self._timeout = timeout

        if "Content-Type" not in self._headers:
            self._headers["Content-Type"] = "application/json"

    def replay(self, workflow: Workflow) -> Workflow:
        """Replay all turns from *workflow* and return a new Workflow."""
        target_url = self.agent_url or workflow.agent_url
        target_format = self.agent_format or workflow.agent_format

        replayed = Workflow(
            name=f"{workflow.name}-replay",
            agent_url=target_url,
            agent_format=target_format,
            metadata={
                "replay_of": workflow.name,
                "original_created_at": workflow.created_at,
            },
        )

        messages: list[dict[str, Any]] = []
        client = httpx.Client(timeout=self._timeout)

        try:
            for turn in workflow.turns:
                start = time.perf_counter()
                error: str | None = None
                response_text = ""
                tool_calls: list[ToolCall] = []
                extra_meta: dict[str, Any] = {}

                try:
                    if target_format == "openai":
                        response_text, tool_calls, extra_meta = self._send_openai(
                            client, target_url, messages, turn.user_message,
                        )
                    else:
                        response_text, tool_calls, extra_meta = self._send_raw(
                            client, target_url, turn.user_message,
                        )
                except Exception as exc:  # noqa: BLE001
                    error = str(exc)
                    response_text = f"[ERROR] {exc}"

                latency_ms = (time.perf_counter() - start) * 1000

                new_turn = Turn(
                    index=turn.index,
                    user_message=turn.user_message,
                    agent_response=response_text,
                    tool_calls=tool_calls,
                    latency_ms=round(latency_ms, 1),
                    error=error,
                    metadata=extra_meta,
                )

                replayed.add_turn(new_turn)

                if error and self.stop_on_error:
                    break
        finally:
            client.close()

        return replayed

    # -- Private helpers -----------------------------------------------------

    def _send_openai(
        self,
        client: httpx.Client,
        url: str,
        messages: list[dict[str, Any]],
        user_message: str,
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        """Send a single turn via OpenAI format, maintaining message history."""

        messages.append({"role": "user", "content": user_message})
        payload: dict[str, Any] = {"messages": list(messages)}

        resp = client.post(url, json=payload, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""

        tool_calls: list[ToolCall] = []
        for tc in msg.get("tool_calls", []):
            func = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=func.get("name", ""),
                    arguments=func.get("arguments", "{}"),
                )
            )

        # Update history
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            assistant_msg["tool_calls"] = msg.get("tool_calls", [])
        messages.append(assistant_msg)

        metadata = {
            "model": data.get("model", ""),
            "finish_reason": choice.get("finish_reason", ""),
            "usage": data.get("usage", {}),
        }
        return content, tool_calls, metadata

    def _send_raw(
        self,
        client: httpx.Client,
        url: str,
        user_message: str,
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        """Send a single turn via raw JSON format."""
        import json

        payload = {"message": user_message}
        resp = client.post(url, json=payload, headers=self._headers)
        resp.raise_for_status()
        data = resp.json()

        content = (
            data["response"]
            if "response" in data and data["response"] is not None
            else data["content"]
            if "content" in data and data["content"] is not None
            else data["text"]
            if "text" in data and data["text"] is not None
            else str(data)
        )

        tool_calls: list[ToolCall] = []
        for tc in data.get("tool_calls", []):
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", tc.get("function", "")),
                    arguments=json.dumps(tc.get("arguments", tc.get("params", {}))),
                    result=tc.get("result"),
                )
            )

        return content, tool_calls, {}
