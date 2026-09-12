from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pytest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.workspace.context import atomic_branch_count, workspace_version_hash
from research_tree.workspace.repository import LocalJsonWorkspaceRepository, WorkspaceRepository


class AtomicBranchCountTest(unittest.TestCase):
    """A parent branch groups its children; counting both counts the same work twice."""

    def test_counts_only_leaves(self) -> None:
        nodes = [
            {"node_id": "a", "parent_id": "root", "is_leaf": False},
            {"node_id": "a1", "parent_id": "a", "is_leaf": True},
            {"node_id": "a2", "parent_id": "a", "is_leaf": True},
            {"node_id": "b", "parent_id": "root", "is_leaf": True},
        ]

        self.assertEqual(atomic_branch_count(nodes), 3)

    def test_falls_back_to_the_tree_when_is_leaf_is_absent(self) -> None:
        nodes = [
            {"node_id": "a", "parent_id": "root"},
            {"node_id": "a1", "parent_id": "a"},
        ]

        self.assertEqual(atomic_branch_count(nodes), 1)

    def test_tolerates_a_missing_or_malformed_tree(self) -> None:
        self.assertEqual(atomic_branch_count(None), 0)
        self.assertEqual(atomic_branch_count([{"node_id": "a"}, "not-a-node"]), 1)



def test_new_change_after_restore_truncates_forward_navigation(repository) -> None:
    first = _workspace()
    first_hash = repository.save_workspace_version(
        "workspace-1",
        first,
        actor="system",
        parent_version_hash=None,
        reason="initial",
    )
    second_hash = repository.save_workspace_version(
        "workspace-1",
        {**first, "title": "Second"},
        actor="user",
        parent_version_hash=first_hash,
        reason="second",
    )
    repository.restore_workspace_version(
        "workspace-1",
        first_hash,
        actor="user",
        reason="go back",
    )

    before_edit = repository.list_workspace_versions("workspace-1")
    third_hash = repository.save_workspace_version(
        "workspace-1",
        {**first, "title": "New branch"},
        actor="user",
        parent_version_hash=first_hash,
        reason="edit after restore",
    )
    after_edit = repository.list_workspace_versions("workspace-1")

    assert [item["version_hash"] for item in before_edit] == [first_hash, second_hash]
    assert [item["version_hash"] for item in after_edit] == [first_hash, third_hash]
    assert (after_edit[-1]["is_current"])


@pytest.mark.json_only
def test_delete_workspace_moves_it_out_of_active_repository(repository, data_dir) -> None:
    version_hash = _seed_current(repository)

    result = repository.delete_workspace(
        "workspace-1",
        expected_version_hash=version_hash,
    )

    assert result["deleted"]
    assert repository.list_workspaces() == []
    assert len(list((data_dir / ".trash").iterdir())) == 1



def test_deleted_workspace_is_hidden_and_keeps_its_id(repository) -> None:
    """Deletion hides every read; the id is not handed to the next build."""

    version_hash = _seed_current(repository)
    repository.save_pending_review(
        "workspace-1",
        review_id="review-1",
        agent_run_id="agent-run-1",
        base_workspace_version_hash=version_hash,
        user_message="Change title.",
        proposed_workspace={**_workspace(), "title": "Changed"},
        proposed_operations=[],
        diff_summary={},
        validation_summary={"valid": True},
        interrupt_payload={},
    )

    with pytest.raises(ValueError, match="changed before deletion"):
        repository.delete_workspace("workspace-1", expected_version_hash="0" * 64)
    result = repository.delete_workspace("workspace-1", expected_version_hash=version_hash)

    assert result["deleted"]
    assert repository.list_workspaces() == []
    with pytest.raises(FileNotFoundError):
        repository.get_current_workspace("workspace-1")
    with pytest.raises(FileNotFoundError):
        repository.get_workspace_version("workspace-1", version_hash)
    assert repository.list_workspace_versions("workspace-1") == []
    with pytest.raises(FileNotFoundError):
        repository.approve_review_once("workspace-1", "review-1")
    # The name stays taken in both stores: a rebuild gets the next suffix
    # rather than the deleted workspace's runs and history.
    assert repository.claim_workspace_id("workspace-1") == "workspace-1-2"



