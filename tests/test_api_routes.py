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
    assert response.json()["error_code"] == "workspace_not_found"


def test_workspace_events_and_reviews_list(client, seed_workspace) -> None:
    seed_workspace()

    events = client.get("/workspaces/workspace-1/events")
    reviews = client.get("/workspaces/workspace-1/reviews")

    assert events.status_code == 200
    assert events.json()["workspace_id"] == "workspace-1"
    assert isinstance(events.json()["events"], list)
    assert reviews.status_code == 200
    assert reviews.json()["reviews"] == []


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
        return_value={"run_id": "pipeline_new", "status": "queued"},
    ):
        response = client.post(
            "/workspaces/workspace-1/pipeline-runs",
            json={"start_stage": "construct"},
        )

    assert response.status_code == 202
    assert response.json()["pipeline_run"]["run_id"] == "pipeline_new"


def test_create_workspace_records_reader_instructions_on_the_run(client, repository) -> None:
    token = _issue_topic_review_approval("Prompting")

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
    token = _issue_topic_review_approval("Prompting")

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
    assert response.json()["error_code"] == "invalid_payload"


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
        assert openai == {"configured": True, "masked": "sk-…9f2a", "source": "environment"}

    def test_api_key_status_without_a_key(self, client) -> None:
        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=False):
            response = client.get("/account/api-keys")

        assert response.json()["openai"] == {
            "configured": False,
            "masked": None,
            "source": None,
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
