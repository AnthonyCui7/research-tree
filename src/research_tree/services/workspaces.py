from __future__ import annotations

from typing import Any

from research_tree.principal import acting_user_id, current_owner_id
from research_tree.services.errors import (
    InvalidResourceIdError,
    ReviewConflictError,
    WorkspaceNotFoundError,
    WorkspaceServiceError,
    WorkspaceVersionNotFoundError,
)
from research_tree.services.tenancy import require_owned
from research_tree.services.validation import validate_resource_id, validate_version_hash
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.repository import WorkspaceRepository


# A full workspace document, twice over on a review that was edited. Nothing
# that lists reviews reads either one.
_REVIEW_DOCUMENT_FIELDS = frozenset({"proposed_workspace", "edited_workspace"})
# The change a review proposes, which the assistant panel renders while the
# review is still open. On a finished review it is history nobody reads.
_REVIEW_PROPOSAL_FIELDS = frozenset({"proposed_operations", "interrupt_payload"})


class WorkspaceQueryService:
    def __init__(self, repository: WorkspaceRepository) -> None:
        self.repository = repository

    def list_workspaces(self) -> dict[str, Any]:
        return {"workspaces": self.repository.list_workspaces(owner_id=current_owner_id())}

    def get_current_workspace(self, workspace_id: str) -> dict[str, Any]:
        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        require_owned(self.repository, safe_workspace_id)
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
        """Every review of this workspace, newest first, without the documents.

        Reviews accumulate for the life of a workspace and each one embeds the
        document it proposed — over half a megabyte on a real workspace — so the
        list grew without bound on a route the assistant panel calls on open.
        The documents go out of the list entirely: the detail route still serves
        one review whole. What the panel restores from is the change itself, and
        that only matters while a review is still pending.
        """

        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        self.get_current_workspace(safe_workspace_id)
        summaries = [
            {
                key: value
                for key, value in review.items()
                if key not in _REVIEW_DOCUMENT_FIELDS
                and not (
                    key in _REVIEW_PROPOSAL_FIELDS
                    and str(review.get("status") or "") != "pending"
                )
            }
            for review in self.repository.list_workspace_reviews(safe_workspace_id)
        ]
        summaries.sort(key=lambda review: str(review.get("created_at") or ""), reverse=True)
        return {"workspace_id": safe_workspace_id, "reviews": summaries}

    def get_paper_content(self, workspace_id: str, paper_id: str) -> dict[str, Any]:
        """Return the text extracted from a paper's open-access PDF.

        Enrichment already downloads and extracts every open-access PDF it can
        reach, so the reader serves that artifact rather than fetching again.
        The extract is kept out of the workspace document because it dwarfs the
        editorial content; it is fetched per paper, on demand.
        """

        safe_workspace_id = validate_resource_id(workspace_id, field_name="workspace_id")
        # A paper id is whatever Semantic Scholar, the DOI, or the title gave us,
        # so it can carry slashes and spaces that no resource-id pattern allows.
        # The stored filename is a hash of it, which is what keeps the path safe.
        safe_paper_id = paper_id.strip()
        if not safe_paper_id or len(safe_paper_id) > 512:
            raise InvalidResourceIdError("paper_id must be between 1 and 512 characters.")
        self.get_current_workspace(safe_workspace_id)
        try:
            content = self.repository.get_paper_content(safe_workspace_id, safe_paper_id)
        except FileNotFoundError as error:
            raise WorkspaceNotFoundError(
                f"no stored content for paper: {safe_paper_id}"
            ) from error
        except ValueError as error:
            raise WorkspaceServiceError(str(error)) from error
        full_text = content.get("full_text")
        return {
            "workspace_id": safe_workspace_id,
            "paper_id": safe_paper_id,
            "status": str(content.get("status") or "unavailable"),
            "source_type": content.get("source_type"),
            "source_url": content.get("source_url"),
            "page_count": content.get("page_count"),
            "figure_count": content.get("figure_count"),
            "truncated": bool(content.get("truncated")),
            "retrieved_at": content.get("retrieved_at"),
            "full_text": full_text if isinstance(full_text, str) else "",
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
            raise WorkspaceVersionNotFoundError("workspace version does not exist") from error
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
                actor_id=acting_user_id(),
                reason=reason,
            )
        except FileNotFoundError as error:
            raise WorkspaceVersionNotFoundError("workspace version does not exist") from error
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
        require_owned(self.repository, safe_workspace_id)
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
