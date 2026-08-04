from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from research_tree.api.app import create_app
from research_tree.api.dependencies import (
    get_repository,
    get_workspace_agent_service,
)
from research_tree.api.routes import agent, health, reviews, workspaces
from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.llm import DeterministicWorkspaceAgentLlmClient
from research_tree.agents.workspace.llm import AgentTurn, ToolCall
from research_tree.services.agent import WorkspaceAgentService
from research_tree.services.errors import InvalidPayloadError
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def test_health() -> None:
    client, _repository = _client_with_repository()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_topic_review_approval_is_single_use() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    service = TopicReviewService(repository)
    reviewed = {
        "normalized_topic": "Prompting",
        "is_research_topic": True,
        "guidance": "",
        "existing_workspace_id": None,
    }
    with (
        # review() only reaches the model — and only mints a token — when a key
        # is set. Without this the test passes locally off a loaded .env and
        # fails in CI, which runs keyless.
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch("research_tree.services.topics._review_with_model", return_value=reviewed),
    ):
        result = service.review("prompting")

    token = result["topic_review_token"]
    assert isinstance(token, str)
    assert service.consume_approved_topic(token=token, topic="Prompting") == "Prompting"
    assert service.consume_approved_topic(token=token, topic="Prompting") is None


def test_paper_content_is_stored_outside_versioned_workspace_json() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)

    content_key = repository.save_paper_content(
        "workspace-1",
        "doi:10.1/example",
        {"status": "available", "full_text": "complete paper text"},
    )

    assert len(content_key) == 64
    assert repository.get_paper_content("workspace-1", "doi:10.1/example")["full_text"] == "complete paper text"
    assert "full_text" not in str(repository.get_current_workspace("workspace-1"))


def test_workspace_current_versions_events_and_reviews() -> None:
    client, repository = _client_with_repository()
    base_hash = _seed_current(repository)
    event_id = repository.append_workspace_event(
        "workspace-1",
        actor="system",
        event_type="workspace_seeded",
        target_ids={},
        before_hash=None,
        after_hash=base_hash,
        payload={"reason": "test"},
    )
    _save_pending_review(repository, review_id="review-1")

    current_response = client.get("/workspaces/workspace-1")
    versions_response = client.get("/workspaces/workspace-1/versions")
    events_response = client.get("/workspaces/workspace-1/events")
    reviews_response = client.get("/workspaces/workspace-1/reviews")

    assert current_response.status_code == 200
    assert current_response.json()["workspace_version_hash"] == base_hash
    assert current_response.json()["workspace"]["title"] == "Test Topic"
    assert versions_response.status_code == 200
    assert versions_response.json()["versions"][0]["version_hash"] == base_hash
    assert events_response.status_code == 200
    assert events_response.json()["events"][0]["event_id"] == event_id
    assert reviews_response.status_code == 200
    assert reviews_response.json()["reviews"][0]["review_id"] == "review-1"


def test_list_workspaces_returns_repository_summaries() -> None:
    client, repository = _client_with_repository()
    base_hash = _seed_current(repository)

    response = client.get("/workspaces")

    assert response.status_code == 200
    assert response.json() == {
        "workspaces": [
            {
                "workspace_id": "workspace-1",
                "workspace_version_hash": base_hash,
                "title": "Test Topic",
                "topic": "test topic",
                "paper_count": 2,
                "branch_count": 1,
                "paper_path_count": 1,
                "updated_at": "2026-07-05T00:00:00+00:00",
            }
        ]
    }


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_topic_review_fails_closed_without_an_llm_key() -> None:
    client, repository = _client_with_repository()
    _seed_current(repository)

    response = client.post("/workspaces/topic-review", json={"topic": "  Test   Topic "})

    assert response.status_code == 200
    assert response.json()["can_create"] is False
    assert response.json()["existing_workspace"] is None
    assert response.json()["is_research_topic"] is False