def test_stale_publish_is_rejected(repository) -> None:
    """Two writers publishing against the same parent: the second one loses."""

    first_hash = _seed_current(repository)
    repository.save_workspace_version(
        "workspace-1",
        {**_workspace(), "title": "Second"},
        actor="user",
        parent_version_hash=first_hash,
        reason="second",
        expected_version_hash=first_hash,
    )

    with pytest.raises(RuntimeError, match="changed before publish"):
        repository.save_workspace_version(
            "workspace-1",
            {**_workspace(), "title": "Third"},
            actor="user",
            parent_version_hash=first_hash,
            reason="stale",
            expected_version_hash=first_hash,
        )

    assert repository.get_current_workspace("workspace-1")["title"] == "Second"


def test_new_workspace_pipeline_topic_reservation_is_atomic(repository) -> None:
    first = {
        "run_id": "pipeline-1",
        "topic": "RAG Evaluation",
        "status": "queued",
    }
    repository.reserve_new_workspace_run(first)

    with pytest.raises(ValueError, match="already being built"):
        repository.reserve_new_workspace_run(
            {
                "run_id": "pipeline-2",
                "topic": " rag   evaluation ",
                "status": "queued",
            }
        )


def test_saves_current_version_metadata_and_events(repository) -> None:
    workspace = _workspace()
    version_hash = repository.save_workspace_version(
        "workspace-1",
        workspace,
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )
    event_id = repository.append_workspace_event(
        "workspace-1",
        actor="system",
        event_type="workspace_seeded",
        target_ids={},
        before_hash=None,
        after_hash=version_hash,
        payload={"reason": "test"},
    )

    current = repository.get_current_workspace("workspace-1")
    version = repository.get_workspace_version("workspace-1", version_hash)
    metadata = repository.list_workspace_versions("workspace-1")[0]
    events = repository.list_workspace_events("workspace-1")

    assert version_hash == workspace_version_hash(workspace)
    assert current == workspace
    assert version == workspace
    assert metadata["schema_version"] == "research_tree.workspace_version_metadata.v1"
    assert metadata["actor"] == "system"
    assert metadata["actor_type"] == "system"
    assert metadata["actor_id"] == "workspace_agent"
    assert metadata["reason"] == "seed fixture"
    assert events[0]["schema_version"] == "research_tree.workspace_event.v1"
    assert events[0]["actor_type"] == "system"
    assert events[0]["actor_id"] == "workspace_agent"
    assert events[0]["event_id"] == event_id
    assert events[0]["event_type"] == "workspace_seeded"


def test_get_current_workspace_loads_saved_snapshot(repository) -> None:
    workspace = _workspace()
    repository.save_workspace_version(
        "workspace-1",
        workspace,
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )

    loaded = repository.get_current_workspace("workspace-1")

    assert loaded == workspace


def test_restore_moves_current_to_an_immutable_previous_snapshot(repository) -> None:
    first = _workspace()
    first_hash = repository.save_workspace_version(
        "workspace-1",
        first,
        actor="system",
        parent_version_hash=None,
        reason="initial construction",
    )
    second = {**first, "title": "Rebuilt Topic"}
    second_hash = repository.save_workspace_version(
        "workspace-1",
        second,
        actor="system",
        parent_version_hash=first_hash,
        reason="reconstructed from the same candidate artifact",
    )

    result = repository.restore_workspace_version(
        "workspace-1",
        first_hash,
        actor="user",
        actor_type="user",
        reason="rejected reconstructed workspace",
    )
    current = repository.get_current_workspace("workspace-1")
    versions = {
        version["version_hash"]: version
        for version in repository.list_workspace_versions("workspace-1")
    }
    events = repository.list_workspace_events("workspace-1")

    assert (result["restored"])
    assert result["before_hash"] == second_hash
    assert current == first
    assert (versions[first_hash]["is_current"])
    assert not (versions[second_hash]["is_current"])
    assert versions[second_hash]["parent_version_hash"] == first_hash
    assert _count_events(events, "workspace_restored") == 1


