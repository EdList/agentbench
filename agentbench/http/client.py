"""HTTP client for probing agent endpoints."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlsplit, urlunsplit

import httpx

from agentbench.probes.base import Probe, ProbeResult

# Default timeout per request
DEFAULT_TIMEOUT = 30.0

# CRITICAL-1: Maximum seconds to honour a Retry-After header.  Even if a
# server demands a multi-hour backoff, we cap the sleep so a scan is not
# blocked indefinitely.
MAX_RETRY_AFTER_SLEEP = 60.0

# MEDIUM-4: Reject response bodies larger than this to avoid loading
# multi-GB responses into memory.
MAX_RESPONSE_SIZE = 1_048_576  # 1 MiB

logger = logging.getLogger(__name__)


def redact_url_for_display(url: str) -> str:
    """Remove query values and fragments before logging or serialization."""
    parts = urlsplit(url)
    redacted_query = urlencode([
        (name, "[REDACTED]") for name, _value in parse_qsl(
            parts.query, keep_blank_values=True,
        )
    ])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, redacted_query, ""))


def validate_secure_api_key_transport(
    url: str,
    api_key: str | None,
    *,
    allow_insecure_http: bool = False,
) -> None:
    """Fail closed before sending credentials over cleartext HTTP."""
    if api_key and urlparse(url).scheme.lower() == "http" and not allow_insecure_http:
        raise ValueError(
            "Refusing to send an API key over insecure HTTP; use HTTPS or "
            "explicitly allow insecure HTTP for trusted local development"
        )


async def send_probe(
    url: str,
    probe: Probe,
    *,
    api_key: str | None = None,
    model: str | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    client: httpx.AsyncClient | None = None,
    allow_insecure_http: bool = False,
) -> ProbeResult:
    """Send a single probe to an agent endpoint and return the result.

    Supports OpenAI-compatible chat completions format.
    Falls back to simple JSON-in/JSON-out for non-OpenAI endpoints.

    If *client* is provided, it is used for all HTTP requests (enabling
    connection pooling).  Otherwise a one-shot client is created internally.
    """
    validate_secure_api_key_transport(
        url, api_key, allow_insecure_http=allow_insecure_http,
    )
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
    # AB-H1 FIX: Use shared client if provided (connection pooling); otherwise
    # create a one-shot client for backward compatibility.
    _owns_client = client is None
    active_client: httpx.AsyncClient = (
        httpx.AsyncClient(timeout=timeout) if client is None else client
    )

    try:
        resp = await _post_with_rate_limit(active_client, url, payload, request_headers)
        elapsed = (time.monotonic() - start) * 1000

        result.status_code = resp.status_code
        result.latency_ms = elapsed

        # MEDIUM-4: Reject oversized responses before attempting to parse.
        if _is_oversized(resp):
            result.error = (
                f"Response too large: {_response_size(resp)} bytes "
                f"exceeds limit {MAX_RESPONSE_SIZE}"
            )
            return result

        if resp.status_code >= 400:
            # Some non-OpenAI endpoints reject chat-completions payloads but
            # accept a simple JSON prompt. Retry once only when no model was
            # specified, so OpenAI/OpenRouter-style usage is unaffected.
            if model is None and resp.status_code in (400, 422):
                fallback = await _try_simple_json_fallback(
                    active_client, url, probe, request_headers, start
                )
                if fallback is not None:
                    return fallback
            result.error = f"HTTP {resp.status_code}: {resp.text[:500]}"
            return result

        # Parse response — try OpenAI format
        try:
            body = resp.json()
        except json.JSONDecodeError as exc:
            result.error = _json_decode_error_message(resp, exc)
            return result
        result.response = _extract_response_text(body)

        # Handle follow-ups
        if probe.follow_ups and result.response:
            await _handle_follow_ups(
                active_client, url, probe, payload, request_headers, messages, result
            )

    except httpx.TimeoutException:
        result.error = f"Timeout after {timeout}s"
        result.latency_ms = (time.monotonic() - start) * 1000
    except (httpx.HTTPError, OSError, ValueError) as e:
        result.error = str(e)
        result.latency_ms = (time.monotonic() - start) * 1000
    finally:
        if _owns_client:
            close = getattr(active_client, "aclose", None)
            if close is not None:
                await close()

    return result


async def _handle_follow_ups(
    client: httpx.AsyncClient,
    url: str,
    probe: Probe,
    payload: dict[str, Any],
    request_headers: dict[str, str],
    messages: list[dict[str, str]],
    result: ProbeResult,
) -> None:
    """Send follow-up messages and append responses to *result*."""
    if result.response is None:
        raise ValueError("result.response must not be None when handling follow-ups")
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
                try:
                    fu_body = fu_resp.json()
                except json.JSONDecodeError as exc:
                    fu_text = _json_decode_error_message(fu_resp, exc)
                    result.error = f"Required follow-up failed: {fu_text}"
                    result.follow_up_responses.append(f"[Error: {fu_text}]")
                    break
                fu_text = _extract_response_text(fu_body)
                result.follow_up_responses.append(fu_text)
                messages.append({"role": "assistant", "content": fu_text})
            else:
                error_text = f"Required follow-up failed: HTTP {fu_resp.status_code}"
                result.error = error_text
                result.follow_up_responses.append(f"[HTTP {fu_resp.status_code}]")
                break
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as e:
            logger.debug("follow-up request failed", exc_info=True)
            result.error = f"Required follow-up failed: {e}"
            result.follow_up_responses.append(f"[Error: {e}]")
            break


def _response_size(resp: httpx.Response) -> int:
    """Determine the response body size from Content-Length or actual content."""
    cl = resp.headers.get("content-length")
    if cl:
        try:
            return int(cl)
        except ValueError:
            pass
    return len(resp.content)


def _is_oversized(resp: httpx.Response) -> bool:
    """MEDIUM-4: Return True if the response exceeds MAX_RESPONSE_SIZE."""
    return _response_size(resp) > MAX_RESPONSE_SIZE


async def _post_with_rate_limit(
    client: httpx.AsyncClient,
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
) -> httpx.Response:
    """POST a payload, retrying on rate-limit (429) and server errors (503).

    CRITICAL-1: Retry-After is capped at MAX_RETRY_AFTER_SLEEP so a
    malicious or misconfigured server cannot block the scan indefinitely.
    MEDIUM-5: 503 (Service Unavailable) is retried alongside 429.
    """
    # HIGH-1 FIX: Use streaming so we can check Content-Length BEFORE the
    # body is loaded into memory.  httpx eagerly reads the full body with
    # client.post(); streaming defers the read until aread().
    for attempt in range(3):
        req = client.build_request("POST", url, json=payload, headers=headers)
        resp = await client.send(req, stream=True)

        # Retry on 429 (rate limit) and 503 (service unavailable).
        if resp.status_code in (429, 503):
            # L1 FIX: On the final retry, read the body before closing so the
            # returned response has usable content (status_code + body).
            if attempt + 1 >= 3:
                await _read_response_limited(resp)
                return resp
            await resp.aclose()

            # AB-H2 FIX / CRITICAL-1: Respect Retry-After header but cap at 60s.
            delay: float = 3.0 * (attempt + 1)
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    requested = float(retry_after)
                    if requested > MAX_RETRY_AFTER_SLEEP:
                        logger.warning(
                            "Retry-After %ss exceeds cap %ss — capping",
                            retry_after, MAX_RETRY_AFTER_SLEEP,
                        )
                        delay = MAX_RETRY_AFTER_SLEEP
                    else:
                        delay = max(requested, delay)
                except ValueError:
                    pass  # Retry-After might be HTTP-date; fall back to default
            await asyncio.sleep(delay)
            continue

        # Check Content-Length BEFORE reading the body to avoid loading
        # a multi-GB response into memory.
        cl = resp.headers.get("content-length")
        if cl:
            try:
                cl_int = int(cl)
            except ValueError:
                cl_int = None  # Non-numeric Content-Length — proceed
            if cl_int is not None and cl_int > MAX_RESPONSE_SIZE:
                await resp.aclose()
                raise ValueError(
                    f"Response too large: Content-Length {cl} "
                    f"exceeds limit {MAX_RESPONSE_SIZE}"
                )

        # Enforce the limit while streaming, including chunked and decoded
        # compressed bodies. This avoids buffering an attacker-controlled
        # response before discovering that it is oversized.
        await _read_response_limited(resp)
        return resp

    return resp  # type: ignore[possibly-undefined]


async def _read_response_limited(resp: httpx.Response) -> None:
    """Read a streamed response into memory without exceeding the body cap."""
    content = bytearray()
    try:
        async for chunk in resp.aiter_bytes():
            if len(content) + len(chunk) > MAX_RESPONSE_SIZE:
                raise ValueError(
                    f"Response too large: streamed body exceeds limit {MAX_RESPONSE_SIZE}"
                )
            content.extend(chunk)
    finally:
        await resp.aclose()

    # httpx sets this same private cache from Response.aread(); retaining the
    # buffered bytes keeps downstream .json()/.text behavior unchanged.
    resp._content = bytes(content)


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
    try:
        body = resp.json()
    except json.JSONDecodeError as exc:
        result.error = _json_decode_error_message(resp, exc)
        return result
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
                result.error = f"Required follow-up failed: HTTP {fu_resp.status_code}"
            else:
                try:
                    fu_body = fu_resp.json()
                except json.JSONDecodeError as exc:
                    fu_text = f"[Error: {_json_decode_error_message(fu_resp, exc)}]"
                    result.error = f"Required follow-up failed: {fu_text}"
                else:
                    fu_text = _extract_response_text(fu_body)
        except (httpx.HTTPError, OSError, ValueError, json.JSONDecodeError) as e:
            logger.debug("simple JSON follow-up request failed", exc_info=True)
            result.latency_ms += (time.monotonic() - fu_start) * 1000
            fu_text = f"[Error: {e}]"
            result.error = f"Required follow-up failed: {e}"

        result.follow_up_responses.append(fu_text)
        history.extend([
            {"role": "user", "content": follow_up},
            {"role": "assistant", "content": fu_text},
        ])
        if result.error is not None:
            break

    return result


def _format_simple_prompt(history: list[dict[str, str]], follow_up: str) -> str:
    """Flatten conversation history for simple JSON endpoints."""
    transcript = "\n".join(
        f"{turn['role'].title()}: {turn['content']}" for turn in history
    )
    return f"{transcript}\nUser: {follow_up}"


def _json_decode_error_message(resp: httpx.Response, exc: json.JSONDecodeError) -> str:
    """Build a useful error message for non-JSON responses."""
    content_type = resp.headers.get("content-type", "<missing>")
    snippet = resp.text[:500].replace("\n", "\\n")
    return (
        f"Invalid JSON response: {exc.msg} at line {exc.lineno} column {exc.colno}; "
        f"content-type={content_type!r}; body[:500]={snippet!r}"
    )


def _extract_response_text(body: dict[str, Any]) -> str:
    """Extract the text response from various API response formats."""
    # OpenAI-compatible
    if "choices" in body:
        choices = body["choices"]
        if isinstance(choices, list) and choices:
            choice = choices[0]
            if isinstance(choice, dict):
                message = choice.get("message", {})
                if isinstance(message, dict):
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
