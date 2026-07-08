from __future__ import annotations

from typing import Any

from research_tree.services.errors import WorkspaceNotFoundError, WorkspaceServiceError
from research_tree.services.validation import validate_resource_id
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
