from __future__ import annotations

import pytest

from workflow_engine.domain.enums import ErrorCategory
from workflow_engine.engine.providers import (
    LLMMessage,
    LLMRequest,
    LLMResponse,
    MockLLMProvider,
    ProviderError,
    TokenUsage,
)


def _usage(n: int = 10) -> TokenUsage:
    return TokenUsage(prompt_tokens=n, completion_tokens=n, total_tokens=2 * n)


def test_mock_provider_returns_configured_response_for_exact_prompt() -> None:
    provider = MockLLMProvider(
        {"classify this risk": LLMResponse(content="HIGH", usage=_usage())}
    )
    response = provider.generate(
        LLMRequest(messages=[LLMMessage(role="user", content="classify this risk")])
    )
    assert response.content == "HIGH"
    assert response.usage.total_tokens == 20


def test_mock_provider_supports_structured_dict_content() -> None:
    provider = MockLLMProvider(
        {"classify": LLMResponse(content={"risk_score": 91}, usage=_usage())}
    )
    response = provider.generate(LLMRequest(messages=[LLMMessage(role="user", content="classify")]))
    assert response.content == {"risk_score": 91}


def test_mock_provider_raises_configured_provider_error() -> None:
    provider = MockLLMProvider(
        {
            "timeout-prone": ProviderError(
                category=ErrorCategory.TRANSIENT, message="simulated timeout"
            )
        }
    )
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(LLMRequest(messages=[LLMMessage(role="user", content="timeout-prone")]))
    assert exc_info.value.category == ErrorCategory.TRANSIENT


def test_mock_provider_falls_back_to_default() -> None:
    default = LLMResponse(content="fallback", usage=_usage())
    provider = MockLLMProvider({}, default=default)
    response = provider.generate(LLMRequest(messages=[LLMMessage(role="user", content="anything")]))
    assert response.content == "fallback"


def test_mock_provider_raises_permanent_error_for_unconfigured_prompt() -> None:
    provider = MockLLMProvider({})
    with pytest.raises(ProviderError) as exc_info:
        provider.generate(LLMRequest(messages=[LLMMessage(role="user", content="unmapped")]))
    assert exc_info.value.category == ErrorCategory.PERMANENT


def test_mock_provider_is_deterministic() -> None:
    provider = MockLLMProvider({"x": LLMResponse(content="y", usage=_usage())})
    request = LLMRequest(messages=[LLMMessage(role="user", content="x")])
    results = {provider.generate(request).content for _ in range(20)}
    assert results == {"y"}


def test_mock_provider_never_makes_network_calls() -> None:
    """No socket/httpx import anywhere in the providers module — verified by
    AST, mirroring the domain-layer security test."""
    import ast
    import importlib
    import pathlib

    source = pathlib.Path(
        importlib.import_module("workflow_engine.engine.providers").__file__  # type: ignore[arg-type]
    ).read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    forbidden = {"socket", "httpx", "requests", "openai", "anthropic", "urllib"}
    assert not any(m.split(".")[0] in forbidden for m in imported)
