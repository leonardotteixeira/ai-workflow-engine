"""Persistence-layer errors.

Kept distinct from `domain.errors` and `engine.errors`: these signal problems
that only exist because there is now a database — a stale write, a missing
row. The domain and engine layers never raise these and never import this
module (DESIGN.md §10: persistence is the outermost layer).
"""

from __future__ import annotations


class PersistenceError(Exception):
    """Base class for all persistence-layer errors."""


class ConcurrencyConflictError(PersistenceError):
    """Raised when an UPDATE ... WHERE version = ? affects zero rows — the row
    was already changed by someone else since it was read (DESIGN.md §3.3,
    §10: optimistic concurrency). The caller must re-read the current row and
    decide whether to retry, not blindly overwrite it.
    """


class NotFoundError(PersistenceError):
    """Raised when a repository is asked to load a row that doesn't exist."""
