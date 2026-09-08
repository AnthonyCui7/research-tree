"""What Redis and the worker add: queued annotation jobs, the streamed
assistant turn, per-account ceilings, shared topic tokens, the shared
Semantic Scholar lane, and change notifications from the repository.

The Redis-backed tests run when RESEARCH_TREE_TEST_REDIS_URL points at a
scratch instance (CI does); the others are keyless and always run.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from research_tree.api.dependencies import get_paper_annotation_service
from research_tree.artifact_store import FilesystemArtifactStore
from research_tree.retrieval.cache import RateLimiter
from research_tree.services.annotations import PaperAnnotationService, load_annotation_job
from research_tree.services.pipeline import (
    _dispatch_celery,
    _dispatch_local_thread,
    default_pipeline_dispatch,
)
from research_tree.services.topics import TopicReviewService, _issue_topic_review_approval
from research_tree.workspace.repository import WorkspaceRepository
from tests.conftest import read_sse_events
from tests.test_annotation_routes import PAPER_ID, PDF_BYTES, Annotator, annotations_url, seed_paper_workspace



# ---- annotation jobs --------------------------------------------------------


@pytest.fixture
def job_service(
    repository: WorkspaceRepository, redis_client, tmp_path
) -> tuple[PaperAnnotationService, Annotator, list[str]]:
    seed_paper_workspace(repository)
    annotator = Annotator()
    queued: list[str] = []
    service = PaperAnnotationService(
        repository,
        download_pdf=lambda url, **_: PDF_BYTES,
        generate_annotations=annotator,
        artifacts=FilesystemArtifactStore(tmp_path / "artifacts"),
        redis=redis_client,
        enqueue=queued.append,
    )
    return service, annotator, queued


def test_annotation_miss_becomes_a_job_the_worker_completes(client: TestClient, job_service) -> None:
    service, annotator, queued = job_service
    client.app.dependency_overrides[get_paper_annotation_service] = lambda: service

    first = client.get(annotations_url())
    assert first.status_code == 202
    job = first.json()
    assert job["status"] == "queued"
    assert queued == [job["job_id"]]
    assert annotator.calls == 0

    # Asking again while it is queued joins the same job rather than starting another.
    again = client.get(annotations_url())
    assert again.status_code == 202
    assert again.json()["job_id"] == job["job_id"]
    assert queued == [job["job_id"]]

    status_url = f"/workspaces/sampling/paper-annotations/jobs/{job['job_id']}"
    assert client.get(status_url).json()["status"] == "queued"

    service.run_annotation_job(load_annotation_job(job["job_id"], redis=service._redis))
    # Redelivery is harmless: a finished job is left alone.
    service.run_annotation_job(load_annotation_job(job["job_id"], redis=service._redis))
    assert annotator.calls == 1
    assert client.get(status_url).json()["status"] == "completed"

    served = client.get(annotations_url())
    assert served.status_code == 200
    assert served.json()["annotations"][0]["text_ref"] == "fixed compute budget"
    assert client.get("/workspaces/sampling/paper-annotations/jobs/annotation_unknown").status_code == 404


def test_a_failed_job_reports_why(client: TestClient, job_service) -> None:
    service, _, _ = job_service
    service.generate_annotations = _explode
    client.app.dependency_overrides[get_paper_annotation_service] = lambda: service

    job = client.get(annotations_url()).json()
    service.run_annotation_job(load_annotation_job(job["job_id"], redis=service._redis))

    status = client.get(f"/workspaces/sampling/paper-annotations/jobs/{job['job_id']}").json()
    assert status["status"] == "failed"
    assert status["error_code"] == "paper_unavailable"
    assert status["error_status"] == 502
    assert "annotate" in status["detail"]


def _explode(*args: Any, **kwargs: Any) -> list[Any]:
    raise RuntimeError("model unavailable")


# ---- the streamed assistant turn --------------------------------------------


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_agent_turn_can_stream_keepalives_then_the_result(client: TestClient, seed_workspace) -> None:
    seed_workspace()
    with patch("research_tree.api.routes.agent.KEEPALIVE_SECONDS", 0.01):
        with client.stream(
            "POST",
            "/workspaces/workspace-1/agent",
            json={"message": "Explain this workspace."},
            headers={"Accept": "text/event-stream"},
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            frames = read_sse_events(response, limit=2)
    # The turn is narrated before it is answered: the first frame is the
    # model turn starting, the last the answer.
    assert frames[0] == 'event: progress\ndata: {"kind": "thinking"}'
    assert frames[1].startswith("event: result\n")
    assert '"status": "completed"' in frames[1]


def test_agent_refusals_are_plain_errors_even_when_streaming(client: TestClient) -> None:
    response = client.post(
        "/workspaces/missing/agent",
        json={"message": "Hello"},
        headers={"Accept": "text/event-stream"},
    )
    assert response.status_code == 404
    assert response.json()["error_code"] == "workspace_not_found"


# ---- topic tokens -----------------------------------------------------------


def test_topic_review_tokens_are_shared_through_redis(redis_env, repository: WorkspaceRepository) -> None:
    token = _issue_topic_review_approval("Prompting")
    assert redis_env.get(f"topic_review:{token}") == b"Prompting"
    service = TopicReviewService(repository)
    assert service.consume_approved_topic(token=token, topic="Other") is None
    token = _issue_topic_review_approval("Prompting")
    assert service.consume_approved_topic(token=token, topic="Prompting") == "Prompting"
    assert service.consume_approved_topic(token=token, topic="Prompting") is None


# ---- the shared Semantic Scholar lane ---------------------------------------


def test_the_request_lane_is_spaced_on_the_redis_clock(redis_client) -> None:
    limiter = RateLimiter(redis_client=redis_client, redis_key="test:s2:lane")
    started = time.monotonic()
    for _ in range(3):
        limiter.acquire(0.2)
    assert time.monotonic() - started >= 0.4
    assert redis_client.exists("test:s2:lane")


def test_a_broken_redis_falls_back_to_the_local_limiter() -> None:
    class Broken:
        def register_script(self, script: str) -> Any:
            raise ConnectionError("down")

    limiter = RateLimiter(redis_client=Broken())
    started = time.monotonic()
    limiter.acquire(0.05)
    limiter.acquire(0.05)
    assert time.monotonic() - started >= 0.05


def test_the_shared_lane_comes_back_after_redis_recovers() -> None:
    """The fallback used to latch, so one blip split the lane until a restart."""

    class Flaky:
        def __init__(self) -> None:
            self.calls = 0
            self.healthy = False

        def register_script(self, script: str) -> Any:
            def run(keys: list[str], args: list[int]) -> int:
                self.calls += 1
                if not self.healthy:
                    raise ConnectionError("reset")
                return 0

            return run

    redis = Flaky()
    limiter = RateLimiter(redis_client=redis)

    limiter.acquire(0.01)
    limiter.acquire(0.01)
    assert redis.calls == 1

    redis.healthy = True
    limiter._redis_unavailable_until = 0.0
    limiter.acquire(0.01)
    assert redis.calls == 2


def test_an_artifact_put_replaces_what_was_there(tmp_path) -> None:
    """Regenerating a paper's annotations writes new content under the same key."""

    store = FilesystemArtifactStore(tmp_path / "artifacts")
    key = "annotations/workspace-1/abc123"

    store.put(key, b"the first pass")
    store.put(key, b"regenerated from scratch")

    assert store.get(key) == b"regenerated from scratch"


