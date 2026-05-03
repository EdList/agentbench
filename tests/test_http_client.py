"""Tests for HTTP probe client behavior."""

import asyncio

import httpx
import pytest

from agentbench.http import client as http_client
from agentbench.probes.base import Domain, Probe


@pytest.fixture
def probe():
    return Probe(
        id="fallback-probe",
        domain=Domain.CAPABILITY,
        category="test",
        description="test probe",
        prompt="Hello agent",
    )


def test_send_probe_retries_simple_json_on_400_without_model(monkeypatch, probe):
    requests = []

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            payload = kwargs["json"]
            requests.append(payload)
            if "messages" in payload:
                return httpx.Response(400, json={"error": "bad format"}, request=request)
            return httpx.Response(200, json={"response": "simple response"}, request=request)

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

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            payload = kwargs["json"]
            requests.append(payload)
            if "messages" in payload:
                return httpx.Response(400, json={"error": "bad format"}, request=request)
            if len(requests) == 2:
                return httpx.Response(
                    200, json={"response": "I will remember Mars."}, request=request
                )
            return httpx.Response(200, json={"response": "Mars"}, request=request)

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

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            payload = kwargs["json"]
            requests.append(payload)
            return httpx.Response(400, json={"error": "bad model"}, request=request)

    monkeypatch.setattr(http_client.httpx, "AsyncClient", FakeAsyncClient)

    result = asyncio.run(
        http_client.send_probe("https://agent.test", probe, model="openrouter/model")
    )

    assert result.status_code == 400
    assert result.error is not None
    assert result.error.startswith("HTTP 400")
    assert len(requests) == 1
    assert requests[0]["model"] == "openrouter/model"
