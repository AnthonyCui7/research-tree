from __future__ import annotations

from typing import Any, Mapping

from research_tree.principal import acting_user_id, current_owner_id
from research_tree.services.errors import (
    InvalidPayloadError,
    ReviewConflictError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.reviews import validate_workspace_proposal
from research_tree.services.tenancy import require_owned
from research_tree.services.validation import validate_resource_id
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.workspace.repository import normalized_topic_key
from research_tree.workspace.operations import (
    WorkspacePatchError,
    apply_structured_workspace_patch,
    operation_target_ids,
)
from research_tree.workspace.repository import StaleVersionError, WorkspaceRepository


MAX_OPERATIONS_PER_EDIT = 50
# Titles and labels in a version reason are cut so the history row stays a row.
NAME_CHARACTERS = 60


class WorkspaceEditService:
    """Edits the reader makes by hand: rename a branch, move or remove a paper.

    The change is expressed as the same structured operations the assistant's
    proposals apply, goes through the same validators, and lands as a version
    the reader authored. There is no review step: the person doing the editing
    is the reviewer, and version history is the undo.
    """

    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    def _refuse_a_topic_already_taken(
        self,
        workspace_id: str,
        current: Mapping[str, Any],
        proposed: Mapping[str, Any],
    ) -> None:
        """One topic, one workspace, however it got the name.

        A build already refuses a topic this account has a workspace for.
        Renaming was the way around that, and two workspaces on one topic used
        to mean one of them silently vanished from the only list the app can
        open a workspace from.
        """

        topic = str(proposed.get("topic") or proposed.get("title") or "")
        if topic == str(current.get("topic") or current.get("title") or ""):
            return
        topic_key = normalized_topic_key(topic)
        if not topic_key:
            return
        for other in self.repository.list_workspaces(owner_id=current_owner_id()):
            if other["workspace_id"] == workspace_id:
                continue
            if normalized_topic_key(str(other.get("topic") or other.get("title") or "")) == topic_key:
                raise InvalidPayloadError(
                    f"You already have a workspace on that topic: {other.get('title') or other['workspace_id']}."
                )

    def apply(
        self,
        workspace_id: str,
        *,
        operations: list[Mapping[str, Any]],
        expected_version_hash: str | None,
    ) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        if not isinstance(operations, list) or not operations:
            raise InvalidPayloadError("operations must be a non-empty list.")
        if len(operations) > MAX_OPERATIONS_PER_EDIT:
            raise InvalidPayloadError(
                f"an edit applies at most {MAX_OPERATIONS_PER_EDIT} operations."
            )
        require_owned(self.repository, safe_workspace_id)
        current = self._load_current(safe_workspace_id)
        current_hash = workspace_version_hash(current)
        if expected_version_hash and expected_version_hash != current_hash:
            raise ReviewConflictError("Workspace changed. Refresh before editing.")

        summary = describe_edit(current, operations)
        try:
            proposed = apply_structured_workspace_patch(
                base_workspace=current,
                operations=operations,
                removed_by=acting_user_id(),
            )
        except WorkspacePatchError as error:
            raise InvalidPayloadError(str(error)) from error
        self._refuse_a_topic_already_taken(safe_workspace_id, current, proposed)

        derived, diff_summary, diff_warnings = derive_operations_and_diff_summary(
            workspace=current,
            proposed_workspace=proposed,
        )
        validation = validate_workspace_proposal(
            current_workspace=current,
            proposed_workspace=proposed,
            proposed_operations=derived,
            diff_summary=diff_summary,
        )
        if not validation.get("valid"):
            reasons = "; ".join(str(item) for item in validation.get("errors") or [])
            raise InvalidPayloadError(
                f"That change would leave the workspace inconsistent: {reasons}"
            )
        warnings = [*diff_warnings, *(validation.get("warnings") or [])]

        proposed_hash = workspace_version_hash(proposed)
        if proposed_hash == current_hash:
            return {
                "workspace_id": safe_workspace_id,
                "workspace_version_hash": current_hash,
                "previous_version_hash": current_hash,
                "changed": False,
                "summary": summary,
                "warnings": warnings,
            }
        try:
            version_hash = self.repository.save_workspace_version(
                safe_workspace_id,
                proposed,
                actor="user",
                actor_type="user",
                actor_id=acting_user_id(),
                parent_version_hash=current_hash,
                reason=summary,
                expected_version_hash=current_hash,
            )
        except StaleVersionError as error:
            raise ReviewConflictError(str(error)) from error
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error
        self.repository.append_workspace_event(
            safe_workspace_id,
            actor="user",
            actor_type="user",
            actor_id=acting_user_id(),
            event_type="workspace_edited",
            target_ids=operation_target_ids(derived),
            before_hash=current_hash,
            after_hash=version_hash,
            payload={
                "summary": summary,
                "operations": [dict(operation) for operation in operations],
                "operation_types": diff_summary.get("operation_types") or [],
                "diff_summary": diff_summary,
                "warnings": warnings,
            },
        )
        return {
            "workspace_id": safe_workspace_id,
            "workspace_version_hash": version_hash,
            "previous_version_hash": current_hash,
            "changed": True,
            "summary": summary,
            "warnings": warnings,
        }

    def _load_current(self, workspace_id: str) -> dict[str, Any]:
        try:
            return self.repository.get_current_workspace(workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {workspace_id}"
            ) from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error


def describe_edit(workspace: Mapping[str, Any], operations: list[Mapping[str, Any]]) -> str:
    """The version reason: what changed, by name, as history will show it."""

    names = _names(workspace)
    parts: list[str] = []
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        target = operation.get("target") if isinstance(operation.get("target"), Mapping) else {}

        def field(name: str) -> str:
            value = operation.get(name)
            if value is None:
                value = target.get(name)
            return str(value or "")

        verb = field("op")
        entity = field("entity_type") or str(target.get("type") or "")
        if verb == "set" and entity == "branch" and field("field") == "label":
            branch_id = field("branch_id") or str(target.get("id") or "")
            parts.append(
                f"Renamed branch “{names.branch(branch_id)}” to "
                f"“{_shorten(str(operation.get('value') or ''))}”"
            )
        elif verb == "move" and entity == "paper_placement":
            parts.append(
                f"Moved “{names.paper(field('paper_id'))}” to "
                f"“{names.branch(field('to_branch_id'))}”"
            )
        elif verb == "remove" and entity == "paper_placement":
            parts.append(f"Removed “{names.paper(field('paper_id'))}”")
        elif verb == "remove" and entity == "branch":
            parts.append(f"Removed branch “{names.branch(field('branch_id'))}”")
        else:
            parts.append(" ".join(item for item in (verb, entity) if item) or "edit")
    return "; ".join(parts) or "edited on the canvas"


class _names:
    def __init__(self, workspace: Mapping[str, Any]) -> None:
        tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
        self._branches = {
            str(node.get("node_id")): str(node.get("label") or node.get("node_id"))
            for node in tree.get("nodes") or []
            if isinstance(node, Mapping) and node.get("node_id")
        }
        cards = workspace.get("paper_cards")
        self._papers = {
            str(paper_id): str(card.get("title") or paper_id)
            for paper_id, card in (cards.items() if isinstance(cards, Mapping) else [])
            if isinstance(card, Mapping)
        }

    def branch(self, branch_id: str) -> str:
        return _shorten(self._branches.get(branch_id, branch_id))

    def paper(self, paper_id: str) -> str:
        return _shorten(self._papers.get(paper_id, paper_id))


def _shorten(value: str) -> str:
    text = " ".join(value.split())
    if len(text) <= NAME_CHARACTERS:
        return text
    return text[: NAME_CHARACTERS - 1].rstrip() + "…"