# ---- change notifications ---------------------------------------------------


class _RecordingRedis:
    def __init__(self) -> None:
        self.channels: list[str] = []

    def publish(self, channel: str, message: str) -> int:
        self.channels.append(channel)
        return 1


def test_the_postgres_repository_announces_committed_changes(postgres_engine, tmp_path) -> None:
    from research_tree.workspace.postgres_repository import PostgresWorkspaceRepository, workspaces_channel

    redis = _RecordingRedis()
    repository = PostgresWorkspaceRepository(
        postgres_engine, artifacts=FilesystemArtifactStore(tmp_path / "artifacts"), redis=redis
    )
    workspace_id = f"notify-{uuid.uuid4().hex[:8]}"
    repository.save_workspace_version(
        workspace_id,
        {
            "schema_version": "research_tree_workspace.v1",
            "workspace_id": workspace_id,
            "topic": "Notify",
            "title": "Notify",
            "root": {"node_id": "root", "label": "Notify", "overview": ""},
            "tree": {"root_node_id": "root", "nodes": []},
            "paper_paths": [],
            "paper_cards": {},
        },
        actor="system",
        parent_version_hash=None,
        reason="seed",
    )
    assert redis.channels == [workspaces_channel(repository.owner_id)]
    run_id = f"pipeline_{uuid.uuid4().hex}"
    repository.save_pipeline_run(
        {"run_id": run_id, "workspace_id": workspace_id, "status": "completed", "artifacts": {}}
    )
    assert redis.channels[1:] == [f"research_tree:runs:{run_id}", workspaces_channel(repository.owner_id)]
    # A read that changes nothing announces nothing.
    repository.list_pipeline_runs(workspace_id)
    assert len(redis.channels) == 3


# ---- the worker seam --------------------------------------------------------


def test_builds_go_to_the_queue_only_when_redis_is_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    assert default_pipeline_dispatch() is _dispatch_local_thread
    monkeypatch.setenv("RESEARCH_TREE_REDIS_URL", "redis://localhost:6379/0")
    assert default_pipeline_dispatch() is _dispatch_celery


def test_builds_take_turns_on_the_worker(redis_env, monkeypatch: pytest.MonkeyPatch) -> None:
    from celery.exceptions import Retry

    from research_tree import tasks

    executed: list[str] = []
    touched: list[str] = []

    class Service:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        def _execute(self, run_id: str) -> None:
            executed.append(run_id)
            assert redis_env.get(tasks.BUILD_SLOT_KEY) == run_id.encode("utf-8")

    class Repository:
        def for_owner(self, owner_id: str) -> "Repository":
            assert owner_id == "local_user"
            return self

        def touch_pipeline_run(self, run_id: str) -> None:
            touched.append(run_id)

    monkeypatch.setattr("research_tree.services.pipeline.WorkspacePipelineService", Service)
    monkeypatch.setattr(tasks, "_repository", lambda: Repository())
    tasks.run_pipeline.apply(args=["local_user", "pipeline_one"], throw=True)
    assert executed == ["pipeline_one"]
    assert redis_env.get(tasks.BUILD_SLOT_KEY) is None

    redis_env.set(tasks.BUILD_SLOT_KEY, "pipeline_other")
    with pytest.raises(Retry):
        tasks.run_pipeline.apply(args=["local_user", "pipeline_two"], throw=True)
    assert executed == ["pipeline_one"]
    assert touched == ["pipeline_two"]
    assert redis_env.get(tasks.BUILD_SLOT_KEY) == b"pipeline_other"


def test_the_worker_knows_every_task() -> None:
    from research_tree.tasks import app

    for name in (
        "research_tree.run_pipeline",
        "research_tree.generate_annotations",
        "research_tree.keep_database_awake",
        "research_tree.backup_database",
    ):
        assert name in app.tasks
    assert app.conf.broker_transport_options["global_keyprefix"] == "{research-tree}"