def test_saves_pending_review_and_marks_approved(repository) -> None:
    workspace = _workspace()
    base_hash = repository.save_workspace_version(
        "workspace-1",
        workspace,
        actor="system",
        parent_version_hash=None,
        reason="seed fixture",
    )
    proposed = {**workspace, "title": "Changed"}

    review_id = repository.save_pending_review(
        "workspace-1",
        review_id="review-1",
        agent_run_id="agent-run-1",
        base_workspace_version_hash=base_hash,
        user_message="Change title.",
        proposed_workspace=proposed,
        proposed_operations=[{"operation_type": "update_root_overview"}],
        diff_summary={"changed_top_level_fields": ["title"]},
        validation_summary={"valid": True},
        interrupt_payload={"type": "workspace_patch_review"},
    )
    pending = repository.get_pending_review("workspace-1", review_id)
    repository.mark_review_approved(
        "workspace-1",
        review_id,
        applied_workspace_version_hash=workspace_version_hash(proposed),
        event_id="evt-1",
    )
    approved = repository.get_review("workspace-1", review_id)

    assert review_id == "review-1"
    assert pending["status"] == "pending"
    assert pending["base_workspace_version_hash"] == base_hash
    assert pending["proposed_workspace_version_hash"] == workspace_version_hash(proposed)
    assert approved["schema_version"] == "research_tree.workspace_review.v1"
    assert approved["status"] == "approved_applied"
    assert approved["approval_event_id"] == "evt-1"
    assert approved["actor_type"] == "user"
    assert approved["actor_id"] == "local_user"


def test_marks_reviews_rejected_and_edited(repository) -> None:
    workspace = _workspace()
    base_hash = workspace_version_hash(workspace)
    edited = {**workspace, "title": "Edited"}
    for review_id in ("review-reject", "review-edit"):
        repository.save_pending_review(
            "workspace-1",
            review_id=review_id,
            agent_run_id="agent-run-1",
            base_workspace_version_hash=base_hash,
            user_message="Change title.",
            proposed_workspace=workspace,
            proposed_operations=[],
            diff_summary={},
            validation_summary={"valid": True},
            interrupt_payload={"type": "workspace_patch_review"},
        )

    repository.mark_review_rejected(
        "workspace-1",
        "review-reject",
        event_id="evt-reject",
        reason="user rejected",
    )
    repository.mark_review_edited(
        "workspace-1",
        "review-edit",
        edited_workspace=edited,
        event_id="evt-edit",
    )
    rejected = repository.get_review("workspace-1", "review-reject")
    edited_review = repository.get_review("workspace-1", "review-edit")

    assert rejected["status"] == "rejected"
    assert rejected["rejection_event_id"] == "evt-reject"
    assert edited_review["status"] == "edited"
    assert edited_review["edit_event_id"] == "evt-edit"
    assert edited_review["edited_workspace_version_hash"] == workspace_version_hash(edited)
    assert edited_review["edited_workspace"] == edited


def test_pending_review_loads_after_repository_reload(repository, reopen_repository) -> None:
    base_hash = _seed_current(repository)
    proposed = {**_workspace(), "title": "Changed"}
    repository.save_pending_review(
        "workspace-1",
        review_id="review-1",
        agent_run_id="agent-run-1",
        base_workspace_version_hash=base_hash,
        user_message="Change title.",
        proposed_workspace=proposed,
        proposed_operations=[],
        diff_summary={},
        validation_summary={"valid": True},
        interrupt_payload={"type": "workspace_patch_review"},
    )

    reloaded = reopen_repository()
    review = reloaded.get_pending_review("workspace-1", "review-1")

    assert review["status"] == "pending"
    assert review["proposed_workspace"] == proposed


def test_approve_review_after_repository_reload(repository, reopen_repository) -> None:
    _save_pending_review(repository, review_id="review-approve")
    reloaded = reopen_repository()

    result = reloaded.approve_review_once("workspace-1", "review-approve")
    current = reloaded.get_current_workspace("workspace-1")
    review = reloaded.get_review("workspace-1", "review-approve")
    events = reloaded.list_workspace_events("workspace-1")

    assert (result["ok"])
    assert review["status"] == "approved_applied"
    assert current["title"] == "Changed"
    assert _count_events(events, "workspace_patch_approved_applied") == 1