@patch.dict(os.environ, {"OPENAI_API_KEY": "configured-for-test"})
def test_topic_review_uses_model_duplicate_selection() -> None:
    client, repository = _client_with_repository()
    _seed_current(repository)

    with patch(
        "research_tree.services.topics._review_with_model",
        return_value={
            "normalized_topic": "Test Topic",
            "is_research_topic": True,
            "guidance": "",
            "existing_workspace_id": "workspace-1",
        },
    ) as review_model:
        response = client.post("/workspaces/topic-review", json={"topic": "Test Topic"})

    assert response.status_code == 200
    assert response.json()["can_create"] is False
    assert response.json()["existing_workspace"]["workspace_id"] == "workspace-1"
    assert review_model.call_args.kwargs["existing_workspaces"] == [
        {"workspace_id": "workspace-1", "topic": "test topic", "title": "Test Topic"}
    ]


@patch.dict(os.environ, {"OPENAI_API_KEY": "configured-for-test"})
def test_topic_review_rejects_an_unreadable_paper_link() -> None:
    client, _repository = _client_with_repository()

    with patch(
        "research_tree.services.topics._linked_paper_metadata",
        return_value=None,
    ), patch("research_tree.services.topics._review_with_model") as review_model:
        response = client.post(
            "/workspaces/topic-review",
            json={"topic": "https://arxiv.org/abs/9999.99999"},
        )

    assert response.status_code == 200
    assert response.json()["can_create"] is False
    assert "could not identify a paper" in response.json()["guidance"]
    review_model.assert_not_called()


def test_restore_and_delete_workspace_lifecycle() -> None:
    client, repository = _client_with_repository()
    first_hash = _seed_current(repository)
    second = {**repository.get_current_workspace("workspace-1"), "title": "Second"}
    second_hash = repository.save_workspace_version(
        "workspace-1",
        second,
        actor="user",
        parent_version_hash=first_hash,
        reason="second",
    )

    restore = client.post(
        f"/workspaces/workspace-1/versions/{first_hash}/restore",
        json={"expected_version_hash": second_hash},
    )
    delete = client.request(
        "DELETE",
        "/workspaces/workspace-1",
        json={"expected_version_hash": first_hash},
    )

    assert restore.status_code == 200
    assert restore.json()["workspace_version_hash"] == first_hash
    assert delete.status_code == 200
    assert client.get("/workspaces/workspace-1").status_code == 404


def test_deleting_workspace_cancels_active_pipeline_and_blocks_late_publication() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)
    stale_workspace = repository.get_current_workspace("workspace-1")
    repository.save_pipeline_run(
        {
            "run_id": "pipeline-active",
            "workspace_id": "workspace-1",
            "status": "running",
            "topic": "test topic",
            "created_at": "2026-07-12T00:00:00+00:00",
        }
    )

    repository.delete_workspace("workspace-1")

    assert repository.get_pipeline_run("pipeline-active")["status"] == "cancelled"
    with pytest.raises(RuntimeError, match="no longer owns"):
        publish_workspace_version(
            repository_dir=repository.base_dir,
            workspace=stale_workspace,
            reason="late pipeline publication",
            event_type="workspace_pipeline_completed",
            event_payload={},
            pipeline_run_id="pipeline-active",
        )
    with pytest.raises(FileNotFoundError):
        repository.get_current_workspace("workspace-1")


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_chat_agent_request_returns_completed() -> None:
    client, repository = _client_with_repository()
    _seed_current(repository)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Explain this workspace."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "completed"
    assert payload["final_response"]
    assert payload["review_id"] is None


