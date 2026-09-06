from __future__ import annotations

from typing import Any

from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import WorkspaceRepository


def publish_workspace_version(
    *,
    repository: WorkspaceRepository,
    workspace: dict[str, Any],
    reason: str,
    event_type: str,
    event_payload: dict[str, Any],
    expected_parent_version_hash: str | None = None,
    pipeline_run_id: str | None = None,
    owner_id: str | None = None,
) -> dict[str, Any]:
    workspace_id = str(workspace.get("workspace_id") or "").strip()
    if not workspace_id:
        raise ValueError("workspace_id is required to publish a workspace version.")

    try:
        current_workspace = repository.get_current_workspace(workspace_id)
        parent_hash = workspace_version_hash(current_workspace)
    except FileNotFoundError:
        current_workspace = None
        parent_hash = None

    if (
        expected_parent_version_hash is not None
        and parent_hash != expected_parent_version_hash
    ):
        raise RuntimeError(
            "workspace current version changed before publish: "
            f"expected {expected_parent_version_hash}, found {parent_hash}."
        )

    if current_workspace is not None and parent_hash is not None:
        try:
            repository.get_workspace_version(workspace_id, parent_hash)
        except FileNotFoundError:
            repository.save_workspace_version(
                workspace_id,
                current_workspace,
                actor="system",
                parent_version_hash=None,
                reason="imported legacy current workspace",
                pipeline_run_id=pipeline_run_id,
            )

    version_hash = workspace_version_hash(workspace)
    if version_hash == parent_hash:
        return {
            "workspace_id": workspace_id,
            "parent_version_hash": parent_hash,
            "version_hash": version_hash,
            "event_id": None,
            "published": False,
        }

    # The pre-check above is a courtesy; the repository re-checks the parent
    # inside its own transaction, which is what makes a concurrent publish
    # lose cleanly instead of overwriting.
    version_hash = repository.save_workspace_version(
        workspace_id,
        workspace,
        actor="system",
        parent_version_hash=parent_hash,
        reason=reason,
        pipeline_run_id=pipeline_run_id,
        expected_version_hash=parent_hash,
        owner_id=owner_id,
    )
    event_id = repository.append_workspace_event(
        workspace_id,
        actor="system",
        event_type=event_type,
        target_ids={"workspace_id": workspace_id},
        before_hash=parent_hash,
        after_hash=version_hash,
        payload={
            **event_payload,
            "replaces_version_hash": parent_hash,
            "source_candidate_artifact": workspace.get("source_candidate_artifact"),
        },
    )
    return {
        "workspace_id": workspace_id,
        "parent_version_hash": parent_hash,
        "version_hash": version_hash,
        "event_id": event_id,
        "published": True,
    }