def test_reject_review_after_repository_reload_keeps_current_unchanged(repository, reopen_repository) -> None:
    _save_pending_review(repository, review_id="review-reject")
    reloaded = reopen_repository()

    result = reloaded.reject_review_once("workspace-1", "review-reject")
    current = reloaded.get_current_workspace("workspace-1")
    review = reloaded.get_review("workspace-1", "review-reject")
    events = reloaded.list_workspace_events("workspace-1")

    assert (result["ok"])
    assert review["status"] == "rejected"
    assert current["title"] == "Test Topic"
    assert _count_events(events, "workspace_patch_rejected") == 1


def test_edit_review_after_repository_reload(repository, reopen_repository) -> None:
    _save_pending_review(repository, review_id="review-edit")
    reloaded = reopen_repository()
    edited = {**_workspace(), "title": "Edited"}

    result = reloaded.edit_review_once(
        "workspace-1",
        "review-edit",
        edited_workspace=edited,
    )
    review = reloaded.get_review("workspace-1", "review-edit")
    events = reloaded.list_workspace_events("workspace-1")

    assert (result["ok"])
    assert review["status"] == "edited"
    assert review["edited_workspace"] == edited
    assert _count_events(events, "workspace_patch_edited") == 1


def test_stale_approval_after_repository_reload_fails_and_keeps_current(repository, reopen_repository) -> None:
    base_hash = _save_pending_review(repository, review_id="review-stale")
    repository.save_workspace_version(
        "workspace-1",
        {**_workspace(), "title": "User Changed"},
        actor="user",
        parent_version_hash=base_hash,
        reason="manual edit",
    )
    reloaded = reopen_repository()

    result = reloaded.approve_review_once("workspace-1", "review-stale")
    current = reloaded.get_current_workspace("workspace-1")
    review = reloaded.get_review("workspace-1", "review-stale")
    events = reloaded.list_workspace_events("workspace-1")

    assert not (result["ok"])
    assert result["status"] == "failed_stale_base"
    assert review["status"] == "failed_stale_base"
    assert current["title"] == "User Changed"
    assert _count_events(events, "workspace_patch_stale_approval_rejected") == 1


def test_approve_same_review_twice_is_idempotent(repository) -> None:
    _save_pending_review(repository, review_id="review-approve")

    first = repository.approve_review_once("workspace-1", "review-approve")
    second = repository.approve_review_once("workspace-1", "review-approve")
    events = repository.list_workspace_events("workspace-1")
    versions = repository.list_workspace_versions("workspace-1")

    assert (first["ok"])
    assert not (first["idempotent"])
    assert (second["ok"])
    assert (second["idempotent"])
    assert _count_events(events, "workspace_patch_approved_applied") == 1
    assert len(versions) == 2


def test_reject_same_review_twice_is_idempotent(repository) -> None:
    _save_pending_review(repository, review_id="review-reject")

    first = repository.reject_review_once("workspace-1", "review-reject")
    second = repository.reject_review_once("workspace-1", "review-reject")
    events = repository.list_workspace_events("workspace-1")

    assert (first["ok"])
    assert not (first["idempotent"])
    assert (second["ok"])
    assert (second["idempotent"])
    assert _count_events(events, "workspace_patch_rejected") == 1


def test_terminal_review_actions_fail_cleanly(repository) -> None:
    _save_pending_review(repository, review_id="review-rejected")
    repository.reject_review_once("workspace-1", "review-rejected")
    approve_after_reject = repository.approve_review_once(
        "workspace-1",
        "review-rejected",
    )
    edit_after_reject = repository.edit_review_once(
        "workspace-1",
        "review-rejected",
        edited_workspace={**_workspace(), "title": "Edited"},
    )

    _save_pending_review(repository, review_id="review-approved")
    repository.approve_review_once("workspace-1", "review-approved")
    reject_after_approve = repository.reject_review_once(
        "workspace-1",
        "review-approved",
    )
    edit_after_approve = repository.edit_review_once(
        "workspace-1",
        "review-approved",
        edited_workspace={**_workspace(), "title": "Edited"},
    )
    events = repository.list_workspace_events("workspace-1")

    assert not (approve_after_reject["ok"])
    assert not (edit_after_reject["ok"])
    assert not (reject_after_approve["ok"])
    assert not (edit_after_approve["ok"])
    assert _count_events(events, "workspace_patch_rejected") == 1
    assert _count_events(events, "workspace_patch_approved_applied") == 1
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
) -> str:
    base_hash = _seed_current(repository)
    proposed = {**_workspace(), "title": "Changed"}
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
        interrupt_payload={"type": "workspace_patch_review"},
    )
    return base_hash


