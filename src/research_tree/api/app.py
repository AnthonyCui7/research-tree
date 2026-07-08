from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from research_tree.api.routes import agent, health, reviews, workspaces
from research_tree.services.errors import WorkspaceServiceError


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
            content={"detail": exc.message, "error_code": exc.error_code},
        )

    return app


app = create_app()

