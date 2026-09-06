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


class ByokUnavailableError(WorkspaceServiceError):
    """Keys cannot be saved here: no key-encryption key is configured."""

    status_code = 503
    error_code = "byok_unavailable"


class ProviderUnreachableError(WorkspaceServiceError):
    """OpenAI could not be reached to check a key."""

    status_code = 502
    error_code = "provider_unreachable"


# Error codes whose message is written by this codebase for the user to read.
# Everything else gets a generic message so internal detail cannot leak.
PUBLIC_ERROR_CODES = {
    "invalid_payload",
    "invalid_resource_id",
    "unauthenticated",
    "not_allowed",
    "rate_limited",
    "no_llm_credentials",
    "allowance_exhausted",
    "api_key_invalid",
    "byok_unavailable",
    "provider_unreachable",
}


def public_service_error_message(exc: WorkspaceServiceError) -> str:
    if exc.error_code in {"workspace_not_found", "review_not_found"}:
        return "That workspace is no longer available. Refresh and try again."
    if exc.error_code in {"review_conflict", "stale_workspace"}:
        return "This workspace changed. Refresh and try again."
    if exc.error_code == "paper_unavailable":
        return "We could not fetch or annotate that paper right now. Try again later."
    if exc.error_code in PUBLIC_ERROR_CODES:
        # Bad-request messages name the offending field and its allowed values,
        # which is exactly what the caller needs to fix the request.
        return exc.message
    return "We could not complete that request. Please try again."