def test_agent_request_passes_bounded_conversation_history() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)
    captured: dict[str, Any] = {}

    class RecordingGraph:
        def stream(self, graph_input: Any, *, config: dict[str, Any], stream_mode: str) -> list[dict[str, Any]]:
            captured["graph_input"] = graph_input
            captured["config"] = config
            captured["stream_mode"] = stream_mode
            return [
                {
                    "status": "completed",
                    "thread_id": graph_input["thread_id"],
                    "agent_run_id": "agent-run-test",
                    "final_response": "Done.",
                    "warnings": [],
                    "errors": [],
                }
            ]

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_workspace_agent_service] = lambda: WorkspaceAgentService(
        repository,
        graph_factory=lambda _repository: RecordingGraph(),
    )
    client = TestClient(app)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={
            "message": "Use that previous point.",
            "thread_id": "thread-1",
            "conversation_history": [
                {"role": "user", "text": "First question."},
                {"role": "assistant", "text": "First answer."},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["thread_id"] == "thread-1"
    assert captured["graph_input"]["conversation_history"] == [
        {"role": "user", "text": "First question."},
        {"role": "assistant", "text": "First answer."},
    ]


def test_remove_paper_request_persists_a_review_without_calling_the_model() -> None:
    """Paper removal is deterministic, but it still goes through the graph.

    It used to short-circuit before intent classification on any message
    containing "remove", which turned questions like "why would I remove the
    DPR paper?" into deletion proposals. The construction node still recognizes
    the removal and skips the constructor, so no model writes the patch.
    """

    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)

    def graph_factory(active_repository: LocalJsonWorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_edit_tool_turn("Remove Core Method paper.")]
            ),
            workspace_constructor=_unexpected_constructor,
            workspace_repository=active_repository,
        )

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_workspace_agent_service] = lambda: WorkspaceAgentService(
        repository,
        graph_factory=graph_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Remove Core Method paper."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "pending_review"
    assert payload["diff_summary"]["operation_types"] == [
        "demote_visible_paper",
        "update_reading_order",
        "update_workspace_subtree",
    ]
    review = repository.get_pending_review("workspace-1", payload["review_id"])
    assert "p1" not in review["proposed_workspace"]["paper_cards"]
    [placement] = review["proposed_workspace"]["removed_paper_placements"]
    assert placement["paper_id"] == "p1"
    assert placement["paper_card"]["title"] == "Core Method"
    assert placement["branch_placements"][0]["branch_id"] == "branch-main"


def _unexpected_constructor(**_kwargs: Any) -> dict[str, Any]:
    raise AssertionError("deterministic paper removal must not call the constructor")


def test_noop_workspace_modification_does_not_persist_review() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)

    def graph_factory(active_repository: LocalJsonWorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_edit_tool_turn("No matching paper exists.")]
            ),
            workspace_constructor=lambda **kwargs: kwargs["base_workspace"],
            workspace_repository=active_repository,
        )

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_workspace_agent_service] = lambda: WorkspaceAgentService(
        repository,
        graph_factory=graph_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Remove a paper that does not exist."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "completed"
    assert payload["review_id"] is None
    assert payload["final_response"] == "I could not find a workspace change to propose."
    assert repository.list_workspace_reviews("workspace-1") == []


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_modify_agent_request_returns_pending_review_and_persists_review() -> None:
    client, repository = _client_with_repository()
    _seed_current(repository)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Rename the branch."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "pending_review"
    assert payload["review_id"]
    review = repository.get_pending_review("workspace-1", payload["review_id"])
    assert review["status"] == "pending"
    assert review["interrupt_payload"]["review_id"] == payload["review_id"]


def test_get_persisted_review() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-1")

    response = client.get("/workspaces/workspace-1/reviews/review-1")

    assert response.status_code == 200
    assert response.json()["review"]["review_id"] == "review-1"


def test_approve_applies_current_workspace() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-approve")

    response = client.post("/workspaces/workspace-1/reviews/review-approve/approve")

    current = repository.get_current_workspace("workspace-1")
    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "approved_applied"
    assert current["title"] == "Changed Topic"
    assert payload["workspace_version_hash"] == workspace_version_hash(current)


def test_reject_keeps_current_workspace_unchanged() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-reject")
    before = repository.get_current_workspace("workspace-1")

    response = client.post(
        "/workspaces/workspace-1/reviews/review-reject/reject",
        json={"reason": "not right"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert repository.get_current_workspace("workspace-1") == before


def test_double_approve_and_reject_are_idempotent() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-approve")
    first_approve = client.post("/workspaces/workspace-1/reviews/review-approve/approve")
    second_approve = client.post("/workspaces/workspace-1/reviews/review-approve/approve")

    _save_pending_review(
        repository,
        review_id="review-reject",
        proposed_workspace={**repository.get_current_workspace("workspace-1"), "title": "Rejected"},
    )
    first_reject = client.post("/workspaces/workspace-1/reviews/review-reject/reject")
    second_reject = client.post("/workspaces/workspace-1/reviews/review-reject/reject")

    assert first_approve.status_code == 200
    assert first_approve.json()["idempotent"] is False
    assert second_approve.status_code == 200
    assert second_approve.json()["idempotent"] is True
    assert first_reject.status_code == 200
    assert first_reject.json()["idempotent"] is False
    assert second_reject.status_code == 200
    assert second_reject.json()["idempotent"] is True


def test_terminal_review_transition_conflicts() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-rejected")
    client.post("/workspaces/workspace-1/reviews/review-rejected/reject")
    approve_after_reject = client.post(
        "/workspaces/workspace-1/reviews/review-rejected/approve"
    )

    _save_pending_review(
        repository,
        review_id="review-approved",
        proposed_workspace={**repository.get_current_workspace("workspace-1"), "title": "Approved"},
    )
    client.post("/workspaces/workspace-1/reviews/review-approved/approve")
    reject_after_approve = client.post(
        "/workspaces/workspace-1/reviews/review-approved/reject"
    )

    assert approve_after_reject.status_code == 409
    assert reject_after_approve.status_code == 409


def test_stale_approval_returns_conflict_and_preserves_current_workspace() -> None:
    client, repository = _client_with_repository()
    base_hash = _save_pending_review(repository, review_id="review-stale")
    user_changed = {**_workspace(), "title": "User Changed Topic"}
    repository.save_workspace_version(
        "workspace-1",
        user_changed,
        actor="user",
        parent_version_hash=base_hash,
        reason="manual edit",
    )

    response = client.post("/workspaces/workspace-1/reviews/review-stale/approve")

    current = repository.get_current_workspace("workspace-1")
    assert response.status_code == 409
    assert current["title"] == "User Changed Topic"


def test_edit_review_revalidates_and_persists_new_pending_review() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-edit")
    edited_workspace = {**repository.get_current_workspace("workspace-1"), "title": "Edited Topic"}

    response = client.post(
        "/workspaces/workspace-1/reviews/review-edit/edit",
        json={"proposed_workspace": edited_workspace},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "pending_review"
    assert payload["new_review_id"]
    assert repository.get_review("workspace-1", "review-edit")["status"] == "edited"
    assert repository.get_pending_review("workspace-1", payload["new_review_id"])


def test_edit_review_validation_failure_returns_failed_validation() -> None:
    client, repository = _client_with_repository()
    _save_pending_review(repository, review_id="review-edit-invalid")
    invalid_workspace = {**repository.get_current_workspace("workspace-1"), "paper_cards": {}}

    response = client.post(
        "/workspaces/workspace-1/reviews/review-edit-invalid/edit",
        json={"proposed_workspace": invalid_workspace},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "failed_validation"
    assert payload["errors"]
    assert repository.get_review("workspace-1", "review-edit-invalid")["status"] == "edited"


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_guardrail_rejection_returns_failed_guardrail_and_skips_retrieval_runner() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)
    retrieval_calls: list[object] = []

    def graph_factory(active_repository: LocalJsonWorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _rerun_tool_turn("Find more papers for this topic."),
                ]
            ),
            workspace_repository=active_repository,
            retrieval_runner=lambda config: retrieval_calls.append(config) or {},
        )

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_workspace_agent_service] = lambda: WorkspaceAgentService(
        repository,
        graph_factory=graph_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Find more papers for this topic."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed_guardrail"
    assert retrieval_calls == []


def test_invalid_workspace_proposal_returns_failed_validation_status() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)

    def graph_factory(active_repository: LocalJsonWorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[_edit_tool_turn("Return invalid workspace.")]
            ),
            workspace_constructor=lambda **_kwargs: {"schema_version": "bad"},
            workspace_repository=active_repository,
        )

    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    app.dependency_overrides[get_workspace_agent_service] = lambda: WorkspaceAgentService(
        repository,
        graph_factory=graph_factory,
    )
    client = TestClient(app)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Make an invalid workspace proposal."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "failed_validation"
    assert payload["validation_summary"]["valid"] is False
    assert payload["errors"]


