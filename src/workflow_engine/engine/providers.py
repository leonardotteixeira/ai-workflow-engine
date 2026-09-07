"""LLM provider abstraction — DESIGN.md §7.

`LLMNodeExecutor` (node_executor.py) depends only on the `LLMProvider`
protocol here, never on a concrete SDK/HTTP client. `MockLLMProvider` is the
only implementation shipped in this phase: it is fully offline and
deterministic (exact-match on the prompt string), which is what makes the
Fase 4 integration tests runnable without network access or credentials.

A real provider (e.g. calling the Anthropic or OpenAI APIs over HTTP) is
deliberately NOT implemented here — DESIGN.md doesn't yet pick an HTTP client
or specify how credentials would be supplied/rotated, and Fase 4's own brief
only asks for it "if DESIGN.md approves direct HTTP". Adding one now would
mean inventing that decision under the cover of an unrelated phase. This is
documented as a Fase 4 limitation, deferred to DESIGN.md's roadmap phase for
a real provider.

Retry is NOT this module's job (DESIGN.md §7, reaffirmed from Fase 0): a
provider only classifies failures via `ProviderError.category` — the caller
(a future retry-aware layer) decides whether/how to retry.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict

from workflow_engine.domain.enums import ErrorCategory


class ProviderError(Exception):
    """Raised by an `LLMProvider.generate()` call. `category` lets the caller
    (LLMNodeExecutor) translate this into a domain `NodeExecutionError`
    without needing to know anything about the concrete provider.
    """

    def __init__(self, category: ErrorCategory, message: str) -> None:
        self.category = category
        self.message = message
        super().__init__(message)


class LLMMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    role: Literal["system", "user", "assistant"]
    content: str


class LLMRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    messages: list[LLMMessage]
    response_schema: dict[str, Any] | None = None
    max_tokens: int | None = None
    temperature: float | None = None


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


class LLMResponse(BaseModel):
    model_config = ConfigDict(frozen=True)

    content: str | dict[str, Any]
    usage: TokenUsage


class LLMProvider(Protocol):
    def generate(self, request: LLMRequest) -> LLMResponse: ...


class MockLLMProvider:
    """Deterministic, offline provider for tests and demos.

    Configured with an explicit `{prompt_text: LLMResponse | ProviderError}`
    mapping — matched against the last message's `content` — so a test can
    say exactly "prompt X produces response Y" (or "... raises error Z", to
    simulate a provider failure or timeout) without any network call ever
    happening. An unmapped prompt raises `ProviderError` rather than
    guessing, since inventing a plausible-looking response would silently
    hide a test/config mistake.
    """

    def __init__(
        self,
        responses: dict[str, LLMResponse | ProviderError],
        *,
        default: LLMResponse | None = None,
    ) -> None:
        self._responses = responses
        self._default = default

    def generate(self, request: LLMRequest) -> LLMResponse:
        key = request.messages[-1].content if request.messages else ""
        configured = self._responses.get(key)
        if isinstance(configured, ProviderError):
            raise configured
        if configured is not None:
            return configured
        if self._default is not None:
            return self._default
        raise ProviderError(
            category=ErrorCategory.PERMANENT,
            message=f"MockLLMProvider has no configured response for prompt {key!r}",
        )
