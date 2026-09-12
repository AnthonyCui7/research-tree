from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

import pytest
from fastapi.testclient import TestClient

from research_tree.api.app import create_app
from research_tree.api.dependencies import get_repository
from research_tree.billing.keywrap import forget_key_wrapper
from research_tree.db import get_engine
from research_tree.principal import acting_user_id
from research_tree.redis_client import forget_redis_clients
from research_tree.workspace.repository import LocalJsonWorkspaceRepository, WorkspaceRepository


REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_DATABASE_URL_ENV = "RESEARCH_TREE_TEST_DATABASE_URL"
TEST_REDIS_URL_ENV = "RESEARCH_TREE_TEST_REDIS_URL"

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
    "usage_events",
    "user_api_keys",
    "allowances",
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
def keyless_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
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
        "RESEARCH_TREE_KEY_ENCRYPTION_KEY",
        "RESEARCH_TREE_KEY_VAULT_URL",
        "RESEARCH_TREE_MODEL_PRICES",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RESEARCH_TREE_AUTH_MODE", "none")
    monkeypatch.setenv("RESEARCH_TREE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("research_tree.api.app.load_dotenv_file", lambda path: None)
    _forget_process_caches()
    yield
    _forget_process_caches()


def _forget_process_caches() -> None:
    """Per-process caches keyed on the environment, which every test rewrites."""

    forget_redis_clients()
    forget_key_wrapper()
    if get_engine.cache_info().currsize:
        try:
            get_engine().dispose()
        except Exception:  # noqa: BLE001 - an engine built for a URL that is gone
            pass
        get_engine.cache_clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """An empty workspace data root for one test."""

    return tmp_path / "workspaces"


@pytest.fixture(scope="session")
def postgres_engine():
    """One engine for the session; the schema is built the way a deploy builds it.

    `research-tree-migrate` is what the migrate job runs: the migrations, the
    checkpointer's own tables, and the hardening pass over all of them. Using
    it here means the posture tests see the same tables production has.
    """

    url = os.environ.get(TEST_DATABASE_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_DATABASE_URL_ENV} is not set")
    from sqlalchemy import text

    from research_tree.cli.migrate import migrate
    from research_tree.db import make_engine

    engine = make_engine(url, pool_size=2, max_overflow=2)
    with engine.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        # Deployed, the API connects as a role that does not own the tables,
        # which is what makes row-level security apply to it at all. Tests
        # connect as the owner, so without this role the hardening pass would
        # never run its own policy branch here.
        conn.execute(
            text(
                """
                DO $$ BEGIN
                    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'research_tree_app')
                    THEN CREATE ROLE research_tree_app NOLOGIN;
                    END IF;
                END $$
                """
            )
        )
    migrate(url)
    yield engine
    engine.dispose()


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
def redis_client():
    """A flushed Redis for one test; skipped unless a scratch Redis is configured."""

    url = os.environ.get(TEST_REDIS_URL_ENV)
    if not url:
        pytest.skip(f"{TEST_REDIS_URL_ENV} is not set")
    import redis

    client = redis.Redis.from_url(url)
    client.flushdb()
    yield client
    client.flushdb()
    client.close()


@pytest.fixture
def redis_env(redis_client, monkeypatch: pytest.MonkeyPatch):
    """The same Redis, also reachable through `RESEARCH_TREE_REDIS_URL`."""

    monkeypatch.setenv("RESEARCH_TREE_REDIS_URL", os.environ[TEST_REDIS_URL_ENV])
    forget_redis_clients()
    yield redis_client
    forget_redis_clients()


def owner_scoped(repository: WorkspaceRepository) -> Callable[[], WorkspaceRepository]:
    """What `get_repository` does in the app, over a test's repository.

    The request's principal decides whose workspaces a handler sees; with
    accounts off that is always the local user, so the fixture repository
    itself.
    """

    return lambda: repository.for_owner(acting_user_id())


@pytest.fixture
def client(repository: WorkspaceRepository) -> TestClient:
    """A TestClient whose routes all share `repository`."""

    app = create_app()
    app.dependency_overrides[get_repository] = owner_scoped(repository)
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