def test_route_modules_do_not_perform_low_level_file_writes() -> None:
    route_source = "\n".join(
        inspect.getsource(module)
        for module in (agent, health, reviews, workspaces)
    )

    forbidden_calls = [
        "_write_json_atomic(",
        "save_workspace_version(",
        "append_workspace_event(",
        "save_pending_review(",
        "approve_review_once(",
        "reject_review_once(",
        "edit_review_once(",
    ]
    for forbidden_call in forbidden_calls:
        assert forbidden_call not in route_source


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_failed_pipeline_does_not_publish_a_partial_workspace() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    service = WorkspacePipelineService(
        repository,
        repo_root=_temp_dir(),
        dispatch=lambda callback, _name: callback(),
    )

    with patch(
        "research_tree.services.pipeline.run_workspace_candidate_preparation_pipeline",
        side_effect=RuntimeError("Semantic Scholar is unavailable"),
    ):
        run = _start_approved(service, repository, "Failure-Safe RAG")

    saved_run = repository.get_pipeline_run(run["run_id"])
    assert saved_run["status"] == "failed"
    assert saved_run["stages"]["candidates"]["status"] == "failed"
    assert saved_run["stages"]["candidates"]["error"] == "Semantic Scholar is unavailable"
    with pytest.raises(FileNotFoundError):
        repository.get_current_workspace(run["workspace_id"])


