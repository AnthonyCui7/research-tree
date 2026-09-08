from __future__ import annotations

from fastapi import APIRouter

from research_tree.api.schemas import HealthResponse


router = APIRouter(tags=["health"])


# HEAD as well as GET: an uptime check that asks for the headers alone is the
# ordinary way to watch a site, and FastAPI does not add it the way Starlette's
# own routing does.
@router.api_route("/health", methods=["GET", "HEAD"], response_model=HealthResponse)
def get_health() -> dict[str, str]:
    return {"status": "ok"}

