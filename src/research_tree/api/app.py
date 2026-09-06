from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.api.auth import auth_dependency
from research_tree.api.middleware import (
    BodyLimitMiddleware,
    CanonicalHostMiddleware,
    SameOriginMiddleware,
    SecurityHeadersMiddleware,
)
from research_tree.api.routes import account, agent, health, reviews, workspaces
from research_tree.db import database_url, plain_postgres_dsn
from research_tree.log_scrub import install_log_scrubbing
from research_tree.principal import auth_mode
from research_tree.retrieval.env import load_dotenv_file
from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.errors import WorkspaceServiceError
from research_tree.workspace.repository import build_workspace_repository


REPO_ROOT = Path(__file__).resolve().parents[3]
WEB_DIR_ENV = "RESEARCH_TREE_WEB_DIR"

logger = logging.getLogger("uvicorn.error")

# Error codes whose message is written by this codebase for the user to read.
# Everything else gets a generic message so internal detail cannot leak.
PUBLIC_ERROR_CODES = {
    "invalid_payload",
    "invalid_resource_id",
    "unauthenticated",
    "not_allowed",
    "rate_limited",
    "no_llm_credentials",
    "allowance_exhausted",
    "api_key_invalid",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Build the long-lived objects once per process.

    These used to be constructed per request, which quietly disabled the
    agent's checkpointer and node cache: every call got a fresh graph with a
    fresh InMemorySaver, so the thread_id handed back to the client could never
    be resumed. With Postgres configured the checkpointer is Postgres too, so
    a conversation thread survives a restart.
    """

    repository = build_workspace_repository()
    app.state.repository = repository
    checkpointer, checkpoint_pool = _checkpointer()
    app.state.agent_graph = build_workspace_agent_graph(
        workspace_repository=repository, checkpointer=checkpointer
    )
    app.state.agent_service = WorkspaceAgentService(
        repository, graph=app.state.agent_graph
    )
    try:
        yield
    finally:
        if checkpoint_pool is not None:
            checkpoint_pool.close()
        if auth_mode() == "accounts":
            from research_tree.auth.db import dispose_async_engine

            await dispose_async_engine()


def _checkpointer():
    url = database_url()
    if url is None:
        return None, None
    from langgraph.checkpoint.postgres import PostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    pool = ConnectionPool(
        plain_postgres_dsn(url),
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    # The checkpoint tables are created by `research-tree-migrate` (the
    # migrate job), which runs as the database owner; the API's role cannot
    # create tables and does not try to.
    return PostgresSaver(pool), pool


def create_app() -> FastAPI:
    load_dotenv_file(REPO_ROOT / ".env")
    install_log_scrubbing()
    mode = auth_mode()
    if mode == "accounts" and database_url() is None:
        raise RuntimeError(
            "RESEARCH_TREE_AUTH_MODE=accounts needs RESEARCH_TREE_DATABASE_URL: "
            "accounts live in Postgres."
        )

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
    # Outermost last: headers wrap everything, then the origin check, then
    # the body cap, so a refused request never reaches a handler.
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(SameOriginMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(CanonicalHostMiddleware)

    protected = [Depends(auth_dependency())]
    app.include_router(health.router)
    if mode == "accounts":
        from research_tree.auth.routes import build_auth_router

        app.include_router(build_auth_router())
    app.include_router(account.router, dependencies=protected)
    app.include_router(workspaces.router, dependencies=protected)
    app.include_router(agent.router, dependencies=protected)
    app.include_router(reviews.router, dependencies=protected)
    _mount_web(app)

    @app.exception_handler(WorkspaceServiceError)
    async def handle_workspace_service_error(
        request: Request,
        exc: WorkspaceServiceError,
    ) -> Response:
        if _is_browser_callback(request):
            return RedirectResponse(f"/?auth_error={quote(exc.error_code)}", status_code=303)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "detail": _public_service_error_message(exc),
                "error_code": exc.error_code,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException) -> Response:
        # The Google callback lands in a browser tab: a JSON 400 there is a
        # dead end, a redirect into the app with a reason is not.
        if _is_browser_callback(request):
            return RedirectResponse("/?auth_error=google_sign_in_failed", status_code=303)
        return await http_exception_handler(request, exc)

    return app


def _mount_web(app: FastAPI) -> None:
    """Serve the built SPA from the same origin as the API.

    Only `/`, `/favicon.png`, and the hashed assets exist; the SPA has no client
    routes, so there is no catch-all to shadow API 404s.
    """

    web_dir = (os.environ.get(WEB_DIR_ENV) or "").strip()
    if not web_dir:
        return
    root = Path(web_dir)
    index = root / "index.html"
    if not index.is_file():
        logger.warning("%s=%s has no index.html; the SPA is not served.", WEB_DIR_ENV, web_dir)
        return
    assets = root / "assets"
    if assets.is_dir():
        app.mount("/assets", _ImmutableStaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    async def spa_index() -> FileResponse:
        return FileResponse(index, headers={"Cache-Control": "no-cache"})

    favicon = root / "favicon.png"
    if favicon.is_file():

        @app.get("/favicon.png", include_in_schema=False)
        async def spa_favicon() -> FileResponse:
            return FileResponse(favicon, headers={"Cache-Control": "public, max-age=86400"})


class _ImmutableStaticFiles(StaticFiles):
    """Vite hashes asset filenames, so they can be cached forever."""

    def file_response(self, *args, **kwargs):  # type: ignore[override]
        response = super().file_response(*args, **kwargs)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def _is_browser_callback(request: Request) -> bool:
    return request.url.path.endswith("/auth/google/callback")


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
