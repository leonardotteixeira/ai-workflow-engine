"""OpenAIProvider — Fase 12 §12.1, the real `LLMProvider` implementation.

Kept in its own module, isolated behind the same `LLMProvider` protocol
`MockLLMProvider` implements (`engine/providers.py`) — nothing else in the
Engine or API knows this exists; swapping providers is a one-line change in
`api/app.py`'s `_build_llm_provider`.

Credentials come from `Settings.openai_api_key` (an environment variable,
never a code default — see `api/config.py`) and are used only in the
`Authorization` header of the one HTTP call this class makes; the key is
never logged, never included in a `ProviderError` message, and never placed
in a URL/query string. Timeout is a real network-level timeout via
`httpx.Timeout`, mapped to `ErrorCategory.TRANSIENT` (matching DESIGN.md §7:
retry is the Engine's job — this provider only classifies, never retries).

Tests for this module run with a mocked `httpx.Client` and need no
credential (Fase 12 §12.1: "Testes do provider devem funcionar sem
credencial") — no real OpenAI account or network access is exercised
anywhere in this repository's test suite, and CI never sets
`OPENAI_API_KEY`.
"""

from __future__ import annotations

import httpx

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.providers import LLMRequest, LLMResponse, ProviderError, TokenUsage


class OpenAIProvider:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self._model = model
        # `client` is injectable so tests can pass a `httpx.Client` bound to
        # a `httpx.MockTransport` instead of hitting the network — the
        # constructor itself never makes a request.
        self._client = client or httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
            headers={"Authorization": f"Bearer {api_key}"},
        )

    def generate(self, request: LLMRequest) -> LLMResponse:
        payload: dict[str, object] = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in request.messages],
        }
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": request.response_schema,
            }

        try:
            response = self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT, message="OpenAI request timed out"
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT, message="OpenAI request failed (network error)"
            ) from exc

        if response.status_code >= 500:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT,
                message=f"OpenAI returned a server error ({response.status_code})",
            )
        if response.status_code == 429:
            raise ProviderError(
                category=ErrorCategory.TRANSIENT, message="OpenAI rate limit exceeded"
            )
        if response.status_code >= 400:
            # Never include the response body: it can echo back request
            # content, and in principle a misconfigured account/proxy could
            # put sensitive detail in an error body we don't control.
            raise ProviderError(
                category=ErrorCategory.PERMANENT,
                message=f"OpenAI rejected the request ({response.status_code})",
            )

        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
            usage_body = body.get("usage", {})
        except (KeyError, IndexError, ValueError) as exc:
            raise ProviderError(
                category=ErrorCategory.PERMANENT,
                message="OpenAI returned an unexpected response shape",
            ) from exc

        usage = TokenUsage(
            prompt_tokens=usage_body.get("prompt_tokens", 0),
            completion_tokens=usage_body.get("completion_tokens", 0),
            total_tokens=usage_body.get("total_tokens", 0),
        )
        return LLMResponse(content=content, usage=usage)

    def close(self) -> None:
        self._client.close()
