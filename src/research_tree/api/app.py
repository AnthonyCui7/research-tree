from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from research_tree.api.routes import agent, health, reviews, workspaces
from research_tree.retrieval.env import load_dotenv_file
from research_tree.services.errors import WorkspaceServiceError


load_dotenv_file(Path(__file__).resolve().parents[3] / ".env")


def create_app() -> FastAPI:
    app = FastAPI(title="Research Tree API", version="0.1.0")
    app.include_router(health.router)
    app.include_router(workspaces.router)
    app.include_router(agent.router)
    app.include_router(reviews.router)

    @app.exception_handler(WorkspaceServiceError)
    async def handle_workspace_service_error(
        _request: Request,
        exc: WorkspaceServiceError,
    ) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": _public_service_error_message(exc.error_code),
                "error_code": exc.error_code,
            },
        )

    return app


app = create_app()


def _public_service_error_message(error_code: str) -> str:
    if error_code in {"workspace_not_found", "review_not_found"}:
        return "That workspace is no longer available. Refresh and try again."
    if error_code in {"review_conflict", "stale_workspace"}:
        return "This workspace changed. Refresh and try again."
    return "We could not complete that request. Please try again."
