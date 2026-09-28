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
from research_tree.services.errors import InvalidPayloadError, StaleWorkspaceError
from research_tree.services.pipeline import WorkspacePipelineService
from research_tree.services.topics import TopicReviewService
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.publishing import publish_workspace_version
from research_tree.workspace.repository import WorkspaceRepository


def test_health(client) -> None:

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_a_body_too_large_is_refused_as_such_without_a_declared_length(client) -> None:
    def chunks():
        for _ in range(11):
            yield b" " * 100_000

    response = client.post(
        "/workspaces/workspace-1/agent",
        content=chunks(),
        headers={"content-type": "application/json"},
    )

    assert "content-length" not in {name.lower() for name in response.request.headers}
    assert response.status_code == 413


def test_topic_review_approval_is_single_use(repository) -> None:
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


def test_paper_content_is_stored_outside_versioned_workspace_json(repository) -> None:
    _seed_current(repository)

    content_key = repository.save_paper_content(
        "workspace-1",
        "doi:10.1/example",
        {"status": "available", "full_text": "complete paper text"},
    )

    assert len(content_key) == 64
    assert repository.get_paper_content("workspace-1", "doi:10.1/example")["full_text"] == "complete paper text"
    assert "full_text" not in str(repository.get_current_workspace("workspace-1"))


