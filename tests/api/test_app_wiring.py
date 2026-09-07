"""Fase 10 — app-level wiring: lifespan, CORS, body-size limit, and the
generic-500 safety net for exception types the explicit status map doesn't
know about.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from workflow_engine.api.app import create_app
from workflow_engine.api.config import Settings


def test_openai_provider_with_api_key_starts_without_network_calls() -> None:
    """Constructing `httpx.Client` never makes a request by itself — this
    just proves the app wires an OpenAIProvider in when configured, with no
    real credential or network access needed."""
    from workflow_engine.engine.providers_openai import OpenAIProvider

    settings = Settings(
        DATABASE_URL=":memory:", LLM_PROVIDER="openai", OPENAI_API_KEY="sk-fake-for-wiring-test"
    )
    app = create_app(settings)
    with TestClient(app):
        assert isinstance(app.state.llm_provider, OpenAIProvider)
    app.state.llm_provider.close()


def test_anthropic_provider_with_api_key_starts_without_network_calls() -> None:
    from workflow_engine.engine.providers_anthropic import AnthropicProvider

    settings = Settings(
        DATABASE_URL=":memory:",
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="sk-ant-fake-for-wiring-test",
    )
    app = create_app(settings)
    with TestClient(app):
        assert isinstance(app.state.llm_provider, AnthropicProvider)
    app.state.llm_provider.close()


def test_anthropic_provider_without_api_key_refuses_to_start() -> None:
    settings = Settings(DATABASE_URL=":memory:", LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=None)
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="ANTHROPIC_API_KEY"), TestClient(app):
        pass


def test_get_settings_reads_environment_and_is_cached(monkeypatch) -> None:
    from workflow_engine.api.config import get_settings

    get_settings.cache_clear()
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    first = get_settings()
    second = get_settings()
    assert first is second  # cached
    assert first.log_level == "DEBUG"
    get_settings.cache_clear()


def test_openai_provider_without_api_key_refuses_to_start() -> None:
    settings = Settings(DATABASE_URL=":memory:", LLM_PROVIDER="openai", OPENAI_API_KEY=None)
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"), TestClient(app):
        pass


def test_cors_headers_present_when_configured() -> None:
    settings = Settings(
        DATABASE_URL=":memory:", LLM_PROVIDER="mock", CORS_ALLOW_ORIGINS=["https://example.com"]
    )
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})
        assert response.headers.get("access-control-allow-origin") == "https://example.com"


def test_no_cors_headers_when_not_configured() -> None:
    settings = Settings(DATABASE_URL=":memory:", LLM_PROVIDER="mock", CORS_ALLOW_ORIGINS=[])
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "https://example.com"})
        assert "access-control-allow-origin" not in response.headers


def test_oversized_request_body_is_rejected() -> None:
    settings = Settings(DATABASE_URL=":memory:", LLM_PROVIDER="mock", MAX_REQUEST_BODY_BYTES=10)
    app = create_app(settings)
    with TestClient(app) as client:
        response = client.post("/workflows", content=b"x" * 1000)
        assert response.status_code == 413


def test_engine_error_not_in_explicit_status_map_falls_back_to_500(client) -> None:
    """NoMatchingBranchError is an EngineError but not in api/errors.py's
    explicit map — it's a graph-integrity problem, not something a client
    request could reasonably be expected to fix, so it's correctly treated
    as an internal error, not a 4xx."""
    from tests.api.conftest import LINEAR_WORKFLOW

    condition_workflow = {
        "id": "wf-no-match",
        "version": 1,
        "name": "no-match",
        "nodes": [
            {"id": "start", "type": "START"},
            {"id": "classify", "type": "TRANSFORM", "config": {"set": {"score": 5}}},
            {"id": "route", "type": "CONDITION"},
            {"id": "a", "type": "END"},
            {"id": "b", "type": "END"},
        ],
        "edges": [
            {"id": "e1", "source_node_id": "start", "target_node_id": "classify"},
            {"id": "e2", "source_node_id": "classify", "target_node_id": "route"},
            {
                "id": "e3",
                "source_node_id": "route",
                "target_node_id": "a",
                "condition": {"field": "score", "operator": "gte", "value": 100},
            },
            {
                "id": "e4",
                "source_node_id": "route",
                "target_node_id": "b",
                "condition": {"field": "score", "operator": "lt", "value": 0},
            },
        ],
        "start_node_id": "start",
    }
    client.post("/workflows", json=condition_workflow)
    response = client.post(
        "/executions",
        json={"workflow_id": "wf-no-match", "workflow_version": 1, "trigger_input": {}},
    )
    assert response.status_code == 500
    assert response.json() == {"detail": "internal server error"}
    _ = LINEAR_WORKFLOW  # unused here, kept for parity with other test files' imports


def test_truly_unexpected_exception_never_leaks_details(monkeypatch) -> None:
    """`services.get_workflow` is looked up dynamically by the router
    (`services.get_workflow(...)`, not a pre-bound reference captured at
    import time), so patching the module attribute affects the already-built
    app's requests. `raise_server_exceptions=False` is required here: Starlette's
    `ServerErrorMiddleware` re-raises the original exception even after our
    handler has already sent a clean response — by design, so it still
    reaches server-side logs/a WSGI/ASGI server in production — and the
    default TestClient surfaces that re-raise as a test failure. Turning it
    off makes the test see exactly what an external HTTP client would see:
    the response body, not the internal traceback.
    """
    from workflow_engine.api import services
    from workflow_engine.api.app import create_app
    from workflow_engine.api.config import Settings

    def _boom(conn, workflow_id, version):
        raise RuntimeError("some internal secret detail that must never reach the client")

    monkeypatch.setattr(services, "get_workflow", _boom)

    app = create_app(Settings(DATABASE_URL=":memory:", LLM_PROVIDER="mock"))
    with TestClient(app, raise_server_exceptions=False) as safe_client:
        response = safe_client.get("/workflows/anything")
        assert response.status_code == 500
        assert "secret detail" not in response.text
        assert response.json() == {"detail": "internal server error"}
