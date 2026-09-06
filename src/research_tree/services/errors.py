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


class PaperUnavailableError(WorkspaceServiceError):
    """A paper's PDF could not be fetched, or annotating it failed."""

    status_code = 502
    error_code = "paper_unavailable"


class ReviewNotFoundError(WorkspaceServiceError):
    status_code = 404
    error_code = "review_not_found"


class ReviewConflictError(WorkspaceServiceError):
    status_code = 409
    error_code = "review_conflict"


class StaleWorkspaceError(ReviewConflictError):
    error_code = "stale_workspace"


class UnauthenticatedError(WorkspaceServiceError):
    status_code = 401
    error_code = "unauthenticated"


class ForbiddenError(WorkspaceServiceError):
    """Signed in, but not allowed here: the email is not on the allowlist."""

    status_code = 403
    error_code = "not_allowed"


class RateLimitedError(WorkspaceServiceError):
    status_code = 429
    error_code = "rate_limited"


class NoLlmCredentialsError(WorkspaceServiceError):
    """The account has no OpenAI key of its own and no allowance on the platform key."""

    status_code = 402
    error_code = "no_llm_credentials"


class AllowanceExhaustedError(WorkspaceServiceError):
    status_code = 402
    error_code = "allowance_exhausted"


class ApiKeyInvalidError(WorkspaceServiceError):
    status_code = 400
    error_code = "api_key_invalid"