def test_workspace_pipeline_always_uses_luna_for_construction() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    service = WorkspacePipelineService(
        repository,
        repo_root=_temp_dir(),
        dispatch=lambda callback, _name: callback(),
    )

    with patch(
        "research_tree.services.pipeline.run_workspace_candidate_preparation_pipeline",
        side_effect=RuntimeError("stop after run reservation"),
    ):
        run = _start_approved(service, repository, "Model-Locked RAG")

    saved_run = repository.get_pipeline_run(run["run_id"])
    assert saved_run["model"] == "gpt-5.6-luna"


def test_failed_partial_rerun_keeps_source_artifacts_unchanged() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    current_hash = _seed_current(repository)
    # Artifact paths are confined to the data root, so the fixture lives there.
    repo_root = _temp_dir()
    data_dir = _temp_dir()
    source_dir = data_dir / "source-run"
    source_dir.mkdir()
    candidate_json = source_dir / "llm_candidate_papers.json"
    candidate_json.write_text("{}", encoding="utf-8")
    source_run = {
        "run_id": "pipeline_source",
        "workspace_id": "workspace-1",
        "status": "completed",
        "created_at": "2026-01-01T00:00:00+00:00",
        "artifacts": {
            "run_dir": str(source_dir),
            "candidate_json": str(candidate_json),
        },
    }
    repository.save_pipeline_run(source_run)
    service = WorkspacePipelineService(
        repository,
        repo_root=repo_root,
        dispatch=lambda callback, _name: callback(),
    )

    with (
        patch.dict(os.environ, {"RESEARCH_TREE_DATA_DIR": str(data_dir)}),
        patch(
            "research_tree.services.pipeline.construct_workspace_from_candidates",
            side_effect=RuntimeError("invalid model response"),
        ),
    ):
        run = service.rerun(
            "workspace-1",
            start_stage="construct",
            expected_version_hash=current_hash,
        )

    saved_run = repository.get_pipeline_run(run["run_id"])
    assert saved_run["status"] == "failed"
    assert saved_run["stages"]["construct"]["status"] == "failed"
    assert saved_run["artifacts"]["run_dir"] != str(source_dir)
    assert candidate_json.read_text(encoding="utf-8") == "{}"