def test_a_cancelled_run_stays_cancelled(repository: WorkspaceRepository) -> None:
    """The executor holds the run for a whole stage and writes it back afterwards."""

    run = {
        "schema_version": "research_tree.pipeline_run.v2",
        "run_id": "pipeline_1",
        "workspace_id": "workspace-1",
        "topic": "Prompting",
        "status": "running",
        "requested_stages": ["candidates"],
        "stages": {},
    }
    repository.save_pipeline_run(run)
    repository.cancel_pipeline_runs("workspace-1")

    repository.save_pipeline_run({**run, "status": "running"})
    assert repository.get_pipeline_run("pipeline_1")["status"] == "cancelled"

    repository.save_pipeline_run({**run, "status": "completed"})
    assert repository.get_pipeline_run("pipeline_1")["status"] == "cancelled"


def test_a_topic_in_any_script_can_start_a_build(repository: WorkspaceRepository) -> None:
    """An ASCII-only topic key refused every reader who wrote in their own language."""

    for index, topic in enumerate(("Prompting", "\u673a\u5668\u5b66\u4e60", "\u041e\u0431\u0443\u0447\u0435\u043d\u0438\u0435")):
        repository.reserve_new_workspace_run(
            {
                "schema_version": "research_tree.pipeline_run.v2",
                "run_id": f"pipeline_{index}",
                "workspace_id": f"workspace-{index}",
                "topic": topic,
                "status": "queued",
                "requested_stages": ["candidates"],
            }
        )


def test_saving_a_document_already_in_history_moves_to_it(repository: WorkspaceRepository) -> None:
    """An edit and an edit back is one version, not two entries under one reason."""

    first = _workspace()
    first["topic"] = "Prompting"
    changed = _workspace()
    changed["topic"] = "Prompting"
    changed["title"] = "Renamed"

    original = repository.save_workspace_version(
        "workspace-1", first, actor="system", parent_version_hash=None, reason="built"
    )
    edited = repository.save_workspace_version(
        "workspace-1", changed, actor="user", parent_version_hash=original, reason="renamed it"
    )
    reverted = repository.save_workspace_version(
        "workspace-1", first, actor="user", parent_version_hash=edited, reason="renamed it back"
    )

    assert reverted == original
    versions = repository.list_workspace_versions("workspace-1")
    assert [version["version_hash"] for version in versions] == [original, edited]
    assert [version["is_current"] for version in versions] == [True, False]


def test_two_workspaces_on_one_topic_are_both_listed(repository: WorkspaceRepository) -> None:
    """Renaming a topic used to remove a workspace from the only list that opens one."""

    for workspace_id in ("alpha", "beta"):
        document = _workspace()
        document["workspace_id"] = workspace_id
        document["topic"] = "Prompting"
        document["title"] = "Prompting"
        repository.save_workspace_version(
            workspace_id, document, actor="system", parent_version_hash=None, reason="built"
        )

    listed = {summary["workspace_id"] for summary in repository.list_workspaces()}

    assert listed == {"alpha", "beta"}


def _count_events(events: list[dict[str, object]], event_type: str) -> int:
    return sum(1 for event in events if event.get("event_type") == event_type)


def _workspace() -> dict[str, object]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "workspace-1",
        "topic": "test topic",
        "title": "Test Topic",
        "scope": {},
        "source_candidate_artifact": {},
        "root": {},
        "tree": {},
        "paper_paths": [],
        "paper_cards": {},
        "reading_order": [],
        "comparison_tables": [],
        "discarded_candidates": [],
        "provenance": {},
    }


if __name__ == "__main__":
    unittest.main()
