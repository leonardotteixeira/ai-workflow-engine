"""Structured logging + a minimal metrics sink — Fase 9 §9.7/§9.8/§9.9/§9.10.

Deliberately small: this is "enough abstraction to not hardcode a logging
backend," not an observability platform. No heavy infra (no OpenTelemetry SDK,
no Prometheus client) — just the standard library's `logging` module used
correctly (structured `extra=` fields, never `print()`) and a `MetricsSink`
Protocol simple enough that a real backend can be dropped in later without
touching the Engine.

Both are optional dependencies of `ExecutionEngine` (constructor params
defaulting to no-ops) — a caller that doesn't care about observability pays
nothing for it, and the Engine's core logic never depends on either being
configured.
"""

from __future__ import annotations

import logging
from typing import Protocol

from workflow_engine.domain import Event

_SECRET_KEY_MARKERS = (
    "api_key",
    "apikey",
    "secret",
    "password",
    "authorization",
    "access_token",
    "auth_token",
    "session_token",
    "bearer",
)
"""Deliberately more specific than a bare 'token': LLM usage payloads
legitimately have fields like `total_tokens`/`prompt_tokens` (a count, not a
credential) — matching bare 'token' would redact those. Auth-shaped token
keys (`access_token`, `auth_token`, `session_token`) are still caught."""


class MetricsSink(Protocol):
    def increment(self, name: str, *, tags: dict[str, str] | None = None) -> None: ...


class NullMetricsSink:
    """Default sink: does nothing. Used when the caller supplies none."""

    def increment(self, name: str, *, tags: dict[str, str] | None = None) -> None:
        return None


class InMemoryMetricsSink:
    """Test/demo sink: records every increment call for later assertion —
    never touches stdout/a file/a network endpoint.
    """

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.calls: list[tuple[str, dict[str, str]]] = []

    def increment(self, name: str, *, tags: dict[str, str] | None = None) -> None:
        self.counts[name] = self.counts.get(name, 0) + 1
        self.calls.append((name, tags or {}))


def redact_payload(payload: dict[str, object]) -> dict[str, object]:
    """Fase 9 §9.7: never log/emit API keys, credentials, auth headers,
    secrets. Applied before an Event's payload reaches a logger — the Event
    itself (and what gets persisted) is untouched; this is a logging-time
    concern, not a storage-time one, since the persisted event log is the
    source of truth an operator may legitimately need to inspect under
    controlled access, whereas free-text application logs are a much wider
    blast radius (aggregators, third-party log shippers, etc.).
    """
    redacted: dict[str, object] = {}
    for key, value in payload.items():
        if any(marker in key.lower() for marker in _SECRET_KEY_MARKERS):
            redacted[key] = "***REDACTED***"
        elif isinstance(value, dict):
            redacted[key] = redact_payload(value)
        else:
            redacted[key] = value
    return redacted


def log_event(logger: logging.Logger, event: Event) -> None:
    """One structured log line per Event — correlatable by execution_id,
    scoped by node_id/sequence, with the payload redacted of anything that
    looks like a secret. Never `print()`.
    """
    logger.info(
        event.event_type.value,
        extra={
            "execution_id": event.execution_id,
            "node_id": event.node_id,
            "sequence": event.sequence,
            "event_type": event.event_type.value,
            "payload": redact_payload(event.payload),
        },
    )