def test_unpublished_paper_content_does_not_block_workspace_retry_id() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    service = WorkspacePipelineService(repository, repo_root=_temp_dir())
    (repository.base_dir / "retrieval-augmented-generation" / "paper_content").mkdir(
        parents=True
    )

    assert service._available_workspace_id("retrieval-augmented-generation") == (
        "retrieval-augmented-generation"
    )


def test_pipeline_rerun_rejects_another_active_run_for_the_workspace() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)
    repository.save_pipeline_run(
        {
            "run_id": "pipeline_source",
            "workspace_id": "workspace-1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00+00:00",
            "artifacts": {},
        }
    )
    repository.save_pipeline_run(
        {
            "run_id": "pipeline_active",
            "workspace_id": "workspace-1",
            "status": "running",
            "runner_pid": os.getpid(),
            "created_at": "2026-01-02T00:00:00+00:00",
        }
    )
    service = WorkspacePipelineService(repository, repo_root=_temp_dir())

    with pytest.raises(InvalidPayloadError, match="already active"):
        service.rerun("workspace-1", start_stage="related")


def test_dead_pipeline_owner_is_reclaimed_before_a_new_run_is_reserved() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    stale_run = {
        "run_id": "pipeline_stale",
        "workspace_id": "workspace-1",
        "status": "running",
        "runner_pid": 12345,
        "created_at": "2026-01-01T00:00:00+00:00",
    }
    new_run = {
        "run_id": "pipeline_new",
        "workspace_id": "workspace-1",
        "status": "queued",
        "runner_pid": os.getpid(),
        "created_at": "2026-01-02T00:00:00+00:00",
    }
    repository.save_pipeline_run(stale_run)

    with patch(
        "research_tree.workspace.repository.os.kill",
        side_effect=ProcessLookupError,
    ):
        repository.reserve_pipeline_rerun(new_run)

    assert repository.get_pipeline_run("pipeline_stale")["status"] == "failed"
    assert repository.get_pipeline_run("pipeline_new")["status"] == "queued"


def _client_with_repository() -> tuple[TestClient, LocalJsonWorkspaceRepository]:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app), repository


def _edit_tool_turn(instruction: str) -> AgentTurn:
    """One model turn that asks for a workspace edit."""

    arguments = {"instruction": instruction}
    return AgentTurn(
        output_items=[
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "propose_workspace_edit",
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[
            ToolCall(
                call_id="call_1", name="propose_workspace_edit", arguments=arguments
            )
        ],
        output_text=None,
    )


def _rerun_tool_turn(reason: str) -> AgentTurn:
    """One model turn that asks to rerun the candidates stage."""

    arguments = {"stage": "candidates", "reason": reason}
    return AgentTurn(
        output_items=[
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "propose_pipeline_rerun",
                "arguments": json.dumps(arguments),
            }
        ],
        tool_calls=[
            ToolCall(
                call_id="call_1", name="propose_pipeline_rerun", arguments=arguments
            )
        ],
        output_text=None,
    )


def _temp_dir() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp())


def _start_approved(
    service: WorkspacePipelineService,
    repository: LocalJsonWorkspaceRepository,
    topic: str,
) -> dict[str, Any]:
    """Start a build through the real topic-approval gate, model stubbed out."""

    reviewed = {
        "normalized_topic": topic,
        "is_research_topic": True,
        "guidance": "",
        "existing_workspace_id": None,
    }
    with (
        # review() only reaches the model (and issues a token) when a key is set.
        patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
        patch("research_tree.services.topics._review_with_model", return_value=reviewed),
    ):
        review = TopicReviewService(repository).review(topic)
    return service.start_approved_new_workspace(
        topic=topic,
        topic_review_token=str(review["topic_review_token"]),
    )


