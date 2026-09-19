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
from tests.conftest import owner_scoped

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
    app.dependency_overrides[get_repository] = owner_scoped(repository)
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


def test_a_read_sent_from_another_site_is_rejected(client: TestClient) -> None:
    """Reads behind sign-in answer to this site's own pages, like writes."""

    navigated = client.get(
        "/workspaces/workspace-1/paper-annotations?paper_id=p1&refresh=true",
        headers={"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"},
    )
    embedded = client.get("/workspaces", headers={"Sec-Fetch-Site": "same-site"})

    assert navigated.status_code == 403
    assert navigated.json()["error_code"] == "csrf_origin_rejected"
    assert embedded.status_code == 403


def test_another_site_may_still_send_a_reader_to_the_front_door(client: TestClient) -> None:
    arriving = {"Sec-Fetch-Site": "cross-site", "Sec-Fetch-Mode": "navigate"}

    assert client.get("/health", headers=arriving).status_code == 200
    # No Google client is configured here, so the route is absent; what matters
    # is that the origin check let the request through to be routed at all.
    assert client.get("/auth/google/callback?code=x", headers=arriving).status_code != 403
    assert client.get("/workspaces", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200


def test_a_separately_hosted_frontend_is_let_in_by_its_origin(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("RESEARCH_TREE_ALLOWED_ORIGINS", "https://app.example.com")
    elsewhere = {"Sec-Fetch-Site": "cross-site", "Origin": "https://app.example.com"}
    stranger = {"Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example.com"}

    assert client.get("/workspaces", headers=elsewhere).status_code == 200
    assert client.get("/workspaces", headers=stranger).status_code == 403
    assert (
        client.post("/workspaces/topic-review", json={"topic": " "}, headers=stranger).status_code
        == 403
    )


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

    # Removing the email locks the account out on its next request, and at
    # the door: signing in again with the right password is refused too.
    monkeypatch.setenv("RESEARCH_TREE_ALLOWED_EMAILS", "someone-else@example.com")
    assert accounts_client.get("/account/me").status_code == 403
    signed_in_again = accounts_client.post(
        "/auth/login", data={"username": "friend@example.com", "password": "correct-horse-battery"}
    )
    assert signed_in_again.status_code == 403
    assert signed_in_again.json()["error_code"] == "not_allowed"


def _document(workspace_id: str, title: str) -> dict:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": workspace_id,
        "topic": title,
        "title": title,
        "root": {},
        "tree": {"root_node_id": "root", "nodes": []},
        "paper_paths": [],
        "paper_cards": {},
    }


def test_two_accounts_see_disjoint_workspaces(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """Each account has its own "shared"; neither can see the other's anything."""

    _postgres_only(repository)
    _register_and_sign_in(accounts_client, "one@example.com")
    one_id = accounts_client.get("/account/me").json()["user"]["id"]
    accounts_client.post("/auth/logout")
    _register_and_sign_in(accounts_client, "two@example.com")
    two_id = accounts_client.get("/account/me").json()["user"]["id"]

    for owner, workspace_id, title in (
        (one_id, "shared", "Mine"),
        (one_id, "mine-only", "Mine only"),
        (two_id, "shared", "Theirs"),
    ):
        repository.for_owner(owner).save_workspace_version(
            workspace_id,
            _document(workspace_id, title),
            actor="system",
            parent_version_hash=None,
            reason="seed",
        )

    listed = accounts_client.get("/workspaces").json()["workspaces"]
    assert [(item["workspace_id"], item["title"]) for item in listed] == [("shared", "Theirs")]
    assert accounts_client.get("/workspaces/shared").json()["workspace"]["title"] == "Theirs"
    assert accounts_client.get("/workspaces/mine-only").status_code == 404
    assert accounts_client.get("/workspaces/mine-only/versions").status_code == 404
    assert accounts_client.delete("/workspaces/mine-only").status_code == 404
    assert repository.for_owner(one_id).get_current_workspace("shared")["title"] == "Mine"


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
    """Both get "prompting", each their own; a name is only taken within an account."""

    _postgres_only(repository)

    def account(email: str) -> str:
        created = accounts_client.post(
            "/auth/register", json={"email": email, "password": "correct-horse-battery"}
        )
        assert created.status_code == 201, created.text
        return str(created.json()["id"])

    first = repository.for_owner(account("racer-one@example.com"))
    second = repository.for_owner(account("racer-two@example.com"))

    # Both start before either has published anything.
    assert first.claim_workspace_id("prompting") == "prompting"
    assert second.claim_workspace_id("prompting") == "prompting"

    first.save_workspace_version(
        "prompting", _document("prompting", "Prompting"), actor="system",
        parent_version_hash=None, reason="built",
    )
    second.save_workspace_version(
        "prompting", {**_document("prompting", "Prompting"), "title": "Theirs"}, actor="system",
        parent_version_hash=None, reason="built",
    )

    assert first.get_current_workspace("prompting")["title"] == "Prompting"
    assert second.get_current_workspace("prompting")["title"] == "Theirs"
    assert [item["title"] for item in first.list_workspaces()] == ["Prompting"]
    assert [item["title"] for item in second.list_workspaces()] == ["Theirs"]
    # The second account's next build of the same topic is the one that is refused.
    with pytest.raises(ValueError, match="already exists"):
        second.reserve_new_workspace_run(
            {"run_id": "pipeline_again", "workspace_id": "prompting-2", "topic": "Prompting", "status": "queued"}
        )


def test_runs_and_reviews_are_the_accounts_own(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """A run id is global, but only its owner can read, cancel or stream it."""

    _postgres_only(repository)
    _register_and_sign_in(accounts_client, "builder@example.com")
    builder = repository.for_owner(accounts_client.get("/account/me").json()["user"]["id"])
    builder.save_workspace_version(
        "held", _document("held", "Held"), actor="system", parent_version_hash=None, reason="built"
    )
    builder.save_pipeline_run(
        {"run_id": "pipeline_theirs", "workspace_id": "held", "status": "completed", "artifacts": {}}
    )
    builder.save_pending_review(
        "held",
        review_id="review-theirs",
        agent_run_id="agent-1",
        base_workspace_version_hash="a" * 64,
        user_message="",
        proposed_workspace=_document("held", "Held"),
        proposed_operations=[],
        diff_summary={},
        validation_summary={},
        interrupt_payload={},
    )
    assert accounts_client.get("/workspaces/pipeline-runs/pipeline_theirs").status_code == 200
    assert accounts_client.get("/workspaces/held/reviews/review-theirs").status_code == 200

    accounts_client.post("/auth/logout")
    _register_and_sign_in(accounts_client, "stranger@example.com")
    assert accounts_client.get("/workspaces/pipeline-runs/pipeline_theirs").status_code == 404
    assert accounts_client.post("/workspaces/pipeline-runs/pipeline_theirs/cancel").status_code == 404
    assert accounts_client.get("/workspaces/pipeline-runs/pipeline_theirs/events").status_code == 404
    assert accounts_client.get("/workspaces/held/reviews/review-theirs").status_code == 404
    # Runs are listed under a name the stranger may use; there are none of theirs.
    assert accounts_client.get("/workspaces/held/pipeline-runs").json()["pipeline_runs"] == []
    # The stranger's own "held" is a different workspace entirely.
    stranger = repository.for_owner(accounts_client.get("/account/me").json()["user"]["id"])
    stranger.save_workspace_version(
        "held", _document("held", "Someone else"), actor="system", parent_version_hash=None, reason="built"
    )
    assert accounts_client.get("/workspaces/held").json()["workspace"]["title"] == "Someone else"
    assert accounts_client.get("/workspaces/held/pipeline-runs").json()["pipeline_runs"] == []
    assert builder.get_current_workspace("held")["title"] == "Held"


def test_a_revoked_session_ends_its_open_streams(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """Signing out everywhere ends the streams that session had open.

    Driven through the raw ASGI interface, as the collection stream test in
    test_api_routes is: the client here never disconnects, so the request
    can only end by the server noticing the session is gone.
    """

    from typing import Any

    from research_tree.auth import db as auth_db
    from research_tree.auth.accounts import revoke_sessions

    _postgres_only(repository)
    _register_and_sign_in(accounts_client, "streamer@example.com")
    user_id = accounts_client.get("/account/me").json()["user"]["id"]
    cookie = f"rt_session={accounts_client.cookies['rt_session']}".encode()
    frames: list[str] = []
    revoked: list[int] = []

    async def never_disconnects() -> dict[str, Any]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.start":
            assert message["status"] == 200
        if message["type"] == "http.response.body" and message.get("body"):
            frames.append(message["body"].decode())
            # The first frame is the collection; the session ends now.
            if not revoked:
                revoked.append(await asyncio.to_thread(revoke_sessions, user_id))

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": "/workspaces/events/stream",
        "raw_path": b"/workspaces/events/stream",
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"host", b"testserver"), (b"cookie", cookie)],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    # The account engine is bound to the loop it was made on, which was the
    # test client's; this request runs on a loop of its own.
    auth_db.get_async_engine.cache_clear()
    auth_db._session_factory.cache_clear()
    # Generous relative to the 1 s poll, tight enough to fail rather than hang.
    asyncio.run(asyncio.wait_for(accounts_client.app(scope, never_disconnects, send), timeout=15))

    assert revoked == [1]
    assert frames[0].startswith("event: workspaces_updated")


def test_an_assistant_thread_stays_inside_its_workspace(
    accounts_client: TestClient, repository: WorkspaceRepository
) -> None:
    """Conversation threads are stored by id alone, so the id carries its owner."""

    from research_tree.services.agent import WorkspaceAgentService
    from research_tree.services.errors import InvalidPayloadError

    _postgres_only(repository)
    _register_and_sign_in(accounts_client, "talker@example.com")
    owner = accounts_client.get("/account/me").json()["user"]["id"]
    service = WorkspaceAgentService(repository.for_owner(owner))
    fresh = service.thread_id("held", None)
    assert fresh.startswith(f"{owner}:held:")
    assert service.thread_id("held", fresh) == fresh
    for foreign in ("workspace-agent:held:abc123", f"{owner}:other:abc123", "someone-else:held:abc123"):
        with pytest.raises(InvalidPayloadError):
            service.thread_id("held", foreign)
