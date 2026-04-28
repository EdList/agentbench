"""Session recorder — captures live agent interactions as Workflows.

Supports two agent endpoint formats:

* **openai** — OpenAI-compatible ``/v1/chat/completions`` API (default).
  Sends ``{"messages": [...]}`` payloads and captures ``tool_calls``.
* **raw** — Simple JSON POST.  Sends ``{"message": "..."}`` and reads the
  ``response`` (or ``content`` / ``text``) key from the JSON body.

Usage::

    recorder = SessionRecorder(
        agent_url="https://api.openai.com/v1/chat/completions",
        workflow_name="checkout-flow",
        agent_format="openai",
        headers={"Authorization": "Bearer sk-..."},
    )

    turn = recorder.send("Buy me a blue shirt, size M")
    print(turn.agent_response)

    workflow = recorder.finish()
    workflow.save()
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from agentbench.recorder.workflow import ToolCall, Turn, Workflow


class RecorderError(Exception):
    """Recording failed."""


class SessionRecorder:
    """Records a multi-turn interaction with an agent HTTP endpoint.

    Maintains conversation history (for OpenAI-format endpoints) and
    accumulates :class:`Turn` instances into a :class:`Workflow`.
    """

    def __init__(
        self,
        agent_url: str,
        workflow_name: str,
        agent_format: str = "openai",
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.agent_url = agent_url
        self.agent_format = agent_format
        self.timeout = timeout
        self._headers = headers or {}

        # Ensure Content-Type is set
        if "Content-Type" not in self._headers:
            self._headers["Content-Type"] = "application/json"

        self._workflow = Workflow(
            name=workflow_name,
            agent_url=agent_url,
            agent_format=agent_format,
            metadata=metadata or {},
        )
        self._turn_index = 0
        self._messages: list[dict[str, Any]] = []
        self._client = httpx.Client(timeout=timeout)

    @property
    def workflow(self) -> Workflow:
        return self._workflow

    @property
    def turn_count(self) -> int:
        return self._turn_index

    def send(self, message: str) -> Turn:
        """Send a user message and record the agent's response.

        Returns a :class:`Turn` with the full interaction details
        (response text, tool calls, latency, metadata).
        """
        start = time.perf_counter()
        error: str | None = None
        response_text = ""
        tool_calls: list[ToolCall] = []
        extra_metadata: dict[str, Any] = {}

        try:
            if self.agent_format == "openai":
                response_text, tool_calls, extra_metadata = self._send_openai(
                    message
                )
            else:
                response_text, tool_calls, extra_metadata = self._send_raw(message)
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            response_text = f"[ERROR] {exc}"

        latency_ms = (time.perf_counter() - start) * 1000

        turn = Turn(
            index=self._turn_index,
            user_message=message,
            agent_response=response_text,
            tool_calls=tool_calls,
            latency_ms=round(latency_ms, 1),
            error=error,
            metadata=extra_metadata,
        )

        self._workflow.add_turn(turn)
        self._turn_index += 1
        return turn

    def finish(self) -> Workflow:
        """Finish recording, close the HTTP client, and return the workflow."""
        self._client.close()
        return self._workflow

    def cancel(self) -> None:
        """Cancel recording without saving."""
        self._client.close()

    # -- Private: OpenAI format ----------------------------------------------

    def _send_openai(
        self, message: str
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        self._messages.append({"role": "user", "content": message})

        payload: dict[str, Any] = {"messages": self._messages}

        resp = self._client.post(
            self.agent_url, json=payload, headers=self._headers
        )
        resp.raise_for_status()
        data = resp.json()

        choice = data.get("choices", [{}])[0]
        msg = choice.get("message", {})
        content = msg.get("content", "") or ""

        # Extract tool calls
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

        # Update conversation history with assistant response
        assistant_msg: dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            # Store raw tool_calls for conversation continuity
            assistant_msg["tool_calls"] = msg.get("tool_calls", [])
        self._messages.append(assistant_msg)

        metadata = {
            "model": data.get("model", ""),
            "finish_reason": choice.get("finish_reason", ""),
            "usage": data.get("usage", {}),
        }

        return content, tool_calls, metadata

    # -- Private: Raw format -------------------------------------------------

    def _send_raw(
        self, message: str
    ) -> tuple[str, list[ToolCall], dict[str, Any]]:
        payload = {"message": message}

        resp = self._client.post(
            self.agent_url, json=payload, headers=self._headers
        )
        resp.raise_for_status()
        data = resp.json()

        # Flexible response key extraction
        content = (
            data.get("response")
            or data.get("content")
            or data.get("text")
            or str(data)
        )

        # Extract tool calls if present
        tool_calls: list[ToolCall] = []
        for tc in data.get("tool_calls", []):
            tool_calls.append(
                ToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("name", tc.get("function", "")),
                    arguments=json.dumps(
                        tc.get("arguments", tc.get("params", {}))
                    ),
                    result=tc.get("result"),
                )
            )

        return content, tool_calls, {}
