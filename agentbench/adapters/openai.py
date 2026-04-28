"""OpenAI Assistants API adapter — test OpenAI Assistants with AgentBench."""

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

# Lazy import: the openai package is an optional dependency.
# Install with:  pip install agentbench[openai]
try:
    import openai as _openai_module
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _openai_module = None  # type: ignore[assignment]
    _OpenAI = None  # type: ignore[assignment,misc]


def _require_openai() -> None:
    """Raise a helpful error if the openai package is not installed."""
    if _openai_module is None:
        raise ImportError(
            "The 'openai' package is required for the OpenAIAdapter. "
            "Install it with:  pip install agentbench[openai]"
        )


class OpenAIAdapter(AgentAdapter):
    """Adapter for OpenAI Assistants API (threads, runs, tool calls).

    Wraps an OpenAI Assistant and records every step of its execution —
    LLM responses, tool calls, and errors — as an AgentBench trajectory.

    The adapter intercepts tool calls via the run steps API, records each
    as an ``AgentStep``, and supports failure injection and latency injection.

    Usage::

        from openai import OpenAI

        client = OpenAI()
        assistant = client.beta.assistants.create(
            name="My Agent",
            model="gpt-4o",
            tools=[{"type": "function", "function": {...}}],
        )

        adapter = OpenAIAdapter(
            client=client,
            assistant_id=assistant.id,
            tools=["search", "calculator"],
        )
        trajectory = adapter.run("What's 2+2?", trajectory)

    Args:
        client: An ``openai.OpenAI`` instance.
        assistant_id: The ID of the OpenAI Assistant to use.
        tools: Optional explicit list of tool names available to the agent.
            If not provided, the adapter will attempt to introspect the
            assistant's tool definitions.
        poll_interval: Seconds between status polls when waiting for a run
            to complete (default 0.5).
    """

    def __init__(
        self,
        client: Any,
        assistant_id: str,
        tools: list[str] | None = None,
        poll_interval: float = 0.5,
    ) -> None:
        _require_openai()
        self._client = client
        self._assistant_id = assistant_id
        self._tools = tools
        self._poll_interval = poll_interval

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def get_available_tools(self) -> list[str]:
        """Return tool names available to the assistant.

        If tools were provided at construction time, those are returned.
        Otherwise the assistant definition is fetched from the API and the
        function tool names are extracted.
        """
        if self._tools is not None:
            return list(self._tools)

        # Introspect from the assistant definition
        try:
            assistant = self._client.beta.assistants.retrieve(self._assistant_id)
            names: list[str] = []
            for tool in assistant.tools or []:
                # tool can be a FunctionTool, CodeInterpreterTool, etc.
                tool_type = getattr(tool, "type", None)
                if tool_type == "function":
                    func = getattr(tool, "function", None)
                    if func and hasattr(func, "name"):
                        names.append(func.name)
                    elif isinstance(func, dict):
                        names.append(func.get("name", "unknown"))
                else:
                    # Non-function tools (code_interpreter, retrieval, file_search)
                    names.append(tool_type or "unknown")
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
        """Execute the OpenAI Assistant and record its trajectory.

        Creates a thread, adds the user message, creates a run, then polls
        until completion.  Tool calls submitted by the assistant are
        intercepted via the run steps API.  For each ``requires_action``
        status the adapter resolves tool calls (with failure/latency
        injection) and submits outputs back to the run.
        """
        start = time.time()
        failure_injections = failure_injections or []
        latency_injections = latency_injections or []

        try:
            thread, run_obj = self._create_thread_and_run(prompt)
            trajectory = self._poll_and_resolve(
                thread=thread,
                run_obj=run_obj,
                trajectory=trajectory,
                failure_injections=failure_injections,
                latency_injections=latency_injections,
                max_steps=max_steps,
                timeout_seconds=timeout_seconds,
                start_time=start,
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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _create_thread_and_run(self, prompt: str) -> tuple[Any, Any]:
        """Create a thread with the user message, then start a run."""
        thread = self._client.beta.threads.create()
        self._client.beta.threads.messages.create(
            thread_id=thread.id,
            role="user",
            content=prompt,
        )
        run_obj = self._client.beta.threads.runs.create(
            thread_id=thread.id,
            assistant_id=self._assistant_id,
        )
        return thread, run_obj

    def _poll_and_resolve(
        self,
        thread: Any,
        run_obj: Any,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        max_steps: int,
        timeout_seconds: float,
        start_time: float,
    ) -> AgentTrajectory:
        """Poll the run until terminal state, resolving tool calls.

        On each poll cycle:
        1. Check timeout / max_steps.
        2. If ``requires_action``, resolve tool calls and submit outputs.
        3. If completed / failed / expired / cancelled, break.
        4. Record all new steps from run_steps.
        """
        seen_step_ids: set[str] = set()

        while True:
            elapsed = time.time() - start_time
            if elapsed >= timeout_seconds:
                self._record_step(
                    trajectory,
                    action="error",
                    error=f"OpenAI run timed out after {timeout_seconds}s",
                    latency_ms=elapsed * 1000,
                )
                trajectory.completed = False
                trajectory.error = "timeout"
                trajectory.total_latency_ms = elapsed * 1000
                return trajectory

            if trajectory.step_count >= max_steps:
                self._record_step(
                    trajectory,
                    action="error",
                    error=f"Max steps ({max_steps}) exceeded",
                    latency_ms=(time.time() - start_time) * 1000,
                )
                trajectory.completed = False
                trajectory.error = "max_steps_exceeded"
                trajectory.total_latency_ms = (time.time() - start_time) * 1000
                return trajectory

            # Re-fetch run status
            run_obj = self._client.beta.threads.runs.retrieve(
                thread_id=thread.id,
                run_id=run_obj.id,
            )

            # Record any new steps from the run_steps API
            self._record_new_steps(
                thread=thread,
                run_obj=run_obj,
                trajectory=trajectory,
                seen_step_ids=seen_step_ids,
                start_time=start_time,
                failure_injections=failure_injections,
                latency_injections=latency_injections,
            )

            status = run_obj.status

            # ---- Handle requires_action (tool calls) ----
            if status == "requires_action":
                run_obj = self._resolve_tool_calls(
                    thread=thread,
                    run_obj=run_obj,
                    trajectory=trajectory,
                    failure_injections=failure_injections,
                    latency_injections=latency_injections,
                    seen_step_ids=seen_step_ids,
                    start_time=start_time,
                )
                continue

            # ---- Terminal states ----
            if status == "completed":
                trajectory.completed = True
                trajectory.total_latency_ms = (time.time() - start_time) * 1000
                # Extract final assistant message
                trajectory.final_response = self._extract_final_message(thread)
                return trajectory

            if status in ("failed", "expired", "cancelled"):
                error_msg = self._describe_run_failure(run_obj)
                self._record_step(
                    trajectory,
                    action="error",
                    error=error_msg,
                    latency_ms=(time.time() - start_time) * 1000,
                )
                trajectory.completed = False
                trajectory.error = error_msg
                trajectory.total_latency_ms = (time.time() - start_time) * 1000
                return trajectory

            # Still in progress — wait and poll again
            time.sleep(self._poll_interval)

    def _resolve_tool_calls(
        self,
        thread: Any,
        run_obj: Any,
        trajectory: AgentTrajectory,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
        seen_step_ids: set[str],
        start_time: float,
    ) -> Any:
        """Handle a ``requires_action`` status by submitting tool outputs.

        For each tool call requested by the assistant:
        1. Record a ``tool_call`` step (or ``error`` if failure-injected).
        2. Apply latency injection if configured.
        3. Submit the tool output back to the run.

        Returns the updated run object.
        """
        required_action = run_obj.required_action
        if not required_action:
            return run_obj

        tool_calls = required_action.submit_tool_outputs.tool_calls
        tool_outputs: list[dict[str, str]] = []

        for tc in tool_calls:
            tool_name = tc.function.name
            tool_input: dict[str, Any] = {}
            try:
                tool_input = json.loads(tc.function.arguments)
            except (json.JSONDecodeError, TypeError):
                tool_input = {"raw_arguments": tc.function.arguments}

            # --- Failure injection ---
            fail_msg = self._should_inject_failure(tool_name, failure_injections)
            if fail_msg is not None:
                self._record_step(
                    trajectory,
                    action="error",
                    tool_name=tool_name,
                    tool_input=tool_input,
                    error=fail_msg,
                    latency_ms=(time.time() - start_time) * 1000,
                )
                tool_outputs.append(
                    {
                        "tool_call_id": tc.id,
                        "output": json.dumps({"error": fail_msg}),
                    }
                )
                continue

            # --- Latency injection ---
            for inj in latency_injections:
                if inj.tool_name == tool_name:
                    time.sleep(inj.delay_ms / 1000)

            # --- Record the tool_call step ---
            # We record with a placeholder output; the actual output will
            # come from the tool execution on OpenAI's side or from the
            # run_steps after completion.  For now we note that the
            # assistant *requested* this tool call.
            self._record_step(
                trajectory,
                action="tool_call",
                tool_name=tool_name,
                tool_input=tool_input,
                latency_ms=(time.time() - start_time) * 1000,
            )

            # In a real integration, users would provide a tool executor.
            # For the adapter's purposes, we submit a placeholder that
            # indicates the tool was called.  Consumers can override
            # behavior by subclassing and overriding this method.
            tool_outputs.append(
                {
                    "tool_call_id": tc.id,
                    "output": json.dumps({"result": f"tool_call_{tool_name}_executed"}),
                }
            )

        # Submit all tool outputs back to the run
        if tool_outputs:
            run_obj = self._client.beta.threads.runs.submit_tool_outputs(
                thread_id=thread.id,
                run_id=run_obj.id,
                tool_outputs=tool_outputs,  # type: ignore[arg-type]
            )

        return run_obj

    def _record_new_steps(
        self,
        thread: Any,
        run_obj: Any,
        trajectory: AgentTrajectory,
        seen_step_ids: set[str],
        start_time: float,
        failure_injections: list[ToolFailureInjection],
        latency_injections: list[ToolLatencyInjection],
    ) -> None:
        """Fetch run steps and record any new ones into the trajectory.

        This captures LLM message-creation steps that aren't tool calls
        (e.g., the assistant's reasoning / final response text).
        Tool-call steps that were already recorded in ``_resolve_tool_calls``
        are tracked via *seen_step_ids* to avoid duplicates.
        """
        try:
            steps_page = self._client.beta.threads.runs.steps.list(
                thread_id=thread.id,
                run_id=run_obj.id,
                limit=100,
            )
        except Exception:
            return

        for step in steps_page.data:
            if step.id in seen_step_ids:
                continue
            seen_step_ids.add(step.id)

            _step_type = getattr(step, "type", None) or getattr(step, "step_details", None)

            # Determine the actual type from step_details if available
            step_details = getattr(step, "step_details", None)

            if step_details is not None:
                detail_type = getattr(step_details, "type", None)

                if detail_type == "message_creation":
                    # This is an LLM response step
                    msg_info = getattr(step_details, "message_creation", None)
                    message_id = None
                    if msg_info:
                        message_id = getattr(msg_info, "message_id", None)

                    content = self._fetch_message_content(thread, message_id) if message_id else ""
                    step_latency = (
                        ((step.completed_at - step.started_at) * 1000)
                        if step.started_at and step.completed_at
                        else (time.time() - start_time) * 1000
                    )

                    self._record_step(
                        trajectory,
                        action="llm_response",
                        response=content,
                        reasoning=content,
                        latency_ms=step_latency,
                    )

                elif detail_type == "tool_calls":
                    # Tool calls are handled in _resolve_tool_calls, but
                    # we still mark them as seen so we don't re-process.
                    # If they weren't handled (e.g. code_interpreter tools),
                    # record them here.
                    tool_calls_detail = getattr(step_details, "tool_calls", [])
                    for tc_detail in tool_calls_detail or []:
                        tc_type = getattr(tc_detail, "type", None)
                        if tc_type == "function":
                            # Already handled in _resolve_tool_calls — skip.
                            continue
                        # Non-function tool (code_interpreter, retrieval, file_search)
                        tc_name = tc_type or "unknown"
                        tc_input = {}
                        if hasattr(tc_detail, "input"):
                            tc_input_val = getattr(tc_detail, "input")
                            if isinstance(tc_input_val, str):
                                try:
                                    tc_input = json.loads(tc_input_val)
                                except (json.JSONDecodeError, TypeError):
                                    tc_input = {"input": tc_input_val}
                            elif isinstance(tc_input_val, dict):
                                tc_input = tc_input_val

                        tc_output = ""
                        if hasattr(tc_detail, "output"):
                            tc_output = getattr(tc_detail, "output", "") or ""

                        # Check failure injection
                        fail_msg = self._should_inject_failure(tc_name, failure_injections)
                        if fail_msg is not None:
                            self._record_step(
                                trajectory,
                                action="error",
                                tool_name=tc_name,
                                tool_input=tc_input,
                                error=fail_msg,
                                latency_ms=(time.time() - start_time) * 1000,
                            )
                            continue

                        # Check latency injection
                        for inj in latency_injections:
                            if inj.tool_name == tc_name:
                                time.sleep(inj.delay_ms / 1000)

                        self._record_step(
                            trajectory,
                            action="tool_call",
                            tool_name=tc_name,
                            tool_input=tc_input,
                            tool_output=tc_output,
                            latency_ms=(time.time() - start_time) * 1000,
                        )

    def _fetch_message_content(self, thread: Any, message_id: str | None) -> str:
        """Fetch the text content of a thread message."""
        if not message_id:
            return ""
        try:
            message = self._client.beta.threads.messages.retrieve(
                thread_id=thread.id,
                message_id=message_id,
            )
            parts: list[str] = []
            for content_block in message.content or []:
                text = getattr(content_block, "text", None)
                if text:
                    value = getattr(text, "value", None)
                    if value:
                        parts.append(value)
            return "\n".join(parts)
        except Exception:
            return ""

    def _extract_final_message(self, thread: Any) -> str:
        """Retrieve the last assistant message from the thread."""
        try:
            messages = self._client.beta.threads.messages.list(
                thread_id=thread.id,
                order="desc",
                limit=1,
            )
            for msg in messages.data:
                if msg.role == "assistant":
                    parts: list[str] = []
                    for content_block in msg.content or []:
                        text = getattr(content_block, "text", None)
                        if text:
                            value = getattr(text, "value", None)
                            if value:
                                parts.append(value)
                    return "\n".join(parts)
        except Exception:
            pass
        return ""

    @staticmethod
    def _describe_run_failure(run_obj: Any) -> str:
        """Build a human-readable error string for a failed run."""
        status = getattr(run_obj, "status", "unknown")
        last_error = getattr(run_obj, "last_error", None)
        if last_error:
            code = getattr(last_error, "code", "")
            message = getattr(last_error, "message", "")
            return f"Run {status}: {code} — {message}"
        return f"Run {status}"
