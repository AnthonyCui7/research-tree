from __future__ import annotations

from fastapi import APIRouter

from research_tree.api.schemas import HealthResponse


router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def get_health() -> dict[str, str]:
    return {"status": "ok"}

