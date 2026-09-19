"""Route-level coverage for the endpoints the frontend does not call.

These were previously exercised only through the service layer, so a broken
route signature, response model, or path-ordering change could ship unnoticed.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import patch

from fastapi.testclient import TestClient

from conftest import read_sse_events
from research_tree.api.app import create_app
from research_tree.api.dependencies import get_repository
from research_tree.services.errors import InvalidPayloadError
from research_tree.services.topics import _issue_topic_review_approval
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def test_get_workspace_version_returns_that_version(client, seed_workspace) -> None:
    version_hash = seed_workspace()

    response = client.get(f"/workspaces/workspace-1/versions/{version_hash}")

    assert response.status_code == 200
    body = response.json()
    assert body["workspace_version_hash"] == version_hash
    assert body["workspace"]["topic"] == "Prompting"


def test_malformed_version_hash_is_a_bad_request(client, seed_workspace) -> None:
    seed_workspace()

    response = client.get("/workspaces/workspace-1/versions/deadbeef")

    # A short hash is the caller's mistake, not a server fault.
    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_resource_id"


def test_unknown_version_is_not_found(client, seed_workspace) -> None:
    seed_workspace()

    response = client.get(f"/workspaces/workspace-1/versions/{'a' * 64}")

    assert response.status_code == 404
    # The workspace is there; only the version is gone, and the reader is told
    # which of the two it is.
    assert response.json()["error_code"] == "workspace_version_not_found"
    assert "version" in response.json()["detail"]


def test_workspace_events_and_reviews_list(client, seed_workspace) -> None:
    seed_workspace()

    events = client.get("/workspaces/workspace-1/events")
    reviews = client.get("/workspaces/workspace-1/reviews")

    assert events.status_code == 200
    assert events.json()["workspace_id"] == "workspace-1"
    assert isinstance(events.json()["events"], list)
    assert reviews.status_code == 200
    assert reviews.json()["reviews"] == []


def test_a_build_is_shown_without_where_the_worker_kept_it(client, repository, seed_workspace) -> None:
    seed_workspace()
    run = _save_run(repository, "pipeline_kept", status="completed")
    repository.save_pipeline_run(
        {
            **run,
            "artifacts": {"run_dir": "/data/pipeline_runs/pipeline_kept"},
            "stages": {
                "construct": {
                    "status": "completed",
                    "updated_at": "2026-08-01T00:00:00Z",
                    "inputs": {"candidate_json": "/data/pipeline_runs/pipeline_kept/c.json"},
                    "outputs": {},
                    "error": None,
                }
            },
        }
    )

    shown = client.get("/workspaces/pipeline-runs/pipeline_kept").json()["pipeline_run"]
    listed = client.get("/workspaces/workspace-1/pipeline-runs").json()["pipeline_runs"][0]

    for view in (shown, listed):
        assert view["status"] == "completed"
        assert view["stages"]["construct"] == {
            "status": "completed",
            "updated_at": "2026-08-01T00:00:00Z",
            "error": None,
        }
        assert "/data/" not in json.dumps(view)
        assert not {"artifacts", "owner_id", "runner_pid", "runner_host"} & view.keys()


def test_pipeline_run_lifecycle_routes(client, repository, seed_workspace) -> None:
    seed_workspace()
    run = _save_run(repository, "pipeline_1", status="running")

    listed = client.get("/workspaces/workspace-1/pipeline-runs")
    fetched = client.get(f"/workspaces/pipeline-runs/{run['run_id']}")
    cancelled = client.post(f"/workspaces/pipeline-runs/{run['run_id']}/cancel")

    assert [item["run_id"] for item in listed.json()["pipeline_runs"]] == ["pipeline_1"]
    assert fetched.json()["pipeline_run"]["run_id"] == "pipeline_1"
    assert cancelled.status_code == 200
    assert cancelled.json()["pipeline_run"]["status"] == "cancelled"


def test_unknown_pipeline_run_is_not_found(client) -> None:
    response = client.get("/workspaces/pipeline-runs/pipeline_missing")

    assert response.status_code == 404


def test_rerun_rejects_an_unknown_stage(client, seed_workspace) -> None:
    seed_workspace()

    response = client.post(
        "/workspaces/workspace-1/pipeline-runs",
        json={"start_stage": "not-a-stage"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "invalid_payload"
    # Bad-request detail names the allowed values instead of a generic string.
    assert "start_stage" in response.json()["detail"]


def test_rerun_starts_a_run(client, repository, seed_workspace) -> None:
    seed_workspace()
    _save_run(repository, "pipeline_done", status="completed")

    with patch(
        "research_tree.services.pipeline.WorkspacePipelineService._start",
        return_value={"run_id": "pipeline_new", "workspace_id": "workspace-1", "status": "queued"},
    ):
        response = client.post(
            "/workspaces/workspace-1/pipeline-runs",
            json={"start_stage": "construct"},
        )

    assert response.status_code == 202
    assert response.json()["pipeline_run"]["run_id"] == "pipeline_new"


def test_create_workspace_records_reader_instructions_on_the_run(client, repository) -> None:
    token = _issue_topic_review_approval(repository.owner_id, "Prompting")

    # The run is reserved for real; only the worker that would execute it is
    # stubbed, so the stored run is exactly what the pipeline would read.
    with patch("research_tree.services.pipeline._dispatch_local_thread"):
        response = client.post(
            "/workspaces",
            json={
                "topic": "Prompting",
                "topic_review_token": token,
                "instructions": "  Emphasize\n benchmarks ",
            },
        )

    assert response.status_code == 202
    run_id = response.json()["pipeline_run"]["run_id"]
    assert repository.get_pipeline_run(run_id)["instructions"] == "Emphasize benchmarks"


def test_create_workspace_without_instructions_stores_none(client, repository) -> None:
    token = _issue_topic_review_approval(repository.owner_id, "Prompting")

    with patch("research_tree.services.pipeline._dispatch_local_thread"):
        response = client.post(
            "/workspaces",
            json={"topic": "Prompting", "topic_review_token": token},
        )

    run_id = response.json()["pipeline_run"]["run_id"]
    assert repository.get_pipeline_run(run_id)["instructions"] is None


def test_create_workspace_requires_a_valid_topic_token(client) -> None:
    response = client.post(
        "/workspaces",
        json={"topic": "Prompting", "topic_review_token": "not-a-real-token"},
    )

    assert response.status_code == 400
    assert response.json()["error_code"] == "topic_review_expired"


def test_agent_rejects_a_malformed_thread_id(client, seed_workspace) -> None:
    seed_workspace()

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "hello", "thread_id": "../../etc/passwd"},
    )

    assert response.status_code == 422


class TestPipelineRunStream:
    def test_finished_run_streams_state_then_closes(
        self, client, repository, seed_workspace
    ) -> None:
        seed_workspace()
        _save_run(repository, "pipeline_done", status="completed")

        with client.stream(
            "GET", "/workspaces/pipeline-runs/pipeline_done/events"
        ) as response:
            frames = read_sse_events(response, limit=2)

        assert frames[0].startswith("event: pipeline_run_updated")
        # An explicit end frame: EventSource reconnects forever otherwise.
        assert frames[1].startswith("event: stream_complete")

    def test_unknown_run_answers_404_before_streaming(self, client) -> None:
        response = client.get("/workspaces/pipeline-runs/pipeline_missing/events")

        assert response.status_code == 404


class TestWorkspaceCollectionStream:
    """The collection stream runs until the client goes away.

    It is driven through the raw ASGI interface because that is the only way to
    deliver an http.disconnect; TestClient never sends one, so a regression to
    the old disconnect-blind loop would hang the suite instead of failing it.
    """

    def test_emits_the_collection_then_stops_on_disconnect(
        self, repository, seed_workspace
    ) -> None:
        seed_workspace()
        app = create_app()
        app.dependency_overrides[get_repository] = lambda: repository

        body = asyncio.run(_stream_until_first_chunk(app, "/workspaces/events/stream"))

        assert "event: workspaces_updated" in body
        payload = json.loads(body.split("data: ", 1)[1].split("\n\n", 1)[0])
        assert payload[0]["workspace_id"] == "workspace-1"


async def _stream_until_first_chunk(app: Any, path: str) -> str:
    """Run one SSE request, disconnect after the first chunk, return the body."""

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": "GET",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
        "headers": [(b"host", b"testserver")],
        "client": ("testclient", 50000),
        "server": ("testserver", 80),
    }
    chunks: list[str] = []
    disconnected = asyncio.Event()

    async def receive() -> dict[str, Any]:
        await disconnected.wait()
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            chunks.append(message["body"].decode())
            disconnected.set()

    # Generous relative to the 1 s poll, tight enough to fail rather than hang.
    await asyncio.wait_for(app(scope, receive, send), timeout=10)
    return "".join(chunks)


def test_cors_is_off_by_default_and_on_when_configured() -> None:
    with patch.dict(os.environ, {"RESEARCH_TREE_ALLOWED_ORIGINS": ""}):
        default_app = create_app()
    with patch.dict(
        os.environ, {"RESEARCH_TREE_ALLOWED_ORIGINS": "https://research.example"}
    ):
        configured_app = create_app()

    # Same-origin dev proxying needs no CORS; a separately hosted frontend does.
    assert not _has_cors(default_app)
    assert _has_cors(configured_app)


def test_service_errors_do_not_leak_internal_detail() -> None:
    app = create_app()

    @app.get("/boom")
    def _boom() -> None:
        raise InvalidPayloadError("start_stage must be one of ('candidates',).")

    @app.get("/kaboom")
    def _kaboom() -> None:
        from research_tree.services.errors import WorkspaceServiceError

        raise WorkspaceServiceError("connection string postgres://user:secret@host")

    with TestClient(app) as configured:
        public = configured.get("/boom")
        internal = configured.get("/kaboom")

    assert "start_stage" in public.json()["detail"]
    assert "secret" not in internal.json()["detail"]
    assert internal.json()["detail"] == "We could not complete that request. Please try again."


class TestAccountRoutes:
    """Read is real; both writes are built but deliberately not storing yet."""

    def test_api_key_status_reports_only_the_last_four_characters(self, client) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-live-abcdef9f2a"}, clear=False):
            response = client.get("/account/api-keys")

        assert response.status_code == 200
        openai = response.json()["openai"]
        assert openai == {
            "configured": True,
            "masked": "sk-…9f2a",
            "source": "environment",
            "created_at": None,
        }

    def test_api_key_status_without_a_key(self, client) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            response = client.get("/account/api-keys")

        assert response.json()["openai"] == {
            "configured": False,
            "masked": None,
            "source": None,
            "created_at": None,
        }

    def test_saving_a_key_reports_that_it_was_not_stored(self, client) -> None:
        response = client.put(
            "/account/api-keys",
            json={"provider": "openai", "api_key": "sk-would-be-stored"},
        )

        assert response.status_code == 200
        assert response.json()["stored"] is False

    def test_saving_a_key_for_another_provider_is_rejected(self, client) -> None:
        response = client.put(
            "/account/api-keys",
            json={"provider": "anthropic", "api_key": "sk-something"},
        )

        assert response.status_code == 422

    def test_a_bug_report_is_accepted_but_not_stored(self, client) -> None:
        response = client.post(
            "/account/bug-reports",
            json={"summary": "Tree scrolled to the wrong place", "area": "tree"},
        )

        assert response.status_code == 202
        assert response.json()["received"] is True
        assert response.json()["stored"] is False

    def test_a_bug_report_needs_a_summary(self, client) -> None:
        assert client.post("/account/bug-reports", json={"summary": ""}).status_code == 422


def _has_cors(app: Any) -> bool:
    return any("CORSMiddleware" in str(middleware.cls) for middleware in app.user_middleware)


def _save_run(
    repository: LocalJsonWorkspaceRepository,
    run_id: str,
    *,
    status: str,
) -> dict[str, Any]:
    run = {
        "schema_version": "research_tree.pipeline_run.v2",
        "run_id": run_id,
        "workspace_id": "workspace-1",
        "topic": "Prompting",
        "model": "gpt-5.6-luna",
        "status": status,
        "current_stage": None,
        "requested_stages": ["construct"],
        # No runner_pid: read-time reclamation must not rewrite these fixtures.
        "created_at": "2026-08-01T00:00:00Z",
        "updated_at": "2026-08-01T00:00:00Z",
        "stages": {},
    }
    repository.save_pipeline_run(run)
    return run


# ---- the threads assistant turns run on --------------------------------------


def test_assistant_turns_are_bounded_and_shared_out_between_accounts() -> None:
    import threading

    import pytest

    from research_tree.api.routes.agent import _TurnThreads
    from research_tree.principal import Principal, bind_principal, current_principal
    from research_tree.services.errors import RateLimitedError, ServiceUnavailableError

    async def scenario() -> None:
        threads = _TurnThreads(total=2, per_account=1)
        release = threading.Event()
        seen: list[str | None] = []

        def turn() -> str:
            principal = current_principal()
            seen.append(principal.user_id if principal else None)
            release.wait(5)
            return "answered"

        with bind_principal(Principal(user_id="account-a", email="a@example.com")):
            first = asyncio.ensure_future(threads.run("account-a", turn))
        await asyncio.sleep(0.05)
        # The same account again is refused; another account still gets a thread.
        with pytest.raises(RateLimitedError):
            await threads.run("account-a", turn)
        second = asyncio.ensure_future(threads.run("account-b", turn))
        await asyncio.sleep(0.05)
        with pytest.raises(ServiceUnavailableError):
            await threads.run("account-c", turn)

        release.set()
        assert await first == "answered"
        assert await second == "answered"
        # The turn ran as the account that asked, and its place came back.
        assert "account-a" in seen
        assert await threads.run("account-a", lambda: "again") == "again"

    asyncio.run(scenario())


# ---- what a request body may carry ------------------------------------------


def _edit_body(version_hash: str, value_literal: str, *, extra: str = "") -> str:
    return (
        '{"expected_version_hash":' + json.dumps(version_hash) + extra + ","
        '"operations":[{"op":"set","entity_type":"root","field":"key_terms",'
        '"value":' + value_literal + "}]}"
    )


def test_a_body_nested_past_the_ceiling_is_refused(client, seed_workspace) -> None:
    version_hash = seed_workspace()
    deep: Any = "x"
    for _ in range(300):
        deep = [deep]

    response = client.post(
        "/workspaces/workspace-1/edits",
        content=_edit_body(version_hash, json.dumps(deep)),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert "nested" in response.json()["detail"]
    # The point of the ceiling: a document that got past it could be stored and
    # then never read again, because the response cannot be serialized from it.
    assert client.get("/workspaces/workspace-1").status_code == 200


def test_an_unpaired_surrogate_in_a_body_is_refused(client, seed_workspace) -> None:
    version_hash = seed_workspace()

    response = client.post(
        "/workspaces/workspace-1/edits",
        content=_edit_body(version_hash, '"a\\ud800b"'),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert "cannot be stored" in response.json()["detail"]


def test_a_null_byte_outside_the_document_is_refused(client) -> None:
    # Postgres refuses U+0000 in json, and the run record is written before any
    # workspace exists, so this reached the driver and answered 500.
    response = client.post(
        "/workspaces",
        content='{"topic":"Prompting","topic_review_token":"t","instructions":"emphasise a\\u0000b"}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert response.json()["detail"].startswith("instructions")


def test_a_rejected_field_is_named_without_repeating_what_was_sent(client) -> None:
    response = client.put("/account/api-keys", json={"api_key": ["sk-proj-NOTTHEREALKEY"]})

    assert response.status_code == 422
    body = response.json()
    assert body["error_code"] == "invalid_payload"
    assert body["detail"].startswith("api_key")
    assert "NOTTHEREALKEY" not in response.text


# ---- ids the two stores would read differently -------------------------------


def test_an_id_the_file_store_would_rename_is_refused(client, seed_workspace) -> None:
    """`-workspace-1` served `workspace-1`'s document under a name it does not have."""

    seed_workspace()

    for workspace_id in ("-workspace-1", "workspace-1-", ".workspace-1", "...", "---"):
        response = client.get(f"/workspaces/{workspace_id}")
        assert response.status_code == 400, workspace_id
        assert response.json()["error_code"] == "invalid_resource_id"

    assert client.get("/workspaces/workspace-1").status_code == 200


