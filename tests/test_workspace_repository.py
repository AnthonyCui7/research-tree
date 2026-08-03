from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import LocalJsonWorkspaceRepository


class LocalJsonWorkspaceRepositoryTest(unittest.TestCase):
    def test_new_change_after_restore_truncates_forward_navigation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

        self.assertEqual(
            [item["version_hash"] for item in before_edit],
            [first_hash, second_hash],
        )
        self.assertEqual(
            [item["version_hash"] for item in after_edit],
            [first_hash, third_hash],
        )
        self.assertTrue(after_edit[-1]["is_current"])

    def test_delete_workspace_moves_it_out_of_active_repository(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            version_hash = _seed_current(repository)

            result = repository.delete_workspace(
                "workspace-1",
                expected_version_hash=version_hash,
            )

            self.assertTrue(result["deleted"])
            self.assertEqual(repository.list_workspaces(), [])
            self.assertEqual(len(list((Path(directory) / ".trash").iterdir())), 1)

    def test_new_workspace_pipeline_topic_reservation_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            first = {
                "run_id": "pipeline-1",
                "topic": "RAG Evaluation",
                "status": "queued",
            }
            repository.reserve_new_workspace_run(first)

            with self.assertRaisesRegex(ValueError, "already being built"):
                repository.reserve_new_workspace_run(
                    {
                        "run_id": "pipeline-2",
                        "topic": " rag   evaluation ",
                        "status": "queued",
                    }
                )

    def test_saves_current_version_metadata_and_events(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

            workspace_dir = Path(directory) / "workspace-1"
            current = json.loads((workspace_dir / "current.json").read_text())
            version = json.loads(
                (workspace_dir / "versions" / f"{version_hash}.json").read_text()
            )
            metadata = repository.list_workspace_versions("workspace-1")[0]
            events = repository.list_workspace_events("workspace-1")

        self.assertEqual(version_hash, workspace_version_hash(workspace))
        self.assertEqual(current, workspace)
        self.assertEqual(version, workspace)
        self.assertEqual(
            metadata["schema_version"],
            "research_tree.workspace_version_metadata.v1",
        )
        self.assertEqual(metadata["actor"], "system")
        self.assertEqual(metadata["actor_type"], "system")
        self.assertEqual(metadata["actor_id"], "workspace_agent")
        self.assertEqual(metadata["reason"], "seed fixture")
        self.assertEqual(events[0]["schema_version"], "research_tree.workspace_event.v1")
        self.assertEqual(events[0]["actor_type"], "system")
        self.assertEqual(events[0]["actor_id"], "workspace_agent")
        self.assertEqual(events[0]["event_id"], event_id)
        self.assertEqual(events[0]["event_type"], "workspace_seeded")

    def test_get_current_workspace_loads_saved_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            workspace = _workspace()
            repository.save_workspace_version(
                "workspace-1",
                workspace,
                actor="system",
                parent_version_hash=None,
                reason="seed fixture",
            )

            loaded = repository.get_current_workspace("workspace-1")

        self.assertEqual(loaded, workspace)

    def test_restore_moves_current_to_an_immutable_previous_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

        self.assertTrue(result["restored"])
        self.assertEqual(result["before_hash"], second_hash)
        self.assertEqual(current, first)
        self.assertTrue(versions[first_hash]["is_current"])
        self.assertFalse(versions[second_hash]["is_current"])
        self.assertEqual(versions[second_hash]["parent_version_hash"], first_hash)
        self.assertEqual(_count_events(events, "workspace_restored"), 1)

    def test_saves_pending_review_and_marks_approved(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

        self.assertEqual(review_id, "review-1")
        self.assertEqual(pending["status"], "pending")
        self.assertEqual(pending["base_workspace_version_hash"], base_hash)
        self.assertEqual(
            pending["proposed_workspace_version_hash"],
            workspace_version_hash(proposed),
        )
        self.assertEqual(approved["schema_version"], "research_tree.workspace_review.v1")
        self.assertEqual(approved["status"], "approved_applied")
        self.assertEqual(approved["approval_event_id"], "evt-1")
        self.assertEqual(approved["actor_type"], "user")
        self.assertEqual(approved["actor_id"], "local_user")

    def test_marks_reviews_rejected_and_edited(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

        self.assertEqual(rejected["status"], "rejected")
        self.assertEqual(rejected["rejection_event_id"], "evt-reject")
        self.assertEqual(edited_review["status"], "edited")
        self.assertEqual(edited_review["edit_event_id"], "evt-edit")
        self.assertEqual(
            edited_review["edited_workspace_version_hash"],
            workspace_version_hash(edited),
        )
        self.assertEqual(edited_review["edited_workspace"], edited)

    def test_pending_review_loads_after_repository_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

            reloaded = LocalJsonWorkspaceRepository(Path(directory))
            review = reloaded.get_pending_review("workspace-1", "review-1")

        self.assertEqual(review["status"], "pending")
        self.assertEqual(review["proposed_workspace"], proposed)

    def test_approve_review_after_repository_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _save_pending_review(repository, review_id="review-approve")
            reloaded = LocalJsonWorkspaceRepository(Path(directory))

            result = reloaded.approve_review_once("workspace-1", "review-approve")
            current = reloaded.get_current_workspace("workspace-1")
            review = reloaded.get_review("workspace-1", "review-approve")
            events = reloaded.list_workspace_events("workspace-1")

        self.assertTrue(result["ok"])
        self.assertEqual(review["status"], "approved_applied")
        self.assertEqual(current["title"], "Changed")
        self.assertEqual(_count_events(events, "workspace_patch_approved_applied"), 1)

    def test_reject_review_after_repository_reload_keeps_current_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _save_pending_review(repository, review_id="review-reject")
            reloaded = LocalJsonWorkspaceRepository(Path(directory))

            result = reloaded.reject_review_once("workspace-1", "review-reject")
            current = reloaded.get_current_workspace("workspace-1")
            review = reloaded.get_review("workspace-1", "review-reject")
            events = reloaded.list_workspace_events("workspace-1")

        self.assertTrue(result["ok"])
        self.assertEqual(review["status"], "rejected")
        self.assertEqual(current["title"], "Test Topic")
        self.assertEqual(_count_events(events, "workspace_patch_rejected"), 1)

    def test_edit_review_after_repository_reload(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _save_pending_review(repository, review_id="review-edit")
            reloaded = LocalJsonWorkspaceRepository(Path(directory))
            edited = {**_workspace(), "title": "Edited"}

            result = reloaded.edit_review_once(
                "workspace-1",
                "review-edit",
                edited_workspace=edited,
            )
            review = reloaded.get_review("workspace-1", "review-edit")
            events = reloaded.list_workspace_events("workspace-1")

        self.assertTrue(result["ok"])
        self.assertEqual(review["status"], "edited")
        self.assertEqual(review["edited_workspace"], edited)
        self.assertEqual(_count_events(events, "workspace_patch_edited"), 1)

    def test_stale_approval_after_repository_reload_fails_and_keeps_current(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            base_hash = _save_pending_review(repository, review_id="review-stale")
            repository.save_workspace_version(
                "workspace-1",
                {**_workspace(), "title": "User Changed"},
                actor="user",
                parent_version_hash=base_hash,
                reason="manual edit",
            )
            reloaded = LocalJsonWorkspaceRepository(Path(directory))

            result = reloaded.approve_review_once("workspace-1", "review-stale")
            current = reloaded.get_current_workspace("workspace-1")
            review = reloaded.get_review("workspace-1", "review-stale")
            events = reloaded.list_workspace_events("workspace-1")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed_stale_base")
        self.assertEqual(review["status"], "failed_stale_base")
        self.assertEqual(current["title"], "User Changed")
        self.assertEqual(
            _count_events(events, "workspace_patch_stale_approval_rejected"),
            1,
        )

    def test_approve_same_review_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _save_pending_review(repository, review_id="review-approve")

            first = repository.approve_review_once("workspace-1", "review-approve")
            second = repository.approve_review_once("workspace-1", "review-approve")
            events = repository.list_workspace_events("workspace-1")
            versions = repository.list_workspace_versions("workspace-1")

        self.assertTrue(first["ok"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(_count_events(events, "workspace_patch_approved_applied"), 1)
        self.assertEqual(len(versions), 2)

    def test_reject_same_review_twice_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
            _save_pending_review(repository, review_id="review-reject")

            first = repository.reject_review_once("workspace-1", "review-reject")
            second = repository.reject_review_once("workspace-1", "review-reject")
            events = repository.list_workspace_events("workspace-1")

        self.assertTrue(first["ok"])
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["idempotent"])
        self.assertEqual(_count_events(events, "workspace_patch_rejected"), 1)

    def test_terminal_review_actions_fail_cleanly(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = LocalJsonWorkspaceRepository(Path(directory))
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

        self.assertFalse(approve_after_reject["ok"])
        self.assertFalse(edit_after_reject["ok"])
        self.assertFalse(reject_after_approve["ok"])
        self.assertFalse(edit_after_approve["ok"])
        self.assertEqual(_count_events(events, "workspace_patch_rejected"), 1)
        self.assertEqual(_count_events(events, "workspace_patch_approved_applied"), 1)


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
