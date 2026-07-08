from __future__ import annotations

import inspect
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from research_tree.api.app import create_app
from research_tree.api.dependencies import (
    get_repository,
    get_workspace_agent_service,
)
from research_tree.api.routes import agent, health, reviews, workspaces
from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.services.agent import WorkspaceAgentService
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


def test_health() -> None:
    client, _repository = _client_with_repository()

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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
                "paper_count": 1,
                "branch_count": 1,
                "paper_path_count": 1,
                "updated_at": "2026-07-05T00:00:00+00:00",
            }
        ]
    }


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


def test_guardrail_rejection_returns_failed_guardrail_and_skips_retrieval_runner() -> None:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    _seed_current(repository)
    retrieval_calls: list[object] = []

    def graph_factory(active_repository: LocalJsonWorkspaceRepository) -> Any:
        return build_workspace_agent_graph(
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


def _client_with_repository() -> tuple[TestClient, LocalJsonWorkspaceRepository]:
    repository = LocalJsonWorkspaceRepository(_temp_dir())
    app = create_app()
    app.dependency_overrides[get_repository] = lambda: repository
    return TestClient(app), repository


def _temp_dir() -> Path:
    import tempfile

    return Path(tempfile.mkdtemp())


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
                "paper_ids": ["p1"],
                "rationale": "Fixture.",
            }
        ],
        "paper_cards": {
            "p1": _paper_card("p1", "Core Method", branch_label),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p1", "reason": "Start here."},
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
        "primary_paper_ids": ["p1"],
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
