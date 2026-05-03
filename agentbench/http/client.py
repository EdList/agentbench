"""HTTP client for probing agent endpoints."""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from agentbench.probes.base import Probe, ProbeResult

# Default timeout per request
DEFAULT_TIMEOUT = 30.0


async def send_probe(
    url: str,
    probe: Probe,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
) -> ProbeResult:
    """Send a single probe to an agent endpoint and return the result.

    Supports OpenAI-compatible chat completions format.
    Falls back to simple JSON-in/JSON-out for non-OpenAI endpoints.
    """
    result = ProbeResult(probe=probe)

    # Build request
    request_headers = {"Content-Type": "application/json"}
    if api_key:
        request_headers["Authorization"] = f"Bearer {api_key}"
    if headers:
        request_headers.update(headers)

    # Build messages
    messages: list[dict[str, str]] = []
    if probe.system_prompt:
        messages.append({"role": "system", "content": probe.system_prompt})
    messages.append({"role": "user", "content": probe.prompt})

    # Try OpenAI-compatible format first
    payload: dict[str, Any] = {
        "messages": messages,
        "max_tokens": 1024,
        "temperature": 0.7,
    }
    if model:
        payload["model"] = model

    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            # Retry on 429 (rate limit)
            for attempt in range(3):
                resp = await client.post(url, json=payload, headers=request_headers)
                if resp.status_code != 429:
                    break
                await asyncio.sleep(3 * (attempt + 1))
            elapsed = (time.monotonic() - start) * 1000

            result.status_code = resp.status_code
            result.latency_ms = elapsed

            if resp.status_code >= 400:
                result.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
                return result

            # Parse response — try OpenAI format
            body = resp.json()
            result.response = _extract_response_text(body)

            # Handle follow-ups
            if probe.follow_ups and result.response:
                messages.append({"role": "assistant", "content": result.response})
                for follow_up in probe.follow_ups:
                    messages.append({"role": "user", "content": follow_up})
                    payload["messages"] = messages
                    fu_start = time.monotonic()
                    try:
                        # Retry on 429 for follow-ups too
                        fu_resp = None
                        for fu_attempt in range(3):
                            fu_resp = await client.post(url, json=payload, headers=request_headers)
                            if fu_resp.status_code != 429:
                                break
                            await asyncio.sleep(3 * (fu_attempt + 1))
                        fu_elapsed = (time.monotonic() - fu_start) * 1000
                        if fu_resp.status_code < 400:
                            fu_body = fu_resp.json()
                            fu_text = _extract_response_text(fu_body)
                            result.follow_up_responses.append(fu_text)
                            messages.append({"role": "assistant", "content": fu_text})
                            result.latency_ms += fu_elapsed
                        else:
                            result.follow_up_responses.append(f"[HTTP {fu_resp.status_code}]")
                    except Exception as e:
                        result.follow_up_responses.append(f"[Error: {e}]")

    except httpx.TimeoutException:
        result.error = f"Timeout after {timeout}s"
        result.latency_ms = (time.monotonic() - start) * 1000
    except Exception as e:
        result.error = str(e)
        result.latency_ms = (time.monotonic() - start) * 1000

    return result


def _extract_response_text(body: dict[str, Any]) -> str:
    """Extract the text response from various API response formats."""
    # OpenAI-compatible
    if "choices" in body:
        choices = body["choices"]
        if choices and isinstance(choices, list):
            choice = choices[0]
            message = choice.get("message", {})
            content = message.get("content", "")
            if content is not None:
                return str(content)

    # Anthropic format (list of content blocks) — check BEFORE plain content
    if "content" in body and isinstance(body["content"], list):
        for block in body["content"]:
            if isinstance(block, dict) and block.get("type") == "text":
                return block.get("text", "")

    # Direct content field (string)
    if "content" in body:
        return str(body["content"])

    # Response field
    if "response" in body:
        return str(body["response"])

    # Output field
    if "output" in body:
        return str(body["output"])

    # Fallback: stringify
    return str(body)[:1000]
