from __future__ import annotations


class WorkspaceServiceError(Exception):
    status_code = 500
    error_code = "workspace_service_error"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class InvalidResourceIdError(WorkspaceServiceError):
    status_code = 400
    error_code = "invalid_resource_id"


class InvalidPayloadError(WorkspaceServiceError):
    status_code = 400
    error_code = "invalid_payload"


class WorkspaceNotFoundError(WorkspaceServiceError):
    status_code = 404
    error_code = "workspace_not_found"


class ReviewNotFoundError(WorkspaceServiceError):
    status_code = 404
    error_code = "review_not_found"


class ReviewConflictError(WorkspaceServiceError):
    status_code = 409
    error_code = "review_conflict"


class StaleWorkspaceError(ReviewConflictError):
    error_code = "stale_workspace"

