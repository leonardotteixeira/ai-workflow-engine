from __future__ import annotations

from fastapi import APIRouter

from workflow_engine.api.schemas import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health() -> HealthResponse:
    """No dependency on the database or any external service — a 200 here
    only means the process is up, not that persistence is reachable (a DB
    error surfaces as a 500 on the endpoints that actually touch it)."""
    return HealthResponse()
