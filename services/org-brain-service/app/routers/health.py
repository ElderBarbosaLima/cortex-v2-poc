"""Liveness (`/health`). Cheap on purpose: it only says the process is up.

A dependency-aware `/ready` arrives with the first dependency (Postgres, issue 2).
"""

from fastapi import APIRouter

from app.schemas.health import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse, operation_id="health")
async def health() -> HealthResponse:
    return HealthResponse(service="org-brain-service")