def _seed_current(repository: LocalJsonWorkspaceRepository) -> str:
    return repository.save_workspace_version(
        "workspace-1",
        _workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )


def _save_pending_review(
    repository: LocalJsonWorkspaceRepository,
    *,
    review_id: str,
    proposed_workspace: dict[str, Any] | None = None,
) -> str:
    try:
        current = repository.get_current_workspace("workspace-1")
        base_hash = workspace_version_hash(current)
    except FileNotFoundError:
        base_hash = _seed_current(repository)
        current = repository.get_current_workspace("workspace-1")
    proposed = proposed_workspace or {**current, "title": "Changed Topic"}
    repository.save_pending_review(
        "workspace-1",
        review_id=review_id,
        agent_run_id="agent-run-1",
        base_workspace_version_hash=base_hash,
        user_message="Change title.",
        proposed_workspace=proposed,
        proposed_operations=[{"operation_type": "update_root_overview"}],
        diff_summary={"changed_top_level_fields": ["title"]},
        validation_summary={"valid": True},
        interrupt_payload={"type": "workspace_patch_review", "review_id": review_id},
    )
    return base_hash


def _workspace(branch_label: str = "Main Branch") -> dict[str, Any]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "workspace-1",
        "topic": "test topic",
        "title": "Test Topic",
        "scope": {
            "visible_paper_budget": {
                "target_min": 1,
                "target_max": 3,
                "hard_max_default": 4,
            }
        },
        "source_candidate_artifact": {},
        "root": {
            "node_id": "root",
            "label": "Test Topic",
            "overview": "Overview.",
            "root_survey_type": "generated_overview",
            "survey_anchor_paper_ids": [],
            "representative_paper_ids": ["p1"],
            "key_terms": [],
            "open_questions": [],
            "suggested_reading_direction": "Read the main branch.",
        },
        "tree": {
            "root_node_id": "root",
            "nodes": [_branch_node("branch-main", "root", branch_label)],
        },
        "paper_paths": [
            {
                "path_id": "path-main",
                "branch_node_id": "branch-main",
                "path_type": "primary_timeline",
                "label": "Main path",
                "description": "Tiny path.",
                "paper_ids": ["p1", "p2"],
                "rationale": "Fixture.",
            }
        ],
        "paper_cards": {
            "p1": _paper_card("p1", "Core Method", branch_label),
            "p2": _paper_card("p2", "Evaluation Benchmark", branch_label),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p1", "reason": "Start here."},
            {"order": 2, "paper_id": "p2", "reason": "Then evaluate."},
        ],
        "comparison_tables": [],
        "discarded_candidates": [],
        "provenance": {
            "workspace_constructor": "test",
            "model": "test",
            "prompt_version": "test",
            "created_at": "2026-07-05T00:00:00+00:00",
            "warnings": [],
        },
    }


def _branch_node(node_id: str, parent_id: str, label: str) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "label": label,
        "description": "Fixture branch.",
        "why_it_matters": "It matters.",
        "is_leaf": True,
        "child_node_ids": [],
        "primary_paper_ids": ["p1", "p2"],
        "secondary_paper_ids": [],
        "tags": [],
        "open_questions": [],
    }


def _paper_card(paper_id: str, title: str, branch_label: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": title,
        "authors": ["A. Author"],
        "year": 2024,
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "doi": None,
        "arxiv_id": None,
        "abstract": f"{title} abstract.",
        "primary_tree_location": {
            "node_id": "branch-main",
            "path": ["Test Topic", branch_label],
        },
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": "method",
        "one_sentence_contribution": "Fixture contribution.",
        "problem": "",
        "core_idea": "",
        "method": "",
        "assumptions": "",
        "datasets_or_benchmarks": "",
        "results": "",
        "limitations": "",
        "why_it_belongs": "Fixture.",
        "read_before": [],
        "read_after": [],
        "user_notes": "",
        "similar_papers": [],
    }
