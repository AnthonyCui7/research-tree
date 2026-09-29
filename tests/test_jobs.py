"""What Redis and the worker add: queued annotation jobs, the streamed
assistant turn, per-account ceilings, shared topic tokens, the shared
Semantic Scholar lane, and change notifications from the repository.

The Redis-backed tests run when RESEARCH_TREE_TEST_REDIS_URL points at a
scratch instance (CI does); the others are keyless and always run.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
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

    # A reader polling while the model works sees the job running, not still
    # queued until its first heartbeat.
    status_while_running: list[str] = []

    def annotate(*args, **kwargs):
        status_while_running.append(client.get(status_url).json()["status"])
        return annotator(*args, **kwargs)

    service.generate_annotations = annotate
    service.run_annotation_job(load_annotation_job(job["job_id"], redis=service._redis))
    assert status_while_running == ["running"]
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
    token = _issue_topic_review_approval(repository.owner_id, "Prompting")
    assert redis_env.get(f"topic_review:{repository.owner_id}:{token}") == b"Prompting"
    service = TopicReviewService(repository)
    assert service.consume_approved_topic(token=token, topic="Other") is None
    token = _issue_topic_review_approval(repository.owner_id, "Prompting")
    # The approval is the reviewing account's: another account cannot spend it.
    stranger = TopicReviewService(SimpleNamespace(owner_id="someone-else"))
    assert stranger.consume_approved_topic(token=token, topic="Prompting") is None
    assert service.consume_approved_topic(token=token, topic="Prompting") == "Prompting"
    assert service.consume_approved_topic(token=token, topic="Prompting") is None


# ---- limits counted across processes ------------------------------------------


def test_limits_are_counted_across_processes_and_hold_no_addresses(redis_client) -> None:
    from research_tree.auth.throttle import _FixedWindowLimiter

    api, another_replica = _FixedWindowLimiter(redis_client), _FixedWindowLimiter(redis_client)
    key = "login:ip:203.0.113.9:email:someone@example.com"

    assert api.hit(key, 2, 60)
    assert another_replica.hit(key, 2, 60)
    assert not api.hit(key, 2, 60)
    stored = [name.decode("utf-8") for name in redis_client.keys("throttle:*")]
    assert len(stored) == 1
    assert "203.0.113.9" not in stored[0] and "someone" not in stored[0]
    assert 0 < redis_client.ttl(stored[0]) <= 60


def test_limits_fall_back_to_this_process_when_redis_fails() -> None:
    from research_tree.auth.throttle import _FixedWindowLimiter

    class Broken:
        def register_script(self, script: str) -> Any:
            raise ConnectionError("down")

    limiter = _FixedWindowLimiter(Broken())
    assert limiter.hit("key", 1, 60)
    assert not limiter.hit("key", 1, 60)


def test_costly_actions_are_counted_per_account_and_never_for_the_local_profile(
    monkeypatch,
) -> None:
    from research_tree.auth import throttle
    from research_tree.principal import LOCAL_PRINCIPAL, Principal, bind_principal
    from research_tree.services.errors import RateLimitedError

    throttle.reset()
    monkeypatch.setitem(throttle.ACTIONS_PER_ACCOUNT, "topic_review", (2, 3600, "Too many."))
    with bind_principal(LOCAL_PRINCIPAL):
        for _ in range(5):
            throttle.count_account_action("topic_review")
    with bind_principal(Principal(user_id="account-a", email="a@example.com")):
        throttle.count_account_action("topic_review")
        throttle.count_account_action("topic_review")
        with pytest.raises(RateLimitedError):
            throttle.count_account_action("topic_review")
    # Another account has its own count.
    with bind_principal(Principal(user_id="account-b", email="b@example.com")):
        throttle.count_account_action("topic_review")
    throttle.reset()


# ---- the shared Semantic Scholar lane ---------------------------------------


def test_the_request_lane_is_spaced_on_the_redis_clock(redis_client) -> None:
    limiter = RateLimiter(redis_client=redis_client, redis_key="test:s2:lane")
    started = time.monotonic()
    for _ in range(3):
        limiter.acquire(0.2)
    assert time.monotonic() - started >= 0.4
    assert redis_client.exists("test:s2:lane")


def test_a_deep_queue_keeps_its_place_in_the_lane(redis_client) -> None:
    """The lane's key has to outlive the furthest slot it has handed out."""

    limiter = RateLimiter(redis_client=redis_client, redis_key="test:s2:deep_lane")
    furthest = 0.0
    for _ in range(40):
        furthest = limiter._reserve_across_containers(0.1)
    assert furthest >= 3.5
    assert redis_client.pttl("test:s2:deep_lane") >= furthest * 1000


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
            assert redis_env.get(tasks.BUILD_SLOT_KEY) == f"local_user:{run_id}".encode("utf-8")
            # A lease, not a reservation for the whole build.
            assert 0 < redis_env.ttl(tasks.BUILD_SLOT_KEY) <= tasks.BUILD_SLOT_LEASE_SECONDS

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

    # A slot another build holds is waited for, whatever that build's run
    # says: a cancelled build still runs until its stage ends.
    redis_env.set(tasks.BUILD_SLOT_KEY, "local_user:pipeline_other", ex=60)
    with pytest.raises(Retry):
        tasks.run_pipeline.apply(args=["local_user", "pipeline_two"], throw=True)
    assert executed == ["pipeline_one"]
    assert touched == ["pipeline_two"]
    assert redis_env.get(tasks.BUILD_SLOT_KEY) == b"local_user:pipeline_other"

    # The broker delivering the holder again neither runs it twice nor frees
    # the slot it is running in.
    tasks.run_pipeline.apply(args=["local_user", "pipeline_other"], throw=True)
    assert executed == ["pipeline_one"]
    assert redis_env.get(tasks.BUILD_SLOT_KEY) == b"local_user:pipeline_other"


