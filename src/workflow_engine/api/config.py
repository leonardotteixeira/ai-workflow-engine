"""Centralized configuration — Fase 12 §12.2, built in Fase 10 since the API
needs it from day one.

`pydantic-settings` reads from environment variables (and an optional `.env`
file — never committed, see `.gitignore`); nothing here has a secret as its
default. `LLM_PROVIDER=mock` (the default) needs no credentials at all and is
what CI and the test suite always use — `openai`/`anthropic` each require
their own `..._API_KEY` to be set explicitly by whoever runs it (Fase 12
§12.1).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = Field(default="workflow_engine.db", alias="DATABASE_URL")
    """A bare filename (not a full `sqlite:///` URL) — `create_sqlite_engine`
    builds the URL. Use ":memory:" for an ephemeral, non-persistent database."""

    llm_provider: Literal["mock", "openai", "anthropic"] = Field(
        default="mock", alias="LLM_PROVIDER"
    )
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4o-mini", alias="OPENAI_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    openai_timeout_seconds: float = Field(default=30.0, alias="OPENAI_TIMEOUT_SECONDS")

    anthropic_api_key: str | None = Field(default=None, alias="ANTHROPIC_API_KEY")
    anthropic_model: str = Field(default="claude-sonnet-5", alias="ANTHROPIC_MODEL")
    anthropic_base_url: str = Field(
        default="https://api.anthropic.com/v1", alias="ANTHROPIC_BASE_URL"
    )
    anthropic_timeout_seconds: float = Field(default=30.0, alias="ANTHROPIC_TIMEOUT_SECONDS")
    anthropic_max_tokens: int = Field(default=1024, alias="ANTHROPIC_MAX_TOKENS")
    """Anthropic's Messages API requires `max_tokens` on every request (unlike
    OpenAI, where it's optional) — this is the default used when a node's own
    `LLMRequest.max_tokens` isn't set."""

    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    environment: Literal["development", "test", "production"] = Field(
        default="development", alias="ENVIRONMENT"
    )

    max_request_body_bytes: int = Field(default=1_000_000, alias="MAX_REQUEST_BODY_BYTES")
    cors_allow_origins: list[str] = Field(default_factory=list, alias="CORS_ALLOW_ORIGINS")
    """Empty by default — deliberately restrictive (Fase 10 §10.9: 'unsafe
    CORS' is a named audit item). A demo frontend running on a different
    origin must be added explicitly, never `["*"]`."""


@lru_cache
def get_settings() -> Settings:
    return Settings()