def test_a_run_id_the_file_store_cannot_name_is_refused(client) -> None:
    for run_id in ("---", "..."):
        assert client.get(f"/workspaces/pipeline-runs/{run_id}").status_code == 400
        assert client.post(f"/workspaces/pipeline-runs/{run_id}/cancel").status_code == 400
    assert client.get("/workspaces/pipeline-runs/pipeline_missing").status_code == 404


def test_health_answers_a_headers_only_request(client) -> None:
    """`curl -I` and every uptime check ask with HEAD; FastAPI answered 405."""

    response = client.head("/health")

    assert response.status_code == 200
    assert response.content == b""


def test_renaming_onto_a_topic_you_already_have_is_refused(client, repository) -> None:
    """A build already refuses a duplicate topic; renaming was the way around it."""

    for workspace_id, topic in (("alpha", "Prompting"), ("beta", "Sampling")):
        repository.save_workspace_version(
            workspace_id,
            {
                "schema_version": "research_tree_workspace.v1",
                "workspace_id": workspace_id,
                "topic": topic,
                "title": topic,
                "scope": {},
                "source_candidate_artifact": {},
                "root": {"node_id": "root", "label": topic, "overview": "Overview."},
                "tree": {"root_node_id": "root", "nodes": []},
                "paper_paths": [],
                "paper_cards": {"p1": {"paper_id": "p1", "title": "A paper"}},
                "reading_order": [],
                "comparison_tables": [],
                "discarded_candidates": [],
                "provenance": {},
            },
            actor="system",
            parent_version_hash=None,
            reason="built",
        )

    head = client.get("/workspaces/beta").json()["workspace_version_hash"]
    response = client.post(
        "/workspaces/beta/edits",
        json={
            "expected_version_hash": head,
            "operations": [
                {"op": "set", "entity_type": "workspace", "field": "topic", "value": "Prompting"}
            ],
        },
    )

    assert response.status_code == 400
    assert "already have a workspace on that topic" in response.json()["detail"]
    assert {w["workspace_id"] for w in client.get("/workspaces").json()["workspaces"]} == {
        "alpha",
        "beta",
    }


def test_the_assistant_refuses_a_model_it_cannot_price(client, seed_workspace) -> None:
    seed_workspace()
    refused = client.post(
        "/workspaces/workspace-1/agent", json={"message": "hello", "model": "gpt-9-imaginary"}
    )
    assert refused.status_code == 400
    assert refused.json()["error_code"] == "invalid_payload"
    assert "gpt-5.6-luna" in refused.json()["detail"]


def test_a_non_finite_number_is_refused_before_it_is_stored(client, seed_workspace) -> None:
    # Python's parser admits NaN; JSON, Postgres and the browser do not, so the
    # stored document and the served one would disagree.
    version_hash = seed_workspace()
    response = client.post(
        "/workspaces/workspace-1/edits",
        content=_edit_body(version_hash, "[NaN]"),
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert "finite" in response.json()["detail"]
