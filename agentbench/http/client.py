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
            resp = await _post_with_rate_limit(client, url, payload, request_headers)
            elapsed = (time.monotonic() - start) * 1000

            result.status_code = resp.status_code
            result.latency_ms = elapsed

            if resp.status_code >= 400:
                # Some non-OpenAI endpoints reject chat-completions payloads but
                # accept a simple JSON prompt. Retry once only when no model was
                # specified, so OpenAI/OpenRouter-style usage is unaffected.
                if model is None and resp.status_code in (400, 422):
                    fallback = await _try_simple_json_fallback(
                        client, url, probe, request_headers, start
                    )
                    if fallback is not None:
                        return fallback
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
                        fu_resp = await _post_with_rate_limit(
                            client, url, payload, request_headers
                        )
                        fu_elapsed = (time.monotonic() - fu_start) * 1000
                        result.latency_ms += fu_elapsed
                        if fu_resp.status_code < 400:
                            fu_body = fu_resp.json()
                            fu_text = _extract_response_text(fu_body)
                            result.follow_up_responses.append(fu_text)
                            messages.append({"role": "assistant", "content": fu_text})
                        else:
                            result.follow_up_responses.append(
                                f"[HTTP {fu_resp.status_code}]"
                            )
                            messages.append({
                                "role": "assistant",
                                "content": result.follow_up_responses[-1],
                            })
                    except Exception as e:
                        result.follow_up_responses.append(f"[Error: {e}]")
                        messages.append({
                            "role": "assistant",
                            "content": result.follow_up_responses[-1],
                        })

    except httpx.TimeoutException:
        result.error = f"Timeout after {timeout}s"
        result.latency_ms = (time.monotonic() - start) * 1000
    except Exception as e:
        result.error = str(e)
        result.latency_ms = (time.monotonic() - start) * 1000

    return result


async def _post_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    """POST a payload, retrying briefly on rate limits."""
    for attempt in range(3):
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code != 429:
            return resp
        await asyncio.sleep(3 * (attempt + 1))
    return resp


async def _try_simple_json_fallback(
    client: httpx.AsyncClient,
    url: str,
    probe: Probe,
    headers: dict[str, str],
    start: float,
) -> ProbeResult | None:
    """Retry using simple JSON-in/JSON-out.

    Returns a populated result only if the fallback succeeds. If it also fails,
    the caller keeps reporting the original OpenAI-format error.
    """
    resp = await _post_with_rate_limit(client, url, {"prompt": probe.prompt}, headers)
    if resp.status_code >= 400:
        return None

    result = ProbeResult(probe=probe)
    result.status_code = resp.status_code
    result.latency_ms = (time.monotonic() - start) * 1000
    body = resp.json()
    result.response = _extract_response_text(body)

    history: list[dict[str, str]] = [
        {"role": "user", "content": probe.prompt},
        {"role": "assistant", "content": result.response},
    ]
    for follow_up in probe.follow_ups:
        fu_start = time.monotonic()
        fu_payload = {"prompt": _format_simple_prompt(history, follow_up)}
        try:
            fu_resp = await _post_with_rate_limit(client, url, fu_payload, headers)
            result.latency_ms += (time.monotonic() - fu_start) * 1000
            if fu_resp.status_code >= 400:
                fu_text = f"[HTTP {fu_resp.status_code}]"
            else:
                fu_text = _extract_response_text(fu_resp.json())
        except Exception as e:
            result.latency_ms += (time.monotonic() - fu_start) * 1000
            fu_text = f"[Error: {e}]"

        result.follow_up_responses.append(fu_text)
        history.extend([
            {"role": "user", "content": follow_up},
            {"role": "assistant", "content": fu_text},
        ])

    return result


def _format_simple_prompt(history: list[dict[str, str]], follow_up: str) -> str:
    """Flatten conversation history for simple JSON endpoints."""
    transcript = "\n".join(
        f"{turn['role'].title()}: {turn['content']}" for turn in history
    )
    return f"{transcript}\nUser: {follow_up}"


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
            # content was explicitly None (e.g., function-calling response)
            return ""

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
