"""Tests for HTTP probe client behavior."""

import asyncio

import httpx
import pytest

from agentbench.http import client as http_client
from agentbench.probes.base import Domain, Probe


def test_streamed_response_limit_stops_before_consuming_remaining_chunks(monkeypatch):
    monkeypatch.setattr(http_client, "MAX_RESPONSE_SIZE", 5)

    class ChunkedResponse:
        def __init__(self):
            self.read_chunks = 0
            self.closed = False

        async def aiter_bytes(self):
            for chunk in (b"1234", b"56", b"unread"):
                self.read_chunks += 1
                yield chunk

        async def aclose(self):
            self.closed = True

    response = ChunkedResponse()

    with pytest.raises(ValueError, match="exceeds limit 5"):
        asyncio.run(http_client._read_response_limited(response))

    assert response.read_chunks == 2
    assert response.closed is True


@pytest.mark.asyncio
async def test_failed_required_follow_up_marks_probe_as_error():
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        if requests == 1:
            return httpx.Response(
                200,
                json={"choices": [{"message": {"content": "Initial refusal"}}]},
            )
        return httpx.Response(500, json={"error": "follow-up failed"})

    multi_turn_probe = Probe(
        id="multi-turn",
        domain=Domain.SAFETY,
        category="test",
        description="required follow-up",
        prompt="First turn",
        follow_ups=["Required second turn"],
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await http_client.send_probe(
            "https://agent.test", multi_turn_probe, client=client,
        )

    assert result.is_error is True
    assert result.error is not None
    assert "follow-up" in result.error.lower()
    assert "500" in result.error


@pytest.fixture
def probe():
    return Probe(
        id="fallback-probe",
        domain=Domain.CAPABILITY,
        category="test",
        description="test probe",
        prompt="Hello agent",
    )


def test_library_rejects_api_key_over_plain_http(probe):
    with pytest.raises(ValueError, match="insecure HTTP"):
        asyncio.run(
            http_client.send_probe(
                "http://agent.test", probe, api_key="target-secret",
            )
        )


def test_report_url_redacts_all_query_values_and_fragment():
    redacted = http_client.redact_url_for_display(
        "https://agent.test/chat?api_key=top-secret&model=private-model#access-token"
    )

    assert redacted == (
        "https://agent.test/chat?api_key=%5BREDACTED%5D&model=%5BREDACTED%5D"
    )
    assert "top-secret" not in redacted
    assert "private-model" not in redacted
    assert "access-token" not in redacted


def test_send_probe_retries_simple_json_on_400_without_model(monkeypatch, probe):
    requests = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def build_request(self, method, url, **kwargs):
            req = httpx.Request(method, url)
            req._test_kwargs = kwargs  # type: ignore[attr-defined]
            return req

        async def send(self, req, *, stream=False):
            url = str(req.url)
            payload = getattr(req, "_test_kwargs", {}).get("json", {})
            requests.append(payload)
            if "messages" in payload:
                return httpx.Response(
                400, json={"error": "bad format"},
                request=httpx.Request("POST", url)
            )
            return httpx.Response(
                200, json={"response": "simple response"},
                request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(http_client.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(http_client.send_probe("https://agent.test", probe))

    assert result.error is None
    assert result.status_code == 200
    assert result.response == "simple response"
    assert len(requests) == 2
    assert "messages" in requests[0]
    assert requests[1] == {"prompt": "Hello agent"}


def test_send_probe_simple_json_fallback_runs_follow_ups(monkeypatch):
    requests = []
    probe = Probe(
        id="fallback-followup",
        domain=Domain.RELIABILITY,
        category="state",
        description="test follow-up fallback",
        prompt="Remember Mars.",
        follow_ups=["What planet did I name?"],
    )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def build_request(self, method, url, **kwargs):
            req = httpx.Request(method, url)
            req._test_kwargs = kwargs  # type: ignore[attr-defined]
            return req

        async def send(self, req, *, stream=False):
            url = str(req.url)
            payload = getattr(req, "_test_kwargs", {}).get("json", {})
            requests.append(payload)
            if "messages" in payload:
                return httpx.Response(
                    400, json={"error": "bad format"},
                    request=httpx.Request("POST", url)
                )
            if len(requests) == 2:
                return httpx.Response(
                    200, json={"response": "I will remember Mars."},
                    request=httpx.Request("POST", url)
                )
            return httpx.Response(
                200, json={"response": "Mars"},
                request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(http_client.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(http_client.send_probe("https://agent.test", probe))

    assert result.error is None
    assert result.response == "I will remember Mars."
    assert result.follow_up_responses == ["Mars"]
    assert len(requests) == 3
    assert "Assistant: I will remember Mars." in requests[2]["prompt"]
    assert requests[2]["prompt"].endswith("User: What planet did I name?")


def test_send_probe_does_not_fallback_when_model_is_specified(monkeypatch, probe):
    requests = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        def build_request(self, method, url, **kwargs):
            req = httpx.Request(method, url)
            req._test_kwargs = kwargs  # type: ignore[attr-defined]
            return req

        async def send(self, req, *, stream=False):
            url = str(req.url)
            payload = getattr(req, "_test_kwargs", {}).get("json", {})
            requests.append(payload)
            return httpx.Response(
                400, json={"error": "bad model"},
                request=httpx.Request("POST", url)
            )

    monkeypatch.setattr(http_client.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        http_client.send_probe("https://agent.test", probe, model="openrouter/model")
    )

    assert result.status_code == 400
    assert result.error is not None
    assert result.error.startswith("HTTP 400")
    assert len(requests) == 1
    assert requests[0]["model"] == "openrouter/model"
