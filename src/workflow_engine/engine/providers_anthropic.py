"""AnthropicProvider — a second real `LLMProvider` implementation, alongside
`OpenAIProvider` (providers_openai.py). Same shape, same guarantees: isolated
behind the `LLMProvider` protocol, credentials from `Settings` only (never a
code default), never logged, never included in a `ProviderError` message.

The wire format differs from OpenAI's in one structural way worth noting:
Anthropic's Messages API takes `system` as a top-level request field, not as
a `{"role": "system", ...}` entry inside `messages` — so a `system` role in
`LLMRequest.messages` is extracted and sent that way, and Anthropic also
requires `max_tokens` on every request (OpenAI treats it as optional), so a
default from `Settings.anthropic_max_tokens` is used when the request itself
doesn't specify one.

Tests run against a mocked `httpx.Client` — no real network call, no real
credential — same as `providers_openai.py`.
"""

from __future__ import annotations

import httpx

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.providers import LLMRequest, LLMResponse, ProviderError, TokenUsage

_ANTHROPIC_VERSION = "2023-06-01"


class AnthropicProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.anthropic.com/v1",
        timeout_seconds: float = 30.0,
        default_max_tokens: int = 1024,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        self._default_max_tokens = default_max_tokens
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers={
                "x-api-key": api_key,
                "anthropic-version": _ANTHROPIC_VERSION,
            },
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        system_parts = [m.content for m in request.messages if m.role == "system"]
        messages = [
            {"role": m.role, "content": m.content} for m in request.messages if m.role != "system"
        ]

        payload: dict[str, object] = {
            "model": self._model,
            "messages": messages,
            "max_tokens": request.max_tokens or self._default_max_tokens,
        }
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.temperature is not None:
            payload["temperature"] = request.temperature

        try:
            response = self._client.post("/messages", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT, message="Anthropic request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT,
                message="Anthropic request failed (network error)",
            ) from exc

        if response.status_code >= 500:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT,
                message=f"Anthropic returned a server error ({response.status_code})",
            )
        if response.status_code in (429, 529):  # 529 = Anthropic's "overloaded" status
            raise ProviderError(
                category=ErrorCategory.TRANSIENT, message="Anthropic rate limit/overload"
            )
        if response.status_code >= 400:
            raise ProviderError(
                category=ErrorCategory.PERMANENT,
                message=f"Anthropic rejected the request ({response.status_code})",
            )

        try:
            body = response.json()
            blocks = [b["text"] for b in body["content"] if b.get("type") == "text"]
            content = "".join(blocks)
            usage_body = body.get("usage", {})
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            raise ProviderError(
                category=ErrorCategory.PERMANENT,
                message="Anthropic returned an unexpected response shape",
            ) from exc

        prompt_tokens = usage_body.get("input_tokens", 0)
        completion_tokens = usage_body.get("output_tokens", 0)
        usage = TokenUsage(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        )
        return LLMResponse(content=content, usage=usage)

    def close(self) -> None:
        self._client.close()
