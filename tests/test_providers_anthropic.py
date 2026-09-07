"""AnthropicProvider contract tests — same approach as
test_providers_openai.py: `httpx.MockTransport`, no real network call, no
real credential anywhere in this file.
"""

from __future__ import annotations

import json

import httpx
import pytest

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.providers import LLMMessage, LLMRequest, ProviderError
from workflow_engine.engine.providers_anthropic import AnthropicProvider


def _client_with(handler, *, api_key: str = "test-key") -> httpx.Client:
    transport = httpx.MockTransport(handler)
    return httpx.Client(
        base_url="https://api.anthropic.com/v1",
        transport=transport,
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
    )


def _request(**kwargs: object) -> LLMRequest:
    return LLMRequest(messages=[LLMMessage(role="user", content="what is 2+2?")], **kwargs)  # type: ignore[arg-type]


def test_successful_response_is_parsed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "4"}],
                "usage": {"input_tokens": 5, "output_tokens": 1},
            },
        )

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    response = provider.generate(_request())
    assert response.content == "4"
    assert response.usage.total_tokens == 6


def test_multiple_text_blocks_are_concatenated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        blocks = [{"type": "text", "text": "hello "}, {"type": "text", "text": "world"}]
        return httpx.Response(200, json={"content": blocks})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    response = provider.generate(_request())
    assert response.content == "hello world"


def test_api_key_is_sent_as_x_api_key_header_and_never_logged(caplog) -> None:
    captured: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["key"] = request.headers.get("x-api-key", "")
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        api_key="sk-ant-super-secret-value",
        model="claude-sonnet-4-5",
        client=_client_with(handler, api_key="sk-ant-super-secret-value"),
    )
    provider.generate(_request())

    assert captured["key"] == "sk-ant-super-secret-value"
    assert "sk-ant-super-secret-value" not in caplog.text


def test_system_message_is_sent_as_top_level_field_not_in_messages() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    request = LLMRequest(
        messages=[
            LLMMessage(role="system", content="You are a risk analyst."),
            LLMMessage(role="user", content="Assess this transaction."),
        ]
    )
    provider.generate(request)

    body = captured["body"]
    assert body["system"] == "You are a risk analyst."
    assert body["messages"] == [{"role": "user", "content": "Assess this transaction."}]


def test_max_tokens_defaults_when_not_set_on_request() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        api_key="test-key",
        model="claude-sonnet-4-5",
        client=_client_with(handler),
        default_max_tokens=777,
    )
    provider.generate(_request())
    assert captured["body"]["max_tokens"] == 777

    provider.generate(_request(max_tokens=42))
    assert captured["body"]["max_tokens"] == 42


def test_temperature_is_included_when_set() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    provider.generate(_request(temperature=0.3))
    assert captured["body"]["temperature"] == 0.3


def test_timeout_maps_to_transient_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_server_error_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "unavailable"})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


@pytest.mark.parametrize("status", [429, 529])
def test_rate_limit_and_overload_map_to_transient(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"error": "overloaded"})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_client_error_maps_to_permanent_without_leaking_response_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "invalid x-api-key: sk-ant-leaked"}})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.PERMANENT
    assert "sk-ant-leaked" not in str(exc_info.value)


def test_malformed_response_shape_is_a_permanent_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"unexpected": "shape"})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.PERMANENT


def test_network_error_maps_to_transient() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(_request())
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_missing_usage_defaults_to_zero() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"content": [{"type": "text", "text": "ok"}]})

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=_client_with(handler)
    )
    response = provider.generate(_request())
    assert response.usage.total_tokens == 0


def test_close_delegates_to_underlying_client() -> None:
    closed = {"value": False}

    class FakeClient:
        def close(self) -> None:
            closed["value"] = True

    provider = AnthropicProvider(
        api_key="test-key", model="claude-sonnet-4-5", client=FakeClient()  # type: ignore[arg-type]
    )
    provider.close()
    assert closed["value"] is True
