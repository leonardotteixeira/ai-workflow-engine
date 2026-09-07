"""Retry backoff + idempotency-key derivation — DESIGN.md §2.4/§7, Fase 8.

Both are pure functions: no I/O, no wall-clock reads (the caller passes
`attempt`/inputs in, gets a value back), so retry timing and idempotency keys
stay fully deterministic and testable without real `sleep()`.
"""

from __future__ import annotations

import hashlib
import json
from datetime import timedelta
from typing import Any

from workflow_engine.domain.enums import BackoffStrategy
from workflow_engine.domain.policies import RetryPolicy


def compute_backoff_delay(policy: RetryPolicy, attempt: int) -> timedelta:
    """Delay to wait before `attempt + 1`, given the policy. `attempt` is the
    1-based attempt number that just failed (i.e. this is the delay before
    the *next* one). FIXED always returns `base_delay_seconds`; EXPONENTIAL
    doubles per prior attempt, capped at `max_delay_seconds` — both policy
    fields Fase 1 already validates as positive and consistent
    (`RetryPolicy._validate`), so no extra bounds-checking is needed here.
    """
    if policy.backoff == BackoffStrategy.FIXED:
        seconds = policy.base_delay_seconds
    else:
        seconds = policy.base_delay_seconds * (2 ** (attempt - 1))
    return timedelta(seconds=min(seconds, policy.max_delay_seconds))


def derive_idempotency_key(execution_id: str, node_id: str, input_variables: dict[str, Any]) -> str:
    """Stable across retries of the *same logical attempt* — i.e. as long as
    `input_variables` (the context at the moment this node was reached)
    doesn't change, every attempt at running this node produces the same
    key. This is what would let a real Tool/Provider recognize "I already did
    this" across a crash-and-recover cycle (DESIGN.md §6.1) — V1's Mock
    providers/tools have no external state to deduplicate against, so this is
    the interface Fase 8 was asked to prepare, not a full dedup guarantee end
    to end (see engine.py's retry docstring for the honest limitation).
    """
    canonical = json.dumps(input_variables, sort_keys=True, default=str, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{execution_id}:{node_id}:{digest}"
