"""Accounts, the request gate, and tenancy.

The gate and the browser hardening are exercised keyless. The sign-in flow
itself needs the account tables, so those tests run on the Postgres lane only.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

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
    from research_tree.auth import throttle

    throttle.reset()
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


# ---- Google identity comes from the OpenID userinfo endpoint ----------------


def test_google_identity_is_the_openid_subject_and_verified_email() -> None:
    import asyncio

    import httpx

    from research_tree.auth.google import GoogleOpenIdOAuth2, USERINFO_ENDPOINT, id_and_email

    assert id_and_email({"sub": "1", "email": "a@x.io", "email_verified": True}) == ("1", "a@x.io")
    # An address Google has not verified is not usable for sign-in.
    assert id_and_email({"sub": "1", "email": "a@x.io", "email_verified": False}) == ("1", None)

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization", "")
        return httpx.Response(200, json={"sub": "42", "email": "b@x.io", "email_verified": True})

    client = GoogleOpenIdOAuth2("id", "secret")
    client.get_httpx_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]

    assert asyncio.run(client.get_id_email("tok")) == ("42", "b@x.io")
    assert seen["url"] == USERINFO_ENDPOINT
    assert seen["auth"] == "Bearer tok"


# ---- the sign-in throttle under pressure -------------------------------------


def test_a_flood_of_new_keys_cannot_reset_a_spent_counter(monkeypatch: pytest.MonkeyPatch) -> None:
    """Clearing the table on overflow handed the attacker their own budget back."""

    from research_tree.auth import throttle

    throttle.reset()
    monkeypatch.setattr(throttle, "MAX_TRACKED_KEYS", 8)
    limiter = throttle._limiter

    for _ in range(3):
        assert limiter.hit("login:email:target", 3, 900)
    assert not limiter.hit("login:email:target", 3, 900)

    for index in range(50):
        limiter.hit(f"login:email:flood-{index}", 3, 900)

    assert not limiter.hit("login:email:target", 3, 900)


def test_the_table_recovers_once_its_entries_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    from research_tree.auth import throttle

    throttle.reset()
    monkeypatch.setattr(throttle, "MAX_TRACKED_KEYS", 8)
    limiter = throttle._limiter

    clock = [1_000.0]
    monkeypatch.setattr(throttle.time, "monotonic", lambda: clock[0])
    for index in range(8):
        limiter.hit(f"login:email:early-{index}", 3, 900)
    assert not limiter.hit("login:email:late", 3, 900)

    clock[0] += 901
    assert limiter.hit("login:email:late", 3, 900)


# ---- Google sign-in must not adopt an account nobody proved they own ---------


def test_google_takes_over_an_account_nobody_proved_they_owned(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """Register a stranger's address, wait for their Google sign-in, take their session."""

    _postgres_only(repository)
    from fastapi_users_db_sqlalchemy import SQLAlchemyUserDatabase

    from research_tree.auth.db import _session_factory
    from research_tree.auth.manager import UserManager
    from research_tree.auth.models import OAuthAccount, User

    email = "pre-hijack@example.com"
    password = "attacker-chosen"
    registered = accounts_client.post("/auth/register", json={"email": email, "password": password})
    assert registered.status_code == 201, registered.text
    squatter = TestClient(accounts_client.app)
    assert squatter.post("/auth/login", data={"username": email, "password": password}).status_code == 204

    async def google_signs_in() -> None:
        async with _session_factory()() as session:
            manager = UserManager(SQLAlchemyUserDatabase(session, User, OAuthAccount))
            await manager.oauth_callback(
                "google", "token", "google-account-id", email,
                associate_by_email=True, is_verified_by_default=True,
            )

    asyncio.run(google_signs_in())

    # The password whoever registered the address chose no longer works, and
    # the session they already held is gone.
    refused = accounts_client.post("/auth/login", data={"username": email, "password": password})
    assert refused.status_code == 400
    assert squatter.get("/account/me").status_code == 401

    engine = repository._engine  # type: ignore[attr-defined]
    with engine.begin() as conn:
        verified = conn.execute(
            text('SELECT is_verified FROM "user" WHERE lower(email) = :email'), {"email": email}
        ).scalar()
    assert verified is True


def test_one_account_per_email_whatever_the_case(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """The lookup is case-insensitive, so the constraint has to be too."""

    _postgres_only(repository)

    first = accounts_client.post(
        "/auth/register", json={"email": "CaseCheck@example.com", "password": "correct-horse-battery"}
    )
    assert first.status_code == 201, first.text

    engine = repository._engine  # type: ignore[attr-defined]
    with pytest.raises(IntegrityError):
        with engine.begin() as conn:
            conn.execute(
                text(
                    'INSERT INTO "user" (id, email, hashed_password, is_active, is_superuser, '
                    "is_verified) VALUES (gen_random_uuid(), :email, 'x', true, false, false)"
                ),
                {"email": "casecheck@example.com"},
            )


def test_two_accounts_building_one_topic_never_touch_each_other(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """Both were handed the same name, and whoever finished second overwrote the first."""

    _postgres_only(repository)

    def account(email: str) -> str:
        created = accounts_client.post(
            "/auth/register", json={"email": email, "password": "correct-horse-battery"}
        )
        assert created.status_code == 201, created.text
        return str(created.json()["id"])

    first = account("racer-one@example.com")
    second = account("racer-two@example.com")

    # Both start before either has published anything.
    first_id = repository.claim_workspace_id("prompting", owner_id=first)
    second_id = repository.claim_workspace_id("prompting", owner_id=second)
    assert first_id == "prompting"
    assert second_id == "prompting-2"

    document = {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": first_id,
        "topic": "Prompting",
        "title": "Prompting",
        "root": {},
        "tree": {},
        "paper_paths": [],
        "paper_cards": {},
    }
    repository.save_workspace_version(
        first_id, document, actor="system", parent_version_hash=None,
        reason="built", owner_id=first,
    )
    repository.save_workspace_version(
        second_id, {**document, "workspace_id": second_id, "title": "Theirs"},
        actor="system", parent_version_hash=None, reason="built", owner_id=second,
    )

    assert repository.get_current_workspace(first_id)["title"] == "Prompting"
    assert repository.get_current_workspace(second_id)["title"] == "Theirs"
    assert repository.get_workspace_owner_id(first_id) == first
    assert repository.get_workspace_owner_id(second_id) == second


def test_a_build_will_not_publish_into_another_accounts_workspace(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """A backstop for the claim above: nothing may write into a name it does not hold."""

    _postgres_only(repository)

    created = accounts_client.post(
        "/auth/register", json={"email": "holder@example.com", "password": "correct-horse-battery"}
    )
    assert created.status_code == 201, created.text
    holder = str(created.json()["id"])
    stranger = str(
        accounts_client.post(
            "/auth/register",
            json={"email": "stranger@example.com", "password": "correct-horse-battery"},
        ).json()["id"]
    )
    document = {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "held",
        "topic": "Held",
        "title": "Held",
        "root": {},
        "tree": {},
        "paper_paths": [],
        "paper_cards": {},
    }
    repository.save_workspace_version(
        "held", document, actor="system", parent_version_hash=None,
        reason="built", owner_id=holder,
    )

    with pytest.raises(RuntimeError, match="took that name"):
        repository.save_workspace_version(
            "held", {**document, "title": "Someone else"}, actor="system",
            parent_version_hash=None, reason="built", owner_id=stranger,
        )

    assert repository.get_current_workspace("held")["title"] == "Held"
