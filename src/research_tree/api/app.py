from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.api.routes import agent, health, reviews, workspaces
from research_tree.retrieval.env import load_dotenv_file
from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.errors import WorkspaceServiceError
from research_tree.workspace.repository import LocalJsonWorkspaceRepository
from research_tree.paths import workspaces_dir


REPO_ROOT = Path(__file__).resolve().parents[3]

# Error codes whose message is written by this codebase for the user to read.
# Everything else gets a generic message so internal detail cannot leak.
PUBLIC_ERROR_CODES = {"invalid_payload", "invalid_resource_id"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the long-lived objects once per process.

    These used to be constructed per request, which quietly disabled the
    agent's checkpointer and node cache: every call got a fresh graph with a
    fresh InMemorySaver, so the thread_id handed back to the client could never
    be resumed. Conversation threads live as long as the process — swap in a
    persistent checkpointer here if they should outlive a restart.
    """

    repository = LocalJsonWorkspaceRepository(workspaces_dir())
    app.state.repository = repository
    app.state.agent_graph = build_workspace_agent_graph(workspace_repository=repository)
    app.state.agent_service = WorkspaceAgentService(
        repository, graph=app.state.agent_graph
    )
    yield


def create_app() -> FastAPI:
    load_dotenv_file(REPO_ROOT / ".env")

    app = FastAPI(title="Research Tree API", version="0.1.0", lifespan=lifespan)

    # The dev server proxies /api to this process, so same-origin needs nothing.
    # A separately hosted frontend sets its origins here.
    allowed_origins = [
        origin.strip()
        for origin in os.environ.get("RESEARCH_TREE_ALLOWED_ORIGINS", "").split(",")
        if origin.strip()
    ]
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

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
                "detail": _public_service_error_message(exc),
                "error_code": exc.error_code,
            },
        )

    return app


app = create_app()


def _public_service_error_message(exc: WorkspaceServiceError) -> str:
    if exc.error_code in {"workspace_not_found", "review_not_found"}:
        return "That workspace is no longer available. Refresh and try again."
    if exc.error_code in {"review_conflict", "stale_workspace"}:
        return "This workspace changed. Refresh and try again."
    if exc.error_code in PUBLIC_ERROR_CODES:
        # Bad-request messages name the offending field and its allowed values,
        # which is exactly what the caller needs to fix the request.
        return exc.message
    return "We could not complete that request. Please try again."