def test_paper_content_endpoint_serves_the_stored_extract(client, repository) -> None:
    _seed_current(repository)
    repository.save_paper_content(
        "workspace-1",
        "doi:10.1/example",
        {
            "status": "available",
            "source_type": "open_access_pdf",
            "source_url": "https://arxiv.org/pdf/2201.11903",
            "page_count": 43,
            "figure_count": 2,
            "truncated": False,
            "full_text": "complete paper text",
        },
    )

    response = client.get(
        "/workspaces/workspace-1/paper-content",
        params={"paper_id": "doi:10.1/example"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["full_text"] == "complete paper text"
    assert payload["source_url"] == "https://arxiv.org/pdf/2201.11903"
    assert payload["page_count"] == 43
    assert payload["truncated"] is False


def test_paper_content_endpoint_answers_404_for_a_paper_with_no_extract(client, repository) -> None:
    _seed_current(repository)

    response = client.get(
        "/workspaces/workspace-1/paper-content",
        params={"paper_id": "doi:10.1/missing"},
    )

    assert response.status_code == 404


def test_workspace_current_versions_events_and_reviews(client, repository) -> None:
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


def test_list_workspaces_returns_repository_summaries(client, repository) -> None:
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
def test_topic_review_fails_closed_without_an_llm_key(client, repository) -> None:
    _seed_current(repository)

    response = client.post("/workspaces/topic-review", json={"topic": "  Test   Topic "})

    assert response.status_code == 200
    assert response.json()["can_create"] is False
    assert response.json()["existing_workspace"] is None
    assert response.json()["is_research_topic"] is False


@patch.dict(os.environ, {"OPENAI_API_KEY": "configured-for-test"})
def test_topic_review_uses_model_duplicate_selection(client, repository) -> None:
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
def test_topic_review_rejects_an_unreadable_paper_link(client) -> None:

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


@pytest.mark.parametrize(
    ("link", "identifier"),
    [
        ("https://arxiv.org/abs/1706.03762", "ARXIV:1706.03762"),
        ("https://arxiv.org/abs/1706.03762v5", "ARXIV:1706.03762"),
        ("https://arxiv.org/pdf/2211.17192v2.pdf", "ARXIV:2211.17192"),
        ("https://www.arxiv.org/abs/hep-th/9901001v3", "ARXIV:hep-th/9901001"),
        ("https://doi.org/10.18653/v1/N18-3011", "DOI:10.18653/v1/N18-3011"),
        (
            "https://www.semanticscholar.org/paper/Attention-is-All-you-Need-Vaswani-Shazeer/"
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        ),
        (
            "https://www.semanticscholar.org/paper/204e3073870fae3d05bcbc2f6a8e263d9b72e776",
            "204e3073870fae3d05bcbc2f6a8e263d9b72e776",
        ),
        ("https://arxiv.org/list/cs.CL/recent", None),
        ("https://example.com/abs/1706.03762", None),
        ("http://169.254.169.254/latest/meta-data", None),
    ],
)
def test_paper_links_are_read_the_way_people_copy_them(link: str, identifier: str | None) -> None:
    from research_tree.services.topics import _semantic_scholar_identifier_for_link

    assert _semantic_scholar_identifier_for_link(link) == identifier


@patch.dict(os.environ, {"OPENAI_API_KEY": "configured-for-test"})
def test_topic_review_says_when_the_link_could_not_be_looked_up(client) -> None:
    """A lookup Semantic Scholar turned away is not a link that names no paper."""

    from research_tree.retrieval.cache import JsonRequestError

    with patch(
        "research_tree.services.topics._linked_paper_metadata",
        side_effect=JsonRequestError("HTTP 429", transient=True, status=429),
    ), patch("research_tree.services.topics._review_with_model") as review_model:
        response = client.post(
            "/workspaces/topic-review",
            json={"topic": "https://arxiv.org/abs/1706.03762"},
        )

    assert response.status_code == 200
    assert response.json()["can_create"] is False
    assert "not answering right now" in response.json()["guidance"]
    review_model.assert_not_called()


def test_restore_and_delete_workspace_lifecycle(client, repository) -> None:
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


def test_hand_edit_publishes_a_version_the_reader_authored(client, repository) -> None:
    seed_hash = _seed_current(repository)

    response = client.post(
        "/workspaces/workspace-1/edits",
        json={
            "expected_version_hash": seed_hash,
            "operations": [
                {
                    "op": "set",
                    "entity_type": "branch",
                    "branch_id": "branch-main",
                    "field": "label",
                    "value": "Renamed Branch",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["changed"] is True
    assert body["previous_version_hash"] == seed_hash
    assert body["summary"] == "Renamed branch “Main Branch” to “Renamed Branch”"
    current = client.get("/workspaces/workspace-1").json()
    assert current["workspace_version_hash"] == body["workspace_version_hash"]
    assert current["workspace"]["tree"]["nodes"][0]["label"] == "Renamed Branch"
    [version] = [
        item
        for item in client.get("/workspaces/workspace-1/versions").json()["versions"]
        if item["version_hash"] == body["workspace_version_hash"]
    ]
    assert version["actor_type"] == "user"
    assert version["reason"] == body["summary"]
    events = client.get("/workspaces/workspace-1/events").json()["events"]
    [edited] = [event for event in events if event["event_type"] == "workspace_edited"]
    assert edited["target_ids"]["branch_ids"] == ["branch-main"]
    assert edited["payload"]["operation_types"] == ["rename_branch"]


def test_hand_edit_moves_a_paper_onto_the_other_branch(client, repository) -> None:
    seed_hash = repository.save_workspace_version(
        "workspace-1",
        _two_branch_workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )

    response = client.post(
        "/workspaces/workspace-1/edits",
        json={
            "expected_version_hash": seed_hash,
            "operations": [
                {
                    "op": "move",
                    "entity_type": "paper_placement",
                    "paper_id": "p1",
                    "to_branch_id": "branch-side",
                }
            ],
        },
    )

    assert response.status_code == 200, response.text
    assert response.json()["summary"] == "Moved “Core Method” to “Side Branch”"
    workspace = client.get("/workspaces/workspace-1").json()["workspace"]
    main, side = workspace["tree"]["nodes"]
    assert main["primary_paper_ids"] == ["p2"]
    assert side["primary_paper_ids"] == ["p3", "p1"]
    assert workspace["paper_paths"][1]["paper_ids"] == ["p3", "p1"]
    assert workspace["paper_cards"]["p1"]["primary_tree_location"]["node_id"] == "branch-side"


def test_hand_edit_removal_can_be_undone_by_restoring_the_previous_version(
    client, repository
) -> None:
    seed_hash = _seed_current(repository)

    edit = client.post(
        "/workspaces/workspace-1/edits",
        json={
            "expected_version_hash": seed_hash,
            "operations": [
                {"op": "remove", "entity_type": "paper_placement", "paper_id": "p2"}
            ],
        },
    )
    assert edit.status_code == 200, edit.text
    assert edit.json()["summary"] == "Removed “Evaluation Benchmark”"
    assert "p2" not in client.get("/workspaces/workspace-1").json()["workspace"]["paper_cards"]

    undo = client.post(
        f"/workspaces/workspace-1/versions/{edit.json()['previous_version_hash']}/restore",
        json={"expected_version_hash": edit.json()["workspace_version_hash"]},
    )

    assert undo.status_code == 200, undo.text
    assert undo.json()["workspace_version_hash"] == seed_hash
    assert "p2" in client.get("/workspaces/workspace-1").json()["workspace"]["paper_cards"]


def test_hand_edit_refuses_a_stale_hash_and_a_bad_operation(client, repository) -> None:
    seed_hash = _seed_current(repository)
    rename = {
        "op": "set",
        "entity_type": "branch",
        "branch_id": "branch-main",
        "field": "label",
        "value": "Renamed Branch",
    }

    stale = client.post(
        "/workspaces/workspace-1/edits",
        json={"expected_version_hash": "0" * 64, "operations": [rename]},
    )
    broken = client.post(
        "/workspaces/workspace-1/edits",
        json={
            "expected_version_hash": seed_hash,
            "operations": [
                {"op": "remove", "entity_type": "paper_placement", "paper_id": "missing"}
            ],
        },
    )

    assert stale.status_code == 409
    assert stale.json()["error_code"] == "review_conflict"
    assert broken.status_code == 400
    assert "visible paper does not exist: missing" in broken.json()["detail"]
    # Neither attempt published anything.
    assert client.get("/workspaces/workspace-1").json()["workspace_version_hash"] == seed_hash


def test_deleting_workspace_cancels_active_pipeline_and_blocks_late_publication(repository) -> None:
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
            repository=repository,
            workspace=stale_workspace,
            reason="late pipeline publication",
            event_type="workspace_pipeline_completed",
            event_payload={},
            pipeline_run_id="pipeline-active",
        )
    with pytest.raises(FileNotFoundError):
        repository.get_current_workspace("workspace-1")


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_chat_agent_request_returns_completed(client, repository) -> None:
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


def test_agent_request_passes_bounded_conversation_history(repository) -> None:
    _seed_current(repository)
    captured: dict[str, Any] = {}

    class RecordingGraph:
        def stream(
            self, graph_input: Any, *, config: dict[str, Any], stream_mode: str, durability: str
        ) -> list[dict[str, Any]]:
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
            "thread_id": "local_user:workspace-1:thread-1",
            "conversation_history": [
                {"role": "user", "text": "First question."},
                {"role": "assistant", "text": "First answer."},
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["thread_id"] == "local_user:workspace-1:thread-1"
    assert captured["graph_input"]["conversation_history"] == [
        {"role": "user", "text": "First question."},
        {"role": "assistant", "text": "First answer."},
    ]


def test_remove_paper_request_persists_a_review_without_calling_the_model(repository) -> None:
    """Paper removal is deterministic, but it still goes through the graph.

    The model declares `edit_kind: remove_papers` with explicit target ids on
    its tool call; message wording never routes a deletion, and no model
    writes the patch.
    """

    _seed_current(repository)

    def graph_factory(active_repository: WorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _edit_tool_turn(
                        "Remove Core Method paper.",
                        edit_kind="remove_papers",
                        target_paper_ids=["p1"],
                    )
                ]
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


def test_noop_workspace_modification_does_not_persist_review(repository) -> None:
    _seed_current(repository)

    def graph_factory(active_repository: WorkspaceRepository) -> Any:
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
def test_modify_agent_request_returns_pending_review_and_persists_review(client, repository) -> None:
    _seed_current(repository)

    response = client.post(
        "/workspaces/workspace-1/agent",
        json={"message": "Rename the branch."},
    )

    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "pending_review"
    assert payload["review_id"]
    # The offline fallback supplies message_to_user, so the reply is never the
    # "Assistant completed." placeholder.
    assert payload["final_response"] == (
        "I drafted a workspace change from your request for review."
    )
    review = repository.get_pending_review("workspace-1", payload["review_id"])
    assert review["status"] == "pending"
    assert review["interrupt_payload"]["review_id"] == payload["review_id"]
    # Keyless, the skeptic degrades to no objections rather than blocking.
    assert review["interrupt_payload"]["skeptic_notes"] == []


def test_get_persisted_review(client, repository) -> None:
    _save_pending_review(repository, review_id="review-1")

    response = client.get("/workspaces/workspace-1/reviews/review-1")

    assert response.status_code == 200
    assert response.json()["review"]["review_id"] == "review-1"


def test_approve_applies_current_workspace(client, repository) -> None:
    _save_pending_review(repository, review_id="review-approve")

    response = client.post("/workspaces/workspace-1/reviews/review-approve/approve")

    current = repository.get_current_workspace("workspace-1")
    payload = response.json()
    assert response.status_code == 200
    assert payload["status"] == "approved_applied"
    assert current["title"] == "Changed Topic"
    assert payload["workspace_version_hash"] == workspace_version_hash(current)


def test_reject_keeps_current_workspace_unchanged(client, repository) -> None:
    _save_pending_review(repository, review_id="review-reject")
    before = repository.get_current_workspace("workspace-1")

    response = client.post(
        "/workspaces/workspace-1/reviews/review-reject/reject",
        json={"reason": "not right"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"
    assert repository.get_current_workspace("workspace-1") == before


def test_double_approve_and_reject_are_idempotent(client, repository) -> None:
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


def test_terminal_review_transition_conflicts(client, repository) -> None:
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


def test_stale_approval_returns_conflict_and_preserves_current_workspace(client, repository) -> None:
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


def test_edit_review_revalidates_and_persists_new_pending_review(client, repository) -> None:
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


def test_edit_review_validation_failure_returns_failed_validation(client, repository) -> None:
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
    # An edit that cannot be applied leaves the proposal alone: the reader can
    # fix the edit, approve what the assistant proposed, or reject it.
    assert repository.get_review("workspace-1", "review-edit-invalid")["status"] == "pending"
    assert payload["persisted_event_ids"] == []


def test_edit_review_refuses_a_document_of_the_wrong_shape(client, repository) -> None:
    """A field of the wrong kind is a failed validation, never a 500."""

    _save_pending_review(repository, review_id="review-edit-shape")
    current = repository.get_current_workspace("workspace-1")
    for change in (
        {"paper_cards": 5},
        {"paper_paths": [{"path_id": "p2", "branch_node_id": [1]}]},
        {"reading_order": [{"paper_id": []}]},
        {"discarded_candidates": [{"paper_id": [1]}]},
        {"paper_cards": {**current["paper_cards"], "": {}}},
    ):
        response = client.post(
            "/workspaces/workspace-1/reviews/review-edit-shape/edit",
            json={"proposed_workspace": {**current, **change}},
        )
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "failed_validation"
    assert repository.get_review("workspace-1", "review-edit-shape")["status"] == "pending"


@patch.dict(os.environ, {"OPENAI_API_KEY": ""})
def test_guardrail_rejection_returns_failed_guardrail_and_starts_nothing(repository) -> None:
    _seed_current(repository)

    def graph_factory(active_repository: WorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
            llm_client=DeterministicWorkspaceAgentLlmClient(
                tool_turns=[
                    _rerun_tool_turn("Find more papers for this topic."),
                ]
            ),
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
        json={"message": "Find more papers for this topic."},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "failed_guardrail"
    # The rejected rerun leaves no review and no pipeline run behind.
    assert repository.list_workspace_reviews("workspace-1") == []
    assert repository.list_pipeline_runs("workspace-1") == []


def test_invalid_workspace_proposal_returns_failed_validation_status(repository) -> None:
    _seed_current(repository)

    def graph_factory(active_repository: WorkspaceRepository) -> Any:
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
def test_failed_pipeline_does_not_publish_a_partial_workspace(repository) -> None:
    service = WorkspacePipelineService(
        repository,
        repo_root=_temp_dir(),
        dispatch=lambda owner_id, run_id, execute: execute(run_id),
    )

    from research_tree.retrieval.cache import JsonRequestError

    with patch(
        "research_tree.services.pipeline.run_workspace_candidate_preparation_pipeline",
        side_effect=JsonRequestError("HTTP 503 from /data/cache/abc", transient=True, status=503),
    ):
        run = _start_approved(service, repository, "Failure-Safe RAG")

    saved_run = repository.get_pipeline_run(run["run_id"])
    assert saved_run["status"] == "failed"
    assert saved_run["stages"]["candidates"]["status"] == "failed"
    # The reader is told what happened; the exception's own text is the log's.
    assert saved_run["stages"]["candidates"]["error"].startswith("Semantic Scholar did not answer")
    assert "/data/cache" not in saved_run["error"]
    with pytest.raises(FileNotFoundError):
        repository.get_current_workspace(run["workspace_id"])


def test_an_account_may_only_hold_so_many_builds_at_once(repository) -> None:
    from research_tree.services.errors import RateLimitedError
    from research_tree.workspace.repository import MAX_ACTIVE_BUILDS_PER_ACCOUNT

    # Nothing executes the runs, so each one stays queued.
    service = WorkspacePipelineService(
        repository, repo_root=_temp_dir(), dispatch=lambda owner_id, run_id, execute: None
    )
    for number in range(MAX_ACTIVE_BUILDS_PER_ACCOUNT):
        _start_approved(service, repository, f"Held Topic {number}")

    with pytest.raises(RateLimitedError) as refused:
        _start_approved(service, repository, "One Topic Too Many")
    assert "builds in progress" in refused.value.message
    # The refused build kept neither a run nor the name it had claimed.
    assert len(repository.list_active_pipeline_runs()) == MAX_ACTIVE_BUILDS_PER_ACCOUNT

    # A finished build frees its place.
    held = repository.list_active_pipeline_runs()[0]
    repository.cancel_pipeline_runs(held["workspace_id"])
    _start_approved(service, repository, "One Topic Too Many")


def test_workspace_pipeline_always_uses_luna_for_construction(repository) -> None:
    service = WorkspacePipelineService(
        repository,
        repo_root=_temp_dir(),
        dispatch=lambda owner_id, run_id, execute: execute(run_id),
    )

    with patch(
        "research_tree.services.pipeline.run_workspace_candidate_preparation_pipeline",
        side_effect=RuntimeError("stop after run reservation"),
    ):
        run = _start_approved(service, repository, "Model-Locked RAG")

    saved_run = repository.get_pipeline_run(run["run_id"])
    assert saved_run["model"] == "gpt-5.6-luna"


def test_failed_partial_rerun_keeps_source_artifacts_unchanged(repository) -> None:
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
        dispatch=lambda owner_id, run_id, execute: execute(run_id),
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


@patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test-never-sent"})
def test_hydration_reads_done_only_once_its_version_is_current(repository) -> None:
    # A reader watching a build opens the workspace the moment hydration reads
    # as done, so by then the version it produced has to be the current one.
    seed_hash = _seed_current(repository)
    repository.save_pipeline_run(
        {
            "run_id": "pipeline_source",
            "workspace_id": "workspace-1",
            "status": "completed",
            "created_at": "2026-01-01T00:00:00+00:00",
            "artifacts": {},
        }
    )
    service = WorkspacePipelineService(
        repository,
        repo_root=_temp_dir(),
        dispatch=lambda owner_id, run_id, execute: execute(run_id),
    )
    current_when_done: list[str] = []
    save_run = repository.save_pipeline_run

    def record_current_version(run: dict[str, Any]) -> None:
        hydrate = run["stages"].get("hydrate") or {}
        if str(hydrate.get("status", "")).startswith("completed"):
            current = repository.get_current_workspace("workspace-1")
            current_when_done.append(workspace_version_hash(current))
        save_run(run)

    with (
        patch.object(repository, "save_pipeline_run", side_effect=record_current_version),
        patch(
            "research_tree.services.pipeline.hydrate_workspace_papers",
            side_effect=lambda *, workspace, **_: (workspace, []),
        ),
    ):
        service.rerun("workspace-1", start_stage="hydrate", expected_version_hash=seed_hash)

    assert current_when_done, "hydration never read as done"
    assert current_when_done[0] != seed_hash


def test_unpublished_paper_content_does_not_block_workspace_retry_id(repository) -> None:
    # Failed hydration can leave cached paper content without ever publishing
    # a workspace. Only a claimed name reserves it.
    repository.save_paper_content(
        "retrieval-augmented-generation", "paper-1", {"status": "available", "full_text": "x"}
    )

    assert repository.claim_workspace_id("retrieval-augmented-generation") == (
        "retrieval-augmented-generation"
    )
    _seed_current(repository)
    assert repository.claim_workspace_id("workspace-1") == "workspace-1-2"


def test_pipeline_rerun_rejects_another_active_run_for_the_workspace(repository) -> None:
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


def test_pipeline_rerun_refuses_a_stale_hash_as_a_conflict(repository) -> None:
    _seed_current(repository)
    service = WorkspacePipelineService(repository, repo_root=_temp_dir())

    with pytest.raises(StaleWorkspaceError):
        service.rerun("workspace-1", start_stage="candidates", expected_version_hash="0" * 64)


def test_a_refused_build_hands_its_name_back(repository) -> None:
    """A name claimed for a build that is refused is free for the next build of it."""

    _seed_current(repository)
    service = WorkspacePipelineService(
        repository, repo_root=_temp_dir(), dispatch=lambda owner_id, run_id, execute: None
    )

    # The seeded workspace already covers this topic, so the build is refused
    # after "test-topic" was claimed for it.
    with pytest.raises(InvalidPayloadError, match="already exists"):
        _start_approved(service, repository, "test topic")

    assert repository.claim_workspace_id("test-topic") == "test-topic"


@pytest.mark.json_only
def test_dead_pipeline_owner_is_reclaimed_before_a_new_run_is_reserved(repository) -> None:
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


@pytest.mark.json_only
def test_pipeline_run_from_another_host_is_never_reclaimed(repository) -> None:
    """A PID probe only means anything on the machine that owns the PID.

    On a shared volume, a run written by another host must be left alone even
    when its PID happens to be dead (or alive) here — otherwise a PID
    collision fails a healthy run.
    """

    repository.save_pipeline_run(
        {
            "run_id": "pipeline_remote",
            "workspace_id": "workspace-1",
            "status": "running",
            "runner_pid": 12345,
            "runner_host": "some-other-container",
            "created_at": "2026-01-01T00:00:00+00:00",
        }
    )

    with patch(
        "research_tree.workspace.repository.os.kill",
        side_effect=ProcessLookupError,
    ):
        run = repository.get_pipeline_run("pipeline_remote")

    assert run["status"] == "running"


def test_a_mistyped_paper_id_is_handed_back_to_the_model_not_to_the_reader(repository) -> None:
    """A removal is all or nothing, and one garbled id used to end the whole turn."""

    _seed_current(repository)
    seen_inputs: list[list[dict[str, Any]]] = []

    class CorrectingClient(DeterministicWorkspaceAgentLlmClient):
        def complete_with_tools(self, *, input_items, **kwargs):  # type: ignore[override]
            seen_inputs.append(list(input_items))
            return super().complete_with_tools(input_items=input_items, **kwargs)

    def removal(call_id: str, paper_ids: list[str]) -> AgentTurn:
        turn = _edit_tool_turn("Remove it.", edit_kind="remove_papers", target_paper_ids=paper_ids)
        turn.output_items[0]["call_id"] = call_id
        return AgentTurn(
            output_items=turn.output_items,
            tool_calls=[ToolCall(call_id=call_id, name="propose_workspace_edit", arguments=turn.tool_calls[0].arguments)],
            output_text=None,
        )

    service = WorkspaceAgentService(
        repository,
        graph_factory=lambda active: build_workspace_agent_graph(
            llm_client=CorrectingClient(tool_turns=[removal("call_1", ["p11"]), removal("call_2", ["p1"])]),
            workspace_constructor=_unexpected_constructor,
            workspace_repository=active,
        ),
    )

    result = service.run_agent("workspace-1", message="Remove the core method paper.")

    assert result["status"] == "pending_review", result
    # The second request carried the first call's answer, naming the wrong id
    # and the visible one it resembles.
    answers = [item for item in seen_inputs[1] if item.get("type") == "function_call_output"]
    assert len(answers) == 1 and answers[0]["call_id"] == "call_1"
    told = json.loads(answers[0]["output"])
    assert told["unknown_ids"] == ["p11"]
    assert told["closest_visible_ids"] == {"p11": ["p1"]}


def _edit_tool_turn(instruction: str, **extra: Any) -> AgentTurn:
    """One model turn that asks for a workspace edit."""

    arguments = {
        "instruction": instruction,
        "edit_kind": "structural",
        "message_to_user": "I drafted this change for your review.",
        **extra,
    }
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

    arguments = {
        "stage": "candidates",
        "reason": reason,
        "message_to_user": "I want to rerun candidate retrieval.",
    }
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
    repository: WorkspaceRepository,
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


def test_a_conversation_keeps_one_checkpoint_however_long_it_runs(repository) -> None:
    """Each turn used to leave its whole state behind; the thread only ever resumes from the newest."""

    from psycopg.rows import dict_row
    from psycopg_pool import ConnectionPool

    from research_tree.agents.workspace.checkpoints import PrunedPostgresSaver
    from research_tree.db import plain_postgres_dsn
    from research_tree.workspace.repository import LocalJsonWorkspaceRepository

    if isinstance(repository, LocalJsonWorkspaceRepository):
        pytest.skip("the pruned saver is the Postgres one")
    _seed_current(repository)
    pool = ConnectionPool(
        plain_postgres_dsn(os.environ["RESEARCH_TREE_TEST_DATABASE_URL"]),
        min_size=1,
        max_size=2,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
        open=True,
    )
    try:
        saver = PrunedPostgresSaver(pool)
        service = WorkspaceAgentService(
            repository,
            checkpointer=saver,
            graph_factory=lambda active: build_workspace_agent_graph(
                llm_client=DeterministicWorkspaceAgentLlmClient(text_outputs=["One.", "Two.", "Three."]),
                workspace_repository=active,
                checkpointer=saver,
            ),
        )
        first = service.run_agent("workspace-1", message="What is here?")
        thread_id = first["thread_id"]
        for message in ("And then?", "And after that?"):
            answered = service.run_agent("workspace-1", message=message, thread_id=thread_id)
            assert answered["status"] == "completed"

        with pool.connection() as conn:
            checkpoints = conn.execute(
                "SELECT count(*) AS n FROM checkpoints WHERE thread_id = %s", (thread_id,)
            ).fetchone()["n"]
            stray_blobs = conn.execute(
                """
                SELECT count(*) AS n FROM checkpoint_blobs blobs
                WHERE blobs.thread_id = %s AND NOT EXISTS (
                    SELECT 1 FROM checkpoints kept,
                         jsonb_each_text(kept.checkpoint -> 'channel_versions') AS named
                    WHERE kept.thread_id = blobs.thread_id
                      AND named.key = blobs.channel AND named.value = blobs.version)
                """,
                (thread_id,),
            ).fetchone()["n"]
            kept_blobs = conn.execute(
                "SELECT count(*) AS n FROM checkpoint_blobs WHERE thread_id = %s", (thread_id,)
            ).fetchone()["n"]
        assert checkpoints == 1
        assert stray_blobs == 0
        assert kept_blobs > 0
        # What is left is a whole state: the thread still resumes.
        state = saver.get_tuple({"configurable": {"thread_id": thread_id}})
        assert state is not None and state.checkpoint["channel_values"]["workspace_id"] == "workspace-1"

        # Deleting the workspace takes its conversations, and only its own.
        neighbour = f"{repository.owner_id}:workspace-1:b:{'0' * 12}"
        with pool.connection() as conn:
            conn.execute(
                "INSERT INTO checkpoints (thread_id, checkpoint_id, checkpoint) VALUES (%s, 'c1', '{}')",
                (neighbour,),
            )
        service.forget_conversations("workspace-1")
        with pool.connection() as conn:
            left = [
                row["thread_id"]
                for row in conn.execute("SELECT thread_id FROM checkpoints").fetchall()
            ]
            blobs_left = conn.execute("SELECT count(*) AS n FROM checkpoint_blobs").fetchone()["n"]
        assert left == [neighbour]
        assert blobs_left == 0
    finally:
        with pool.connection() as conn:
            for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
                conn.execute(f"DELETE FROM {table}")
        pool.close()


def _seed_current(repository: WorkspaceRepository) -> str:
    return repository.save_workspace_version(
        "workspace-1",
        _workspace(),
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )


def _save_pending_review(
    repository: WorkspaceRepository,
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


def _two_branch_workspace() -> dict[str, Any]:
    workspace = _workspace()
    side = _branch_node("branch-side", "root", "Side Branch")
    side["primary_paper_ids"] = ["p3"]
    workspace["tree"]["nodes"].append(side)
    workspace["paper_cards"]["p3"] = _paper_card("p3", "Side Paper", "Side Branch")
    workspace["paper_cards"]["p3"]["primary_tree_location"]["node_id"] = "branch-side"
    workspace["paper_paths"].append(
        {
            "path_id": "path-side",
            "branch_node_id": "branch-side",
            "path_type": "primary_timeline",
            "label": "Side path",
            "description": "The other line.",
            "paper_ids": ["p3"],
            "rationale": "Fixture.",
        }
    )
    workspace["reading_order"].append({"order": 3, "paper_id": "p3", "reason": "Last."})
    return workspace


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
