"""Accounts, the request gate, and tenancy.

The gate and the browser hardening are exercised keyless. The sign-in flow
itself needs the account tables, so those tests run on the Postgres lane only.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from research_tree.api.app import create_app
from research_tree.api.dependencies import get_repository
from research_tree.workspace.repository import LocalJsonWorkspaceRepository, WorkspaceRepository

TEST_DATABASE_URL_ENV = "RESEARCH_TREE_TEST_DATABASE_URL"
SESSION_SECRET = "test-session-secret-that-is-long-enough-0123456789"


@pytest.fixture
def accounts_client(
    monkeypatch: pytest.MonkeyPatch, repository: WorkspaceRepository
) -> TestClient:
    """An app in accounts mode over `repository`.

    On the JSON lane the database URL points nowhere: enough to prove that an
    anonymous request never reaches a handler, since resolving a missing
    cookie needs no connection.
    """

    if isinstance(repository, LocalJsonWorkspaceRepository):
        database_url = "postgresql+psycopg://nobody:nothing@127.0.0.1:1/none"
    else:
        database_url = os.environ[TEST_DATABASE_URL_ENV]
    monkeypatch.setenv("RESEARCH_TREE_AUTH_MODE", "accounts")
    monkeypatch.setenv("RESEARCH_TREE_DATABASE_URL", database_url)
    monkeypatch.setenv("SESSION_SECRET", SESSION_SECRET)
    from research_tree.auth import db as auth_db

    auth_db.get_async_engine.cache_clear()
    auth_db._session_factory.cache_clear()
    from research_tree.auth.routes import _limiter

    _limiter.reset()
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app)


def _postgres_only(repository: WorkspaceRepository) -> None:
    if isinstance(repository, LocalJsonWorkspaceRepository):
        pytest.skip("accounts live in Postgres")


def _register_and_sign_in(client: TestClient, email: str, password: str = "correct-horse-battery") -> None:
    registered = client.post("/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201, registered.text
    signed_in = client.post("/auth/login", data={"username": email, "password": password})
    assert signed_in.status_code == 204, signed_in.text
    assert "rt_session" in client.cookies


# ---- the gate, keyless ------------------------------------------------------


def test_anonymous_requests_are_refused_before_any_handler(accounts_client: TestClient) -> None:
    refused = accounts_client.get("/workspaces")
    health = accounts_client.get("/health")

    assert refused.status_code == 401
    assert refused.json()["error_code"] == "unauthenticated"
    assert health.status_code == 200


def test_cross_site_writes_are_rejected(client: TestClient) -> None:
    response = client.post(
        "/workspaces/topic-review",
        json={"topic": "prompting"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403
    assert response.json()["error_code"] == "csrf_origin_rejected"


def test_browser_hardening_headers_are_present(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_local_mode_reports_the_local_profile(client: TestClient) -> None:
    response = client.get("/account/me")

    assert response.status_code == 200
    assert response.json()["auth_mode"] == "none"
    assert response.json()["user"]["id"] == "local_user"


# ---- sign-in and tenancy, Postgres only --------------------------------------


def test_register_sign_in_and_sign_out(
    accounts_client: TestClient, repository: WorkspaceRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres_only(repository)
    monkeypatch.setenv("RESEARCH_TREE_ADMIN_EMAILS", "owner@example.com")

    weak = accounts_client.post(
        "/auth/register", json={"email": "owner@example.com", "password": "short"}
    )
    assert weak.status_code == 400

    _register_and_sign_in(accounts_client, "owner@example.com")
    me = accounts_client.get("/account/me")
    assert me.status_code == 200
    assert me.json()["auth_mode"] == "accounts"
    assert me.json()["user"]["email"] == "owner@example.com"
    assert me.json()["user"]["is_admin"] is True

    duplicate = accounts_client.post(
        "/auth/register", json={"email": "owner@example.com", "password": "another-long-one"}
    )
    assert duplicate.status_code == 400

    wrong = accounts_client.post(
        "/auth/login", data={"username": "owner@example.com", "password": "not-the-password"}
    )
    assert wrong.status_code == 400

    signed_out = accounts_client.post("/auth/logout")
    assert signed_out.status_code == 204
    assert accounts_client.get("/account/me").status_code == 401


def test_allowlist_gates_registration_and_every_request(
    accounts_client: TestClient, repository: WorkspaceRepository, monkeypatch: pytest.MonkeyPatch
) -> None:
    _postgres_only(repository)
    monkeypatch.setenv("RESEARCH_TREE_ALLOWED_EMAILS", "friend@example.com")

    refused = accounts_client.post(
        "/auth/register", json={"email": "stranger@example.com", "password": "long-enough-password"}
    )
    assert refused.status_code == 403
    assert refused.json()["error_code"] == "not_allowed"

    _register_and_sign_in(accounts_client, "friend@example.com")
    assert accounts_client.get("/account/me").status_code == 200

    # Removing the email locks the account out on its next request.
    monkeypatch.setenv("RESEARCH_TREE_ALLOWED_EMAILS", "someone-else@example.com")
    assert accounts_client.get("/account/me").status_code == 403


def test_two_accounts_see_disjoint_workspaces(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    _postgres_only(repository)
    _register_and_sign_in(accounts_client, "one@example.com")
    one_id = accounts_client.get("/account/me").json()["user"]["id"]
    accounts_client.post("/auth/logout")
    _register_and_sign_in(accounts_client, "two@example.com")
    two_id = accounts_client.get("/account/me").json()["user"]["id"]

    for owner, workspace_id in ((one_id, "ws-one"), (two_id, "ws-two")):
        repository.save_workspace_version(
            workspace_id,
            {
                "schema_version": "research_tree_workspace.v1",
                "workspace_id": workspace_id,
                "topic": workspace_id,
                "title": workspace_id,
                "root": {},
                "tree": {"root_node_id": "root", "nodes": []},
                "paper_paths": [],
                "paper_cards": {},
            },
            actor="system",
            parent_version_hash=None,
            reason="seed",
            owner_id=owner,
        )

    listed = accounts_client.get("/workspaces").json()["workspaces"]
    assert [item["workspace_id"] for item in listed] == ["ws-two"]
    assert accounts_client.get("/workspaces/ws-two").status_code == 200
    assert accounts_client.get("/workspaces/ws-one").status_code == 404
    assert accounts_client.get("/workspaces/ws-one/versions").status_code == 404
    assert accounts_client.delete("/workspaces/ws-one").status_code == 404


def test_stale_running_pipeline_is_reclaimed(repository: WorkspaceRepository) -> None:
    _postgres_only(repository)
    repository.save_pipeline_run(
        {
            "run_id": "pipeline_stale",
            "workspace_id": "workspace-1",
            "topic": "Stale",
            "status": "running",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )
    with repository._engine.begin() as conn:  # type: ignore[attr-defined]
        conn.execute(
            text(
                "UPDATE pipeline_runs SET heartbeat_at = now() - INTERVAL '10 minutes' "
                "WHERE run_id = 'pipeline_stale'"
            )
        )

    run = repository.get_pipeline_run("pipeline_stale")

    assert run["status"] == "failed"
    assert "stopped" in run["error"]
    repository.touch_pipeline_run("pipeline_stale")
    assert repository.get_pipeline_run("pipeline_stale")["status"] == "failed"
