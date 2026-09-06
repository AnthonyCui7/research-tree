from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from research_tree.api.app import create_app
from research_tree.api.dependencies import get_repository
from research_tree.workspace.repository import LocalJsonWorkspaceRepository, WorkspaceRepository


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL_ENV = "RESEARCH_TREE_TEST_DATABASE_URL"

# Every test that takes `repository` (directly or through `client`) runs once
# per lane. Locally that is the JSON files; CI also points
# RESEARCH_TREE_TEST_DATABASE_URL at a scratch Postgres and runs both.
REPOSITORY_LANES = ["json"] + (["postgres"] if os.environ.get(TEST_DATABASE_URL_ENV) else [])

# Emptied between tests on the Postgres lane, children before parents.
APP_TABLES = (
    "workspace_events",
    "agent_run_events",
    "reviews",
    "pipeline_runs",
    "workspace_navigation",
    "workspace_versions",
    "workspaces",
    "accesstoken",
    "oauth_account",
    '"user"',
    "service_heartbeat",
)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "json_only: the test asserts on the JSON repository's file layout"
    )


@pytest.fixture(autouse=True)
def keyless_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test reaches a provider, a database, or a sign-in flow by accident.

    A loaded `.env` must not change what the suite does, so the keys and the
    cloud switches are cleared and the app's dotenv loader is disabled.
    """

    for name in (
        "OPENAI_API_KEY",
        "S2_API_KEY",
        "SEMANTIC_SCHOLAR_API_KEY",
        "RESEARCH_TREE_DATABASE_URL",
        "RESEARCH_TREE_REDIS_URL",
        "RESEARCH_TREE_BLOB_ACCOUNT_URL",
        "RESEARCH_TREE_WEB_DIR",
        "RESEARCH_TREE_ALLOWED_ORIGINS",
        "RESEARCH_TREE_PUBLIC_ORIGIN",
        "RESEARCH_TREE_ALLOWED_EMAILS",
        "RESEARCH_TREE_ADMIN_EMAILS",
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESEARCH_TREE_AUTH_MODE", "none")
    monkeypatch.setattr("research_tree.api.app.load_dotenv_file", lambda path: None)


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An empty workspace data root for one test."""

    return tmp_path / "workspaces"


@pytest.fixture(scope="session")
def postgres_engine():
    """One engine for the session; the schema is built by the real migration."""

    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    from research_tree.db import make_engine

    engine = make_engine(url, pool_size=2, max_overflow=2)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    config.cmd_opts = _AlembicArgs(x=[f"url={url}"])
    command.upgrade(config, "head")
    yield engine
    engine.dispose()


class _AlembicArgs:
    def __init__(self, x: list[str]) -> None:
        self.x = x


@pytest.fixture(params=REPOSITORY_LANES)
def repository(request: pytest.FixtureRequest, data_dir: Path, tmp_path: Path) -> WorkspaceRepository:
    if request.param == "json":
        return LocalJsonWorkspaceRepository(data_dir)
    if request.node.get_closest_marker("json_only"):
        pytest.skip("asserts on the JSON file layout")
    from sqlalchemy import text

    from research_tree.artifact_store import FilesystemArtifactStore
    from research_tree.workspace.postgres_repository import PostgresWorkspaceRepository

    engine = request.getfixturevalue("postgres_engine")
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {', '.join(APP_TABLES)} RESTART IDENTITY CASCADE"))
    return PostgresWorkspaceRepository(
        engine, artifacts=FilesystemArtifactStore(tmp_path / "artifacts")
    )


@pytest.fixture
def reopen_repository(
    repository: WorkspaceRepository, data_dir: Path, tmp_path: Path
) -> Callable[[], WorkspaceRepository]:
    """A fresh repository object over the same storage, as a restart would make."""

    def _reopen() -> WorkspaceRepository:
        if isinstance(repository, LocalJsonWorkspaceRepository):
            return LocalJsonWorkspaceRepository(data_dir)
        from research_tree.artifact_store import FilesystemArtifactStore
        from research_tree.workspace.postgres_repository import PostgresWorkspaceRepository

        return PostgresWorkspaceRepository(
            repository._engine, artifacts=FilesystemArtifactStore(tmp_path / "artifacts")
        )

    return _reopen


@pytest.fixture
def client(repository: WorkspaceRepository) -> TestClient:
    """A TestClient whose routes all share `repository`."""

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app)


@pytest.fixture
def seed_workspace(
    repository: WorkspaceRepository,
) -> Callable[..., str]:
    """Publish a workspace version and return its hash.

    Route-level tests only care that a workspace exists, so this stays minimal;
    tests that exercise workspace *content* build their own fixture.
    """

    def _seed(workspace_id: str = "workspace-1", topic: str = "Prompting") -> str:
        return repository.save_workspace_version(
            workspace_id,
            {
                "schema_version": "research_tree_workspace.v1",
                "workspace_id": workspace_id,
                "topic": topic,
                "title": topic,
                "root": {"node_id": "root", "label": topic, "overview": "Overview."},
                "tree": {"root_node_id": "root", "nodes": []},
                "paper_paths": [],
                "paper_cards": {},
            },
            actor="system",
            parent_version_hash=None,
            reason="seeded for tests",
        )

    return _seed


def read_sse_events(response: Any, limit: int) -> list[str]:
    """Collect up to `limit` non-comment SSE frames from a streaming response."""

    frames: list[str] = []
    buffer = ""
    for chunk in response.iter_text():
        buffer += chunk
        while "\n\n" in buffer:
            frame, buffer = buffer.split("\n\n", 1)
            frame = frame.strip()
            if frame and not frame.startswith(":"):
                frames.append(frame)
                if len(frames) >= limit:
                    return frames
    return frames
