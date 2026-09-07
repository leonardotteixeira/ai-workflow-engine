"""FastAPI application factory — Fase 10 §10.1/§10.6.

`create_app(settings)` builds a fully independent app instance — its own
SQLite engine, its own LLM provider, its own tool/node registry, all on
`app.state` — so tests (and, if ever needed, multiple environments) never
share process-global state. There is exactly one place resources are
created and exactly one place they're torn down: the `lifespan` context
manager below.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from workflow_engine.api.config import Settings, get_settings
from workflow_engine.api.errors import register_exception_handlers
from workflow_engine.api.routers import executions, health, workflows
from workflow_engine.engine.node_executor import registry_with_ai
from workflow_engine.engine.providers import LLMProvider, MockLLMProvider
from workflow_engine.engine.tools import default_tool_registry
from workflow_engine.persistence import create_schema, create_sqlite_engine

logger = logging.getLogger("workflow_engine.api")


class BodySizeLimitMiddleware:
    """Plain ASGI middleware (not `BaseHTTPMiddleware`/`@app.middleware("http")`)
    — deliberately: `BaseHTTPMiddleware` wraps the response in a memory stream
    and is known to interfere with exception propagation to FastAPI's own
    exception handlers (an unrelated 500 further down the stack can end up
    re-raised past the handler instead of being caught). A raw ASGI
    middleware has none of that — it just inspects headers and otherwise
    hands scope/receive/send straight through.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        content_length = headers.get(b"content-length")
        if content_length is not None and int(content_length) > self.max_bytes:
            response = JSONResponse(status_code=413, content={"detail": "request body too large"})
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def _build_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "mock":
        # No configured responses: any workflow that reaches an LLM node
        # without the caller configuring one first will get a clear
        # ProviderError rather than a silently made-up answer. A demo that
        # wants canned responses builds its own MockLLMProvider explicitly
        # (see demo.py) rather than relying on this default instance.
        return MockLLMProvider({})

    if settings.llm_provider == "anthropic":
        from workflow_engine.engine.providers_anthropic import AnthropicProvider

        if not settings.anthropic_api_key:
            raise RuntimeError(
                "LLM_PROVIDER=anthropic requires ANTHROPIC_API_KEY to be set — refusing "
                "to start with a real provider that has no credentials configured"
            )
        return AnthropicProvider(
            api_key=settings.anthropic_api_key,
            model=settings.anthropic_model,
            base_url=settings.anthropic_base_url,
            timeout_seconds=settings.anthropic_timeout_seconds,
            default_max_tokens=settings.anthropic_max_tokens,
        )

    from workflow_engine.engine.providers_openai import OpenAIProvider

    if not settings.openai_api_key:
        raise RuntimeError(
            "LLM_PROVIDER=openai requires OPENAI_API_KEY to be set — refusing to "
            "start with a real provider that has no credentials configured"
        )
    return OpenAIProvider(
        api_key=settings.openai_api_key,
        model=settings.openai_model,
        base_url=settings.openai_base_url,
        timeout_seconds=settings.openai_timeout_seconds,
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db_engine = create_sqlite_engine(settings.database_url)
        create_schema(db_engine)
        llm_provider = _build_llm_provider(settings)
        tool_registry = default_tool_registry()

        app.state.db_engine = db_engine
        app.state.llm_provider = llm_provider
        app.state.tool_registry = tool_registry
        app.state.node_registry = registry_with_ai(llm_provider, tool_registry)
        app.state.settings = settings

        try:
            yield
        finally:
            db_engine.dispose()

    app = FastAPI(
        title="AI Workflow Engine API",
        description=(
            "REST surface over a durable, human-in-the-loop workflow engine. "
            "See /docs for schemas; DESIGN.md in the repository for the "
            "engine's full architecture and reliability semantics."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=False,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Idempotency-Key"],
        )

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)

    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(workflows.router)
    app.include_router(executions.router)

    return app
