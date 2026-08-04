from __future__ import annotations

from typing import Any

from research_tree.services.errors import (
    ReviewConflictError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
)
from research_tree.services.validation import validate_resource_id, validate_version_hash
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import WorkspaceRepository


class WorkspaceQueryService:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    def list_workspaces(self) -> dict[str, Any]:
        return {"workspaces": self.repository.list_workspaces()}

    def get_current_workspace(self, workspace_id: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        try:
            workspace = self.repository.get_current_workspace(safe_workspace_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error
        return {
            "workspace_id": safe_workspace_id,
            "workspace_version_hash": workspace_version_hash(workspace),
            "workspace": workspace,
        }

    def list_versions(self, workspace_id: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        self.get_current_workspace(safe_workspace_id)
        return {
            "workspace_id": safe_workspace_id,
            "versions": self.repository.list_workspace_versions(safe_workspace_id),
        }

    def list_events(self, workspace_id: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        self.get_current_workspace(safe_workspace_id)
        return {
            "workspace_id": safe_workspace_id,
            "events": self.repository.list_workspace_events(safe_workspace_id),
        }

    def list_reviews(self, workspace_id: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        self.get_current_workspace(safe_workspace_id)
        return {
            "workspace_id": safe_workspace_id,
            "reviews": self.repository.list_workspace_reviews(safe_workspace_id),
        }

    def get_version(self, workspace_id: str, version_hash: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        safe_version_hash = validate_version_hash(version_hash)
        self.get_current_workspace(safe_workspace_id)
        try:
            workspace = self.repository.get_workspace_version(
                safe_workspace_id,
                safe_version_hash,
            )
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError("workspace version does not exist") from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error
        return {
            "workspace_id": safe_workspace_id,
            "workspace_version_hash": workspace_version_hash(workspace),
            "workspace": workspace,
        }

    def restore_version(
        self,
        workspace_id: str,
        version_hash: str,
        *,
        expected_version_hash: str | None,
        reason: str,
    ) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        safe_version_hash = validate_version_hash(version_hash)
        current = self.get_current_workspace(safe_workspace_id)
        if expected_version_hash and current["workspace_version_hash"] != expected_version_hash:
            raise ReviewConflictError("Workspace changed. Refresh history before restoring.")
        try:
            result = self.repository.restore_workspace_version(
                safe_workspace_id,
                safe_version_hash,
                actor="user",
                actor_type="user",
                reason=reason,
            )
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError("workspace version does not exist") from error
        return {
            "workspace_id": safe_workspace_id,
            "workspace_version_hash": str(result["version_hash"]),
            "changed": bool(result["restored"]),
        }

    def delete_workspace(
        self,
        workspace_id: str,
        *,
        expected_version_hash: str | None,
    ) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        try:
            result = self.repository.delete_workspace(
                safe_workspace_id,
                expected_version_hash=expected_version_hash,
            )
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"workspace does not exist: {safe_workspace_id}"
            ) from error
        except ValueError as error:
            raise ReviewConflictError(str(error)) from error
        return {
            "workspace_id": safe_workspace_id,
            "workspace_version_hash": str(result["workspace_version_hash"]),
            "changed": bool(result["deleted"]),
        }