def test_a_build_slot_lease_renews_and_releases_only_its_own_slot(redis_env, monkeypatch) -> None:
    from research_tree import tasks

    monkeypatch.setattr(tasks, "BUILD_SLOT_RENEW_SECONDS", 0.05)
    redis_env.set(tasks.BUILD_SLOT_KEY, "local_user:pipeline_one", ex=5)
    lease = tasks._BuildSlotLease(redis_env, "local_user:pipeline_one")
    deadline = time.monotonic() + 5
    while redis_env.ttl(tasks.BUILD_SLOT_KEY) <= 5 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert redis_env.ttl(tasks.BUILD_SLOT_KEY) > 5
    # The lease lapsed and the next build took the slot: neither the renewals
    # still running nor the release may touch the new holder's slot.
    redis_env.set(tasks.BUILD_SLOT_KEY, "local_user:pipeline_two", ex=60)
    time.sleep(0.2)
    assert redis_env.ttl(tasks.BUILD_SLOT_KEY) <= 60
    lease.release()
    assert redis_env.get(tasks.BUILD_SLOT_KEY) == b"local_user:pipeline_two"


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


def test_a_job_whose_worker_stopped_is_failed_on_read(client: TestClient, job_service) -> None:
    from research_tree.services import annotations as jobs

    service, _, queued = job_service
    client.app.dependency_overrides[get_paper_annotation_service] = lambda: service

    job = client.get(annotations_url()).json()
    stored = json.loads(service._redis.get(f"annotations:job:{job['job_id']}"))
    stale = (datetime.now(UTC) - timedelta(minutes=10)).isoformat()
    service._redis.set(
        f"annotations:job:{job['job_id']}",
        json.dumps({**stored, "status": "running", "heartbeat_at": stale}),
    )

    status = client.get(f"/workspaces/sampling/paper-annotations/jobs/{job['job_id']}").json()
    assert status["status"] == "failed"
    assert status["detail"] == jobs.RECLAIMED_JOB_DETAIL

    # The next request does not join the dead job: it starts a fresh one.
    again = client.get(annotations_url())
    assert again.status_code == 202
    assert again.json()["job_id"] != job["job_id"]
    assert queued == [job["job_id"], again.json()["job_id"]]


def test_a_dropped_event_stream_hands_its_redis_connection_back(redis_env) -> None:
    import anyio

    from research_tree.api.routes.workspaces import _ChangeSignal
    from research_tree.redis_client import get_async_redis

    async def stream_until_the_client_leaves() -> None:
        # Leaving mid-wait is what a closed tab does: the stream is cancelled.
        with anyio.move_on_after(0.2):
            async with _ChangeSignal("workspaces:test", poll_seconds=0.05) as changes:
                while True:
                    await changes.wait()

    async def drop_streams() -> int:
        for _ in range(3):
            await stream_until_the_client_leaves()
        # Private to redis-py, but it is the pool's own count of checked-out
        # connections, and nothing public reports it.
        return len(get_async_redis().connection_pool._in_use_connections)

    assert anyio.run(drop_streams) == 0


def test_two_deliveries_of_one_annotation_job_start_it_once(redis_env) -> None:
    from research_tree.services.annotations import _claim_queued_job, _save_job

    job = {"job_id": "job-1", "status": "queued"}
    _save_job(redis_env, job)
    # Both deliveries loaded the job while it was still queued.
    first, second = dict(job), dict(job)
    assert _claim_queued_job(redis_env, first)
    assert not _claim_queued_job(redis_env, second)
