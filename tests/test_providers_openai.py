"""Fase 12 §12.1 — OpenAIProvider contract tests.

All requests go through `httpx.MockTransport`: no real network call, no
credential needed anywhere in this file (a fake `api_key="test-key"` is used
purely to prove it lands in the Authorization header, never that a real key
works). This is exactly the "test contract with mocks and declare it"
approach the brief calls for when real credentials aren't available.
"""

from __future__ import annotations

import json

import httpx
import pytest

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.providers import LLMMessage, LLMRequest, ProviderError
from workflow_engine.engine.providers_openai import OpenAIProvider


def _client_with(handler, *, api_key: str = "test-key") -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(
        base_url="https://api.openai.com/v1",
        transport=transport,
        headers={"Authorization": f"Bearer {api_key}"},
    )


def _request() -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="what is 2+2?")])


def test_successful_response_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "4"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 1, "total_tokens": 6},
            },
        )

    provider = OpenAIProvider(
        api_key="test-key", model="gpt-4o-mini", client=_client_with(handler)
    )
    response = provider.generate(_request())
    assert response.content == "4"
    assert response.usage.total_tokens == 6


def test_api_key_is_sent_as_bearer_header_and_never_logged(caplog) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = OpenAIProvider(
        api_key="sk-super-secret-value",
        model="gpt-4o-mini",
        client=_client_with(handler, api_key="sk-super-secret-value"),
    )
    provider.generate(_request())

    assert captured["auth"] == "Bearer sk-super-secret-value"
    assert "sk-super-secret-value" not in caplog.text


def test_timeout_maps_to_transient_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT
    assert "test-key" not in str(exc_info.value)


def test_server_error_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_rate_limit_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_client_error_maps_to_permanent_without_leaking_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid api key: sk-leaked-value"}})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.PERMANENT
    assert "sk-leaked-value" not in str(exc_info.value)


def test_malformed_response_shape_is_a_permanent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.PERMANENT


def test_network_error_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_request_payload_includes_model_and_messages() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    provider.generate(_request())

    body = captured["body"]
    assert body["model"] == "gpt-4o-mini"
    assert body["messages"] == [{"role": "user", "content": "what is 2+2?"}]


def test_request_payload_includes_optional_fields_when_set() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    request = LLMRequest(
        messages=[LLMMessage(role="user", content="hi")],
        max_tokens=50,
        temperature=0.2,
        response_schema={"name": "answer", "schema": {"type": "object"}},
    )
    provider.generate(request)

    body = captured["body"]
    assert body["max_tokens"] == 50
    assert body["temperature"] == 0.2
    assert body["response_format"]["json_schema"] == request.response_schema


def test_close_delegates_to_underlying_client() -> None:
    closed = {"value": False}

    class FakeClient:
        def close(self) -> None:
            closed["value"] = True

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=FakeClient())  # type: ignore[arg-type]
    provider.close()
    assert closed["value"] is True


def test_missing_usage_defaults_to_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    provider = OpenAIProvider(api_key="test-key", model="gpt-4o-mini", client=_client_with(handler))
    response = provider.generate(_request())
    assert response.usage.total_tokens == 0
