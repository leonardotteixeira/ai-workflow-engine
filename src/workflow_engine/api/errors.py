"""Exception -> HTTP status mapping — Fase 10 §10.3.

One place that decides the mapping, so no router has to remember it. Every
handler returns the uniform `ErrorResponse` shape and never includes the
exception's raw `repr()`/traceback — `str(exc)` on our own exceptions is
already a deliberately-written, safe message (see each exception's
docstring in domain/engine/persistence `errors.py`), never a leaked internal
detail. Anything NOT in this mapping (a real bug) falls through to a generic
500 with a fixed, static message — the real exception is only ever visible
in server-side logs (via FastAPI's default logging of unhandled exceptions),
never in the response body.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from workflow_engine.domain.errors import (
    DomainError,
    DuplicateNodeError,
    InvalidApprovalError,
    InvalidEdgeError,
    InvalidPolicyError,
    InvalidStateTransitionError,
    InvalidWorkflowError,
)
from workflow_engine.engine.errors import (
    EngineError,
    InvalidExecutionStateError,
)
from workflow_engine.engine.replay import ReplayError
from workflow_engine.persistence.errors import ConcurrencyConflictError, NotFoundError

logger = logging.getLogger("workflow_engine.api")

_STATUS_BY_EXCEPTION: dict[type[Exception], int] = {
    # 422 — the request describes something structurally invalid
    InvalidWorkflowError: 422,
    DuplicateNodeError: 422,
    InvalidEdgeError: 422,
    InvalidPolicyError: 422,
    ReplayError: 422,
    # 404 — referenced resource doesn't exist
    NotFoundError: 404,
    # 409 — valid request, but conflicts with the resource's current state
    InvalidStateTransitionError: 409,
    InvalidApprovalError: 409,
    InvalidExecutionStateError: 409,
    ConcurrencyConflictError: 409,
}


def _status_for(exc: Exception) -> int:
    for exc_type, status in _STATUS_BY_EXCEPTION.items():
        if isinstance(exc, exc_type):
            return status
    return 500


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    @app.exception_handler(EngineError)
    @app.exception_handler(ConcurrencyConflictError)
    @app.exception_handler(NotFoundError)
    @app.exception_handler(ReplayError)
    async def _handle_known_error(request: Request, exc: Exception) -> JSONResponse:
        status = _status_for(exc)
        if status >= 500:
            logger.exception("unhandled internal error", exc_info=exc)
            return JSONResponse(status_code=500, content={"detail": "internal server error"})
        return JSONResponse(status_code=status, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled internal error", exc_info=exc)
        return JSONResponse(status_code=500, content={"detail": "internal server error"})
