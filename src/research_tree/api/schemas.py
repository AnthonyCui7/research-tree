from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from research_tree.annotation.models import PaperAnnotation
from research_tree.llm import DEFAULT_MODEL
from research_tree.payload import unstorable_reason


class ClientRequest(BaseModel):
    """A model built from a request body.

    Every body the API accepts is checked here for the two shapes this service
    cannot carry: a character no store can hold, and nesting deeper than a
    response can be serialized from. Doing it on the base class rather than
    field by field means a field added later is covered without being told to
    be, and it is the only place that has to know the rule.
    """

    @model_validator(mode="after")
    def _reject_what_cannot_be_carried(self) -> "ClientRequest":
        for field_name in type(self).model_fields:
            reason = unstorable_reason(getattr(self, field_name), path=field_name)
            if reason is not None:
                raise ValueError(reason)
        return self


class HealthResponse(BaseModel):
    status: str


class ApiKeyStatus(BaseModel):
    configured: bool
    """Last four characters only — a whole key never leaves the server."""
    masked: str | None = None
    """`environment` for the local profile's key, `account` for a saved one."""
    source: str | None = None
    created_at: str | None = None


class AllowanceSummary(BaseModel):
    """A sponsored allowance on the platform key, as the account page shows it."""

    id: str
    limit_usd: float
    spent_usd: float
    remaining_usd: float
    period: str
    period_start: str | None = None
    expires_at: str | None = None
    exhausted: bool


class ApiKeysResponse(BaseModel):
    openai: ApiKeyStatus
    allowance: AllowanceSummary | None = None
    """False when no key-encryption key is configured (and for the local profile)."""
    saving_enabled: bool = False
    """Whether the server holds a platform key an allowance could spend."""
    platform_key: bool = False


class SaveApiKeyRequest(ClientRequest):
    provider: str = Field(default="openai", pattern=r"^openai$")
    # No length bounds and a default on purpose: a pydantic refusal echoes the
    # value it rejected, and a *missing field* refusal echoes the whole body —
    # so a caller that named the field wrong got its key quoted back. With a
    # default there is nothing for pydantic to refuse; the route checks the
    # shape itself and answers without repeating the value.
    api_key: str = ""


class SaveApiKeyResponse(BaseModel):
    stored: bool
    detail: str
    openai: ApiKeyStatus | None = None


class RemoveApiKeyResponse(BaseModel):
    removed: bool
    detail: str = ""


class UsageEvent(BaseModel):
    created_at: str | None = None
    source: str
    feature: str | None = None
    label: str | None = None
    model: str
    cost_usd: float


class UsageResponse(BaseModel):
    days: int
    calls: int
    total_usd: float
    byok_usd: float
    sponsored_usd: float
    recent: list[UsageEvent] = Field(default_factory=list)


class BugReportRequest(ClientRequest):
    summary: str = Field(min_length=1, max_length=200)
    details: str = Field(default="", max_length=8_000)
    area: str = Field(default="general", max_length=40, pattern=r"^[a-z-]+$")


class BugReportResponse(BaseModel):
    received: bool
    stored: bool
    detail: str


class WorkspaceResponse(BaseModel):
    workspace_id: str
    workspace_version_hash: str
    workspace: dict[str, Any]


class WorkspaceSummary(BaseModel):
    workspace_id: str
    workspace_version_hash: str
    title: str
    topic: str
    paper_count: int
    branch_count: int
    paper_path_count: int
    updated_at: str | None = None


class WorkspacesResponse(BaseModel):
    workspaces: list[WorkspaceSummary]


class WorkspaceVersionsResponse(BaseModel):
    workspace_id: str
    versions: list[dict[str, Any]]


class TopicReviewRequest(ClientRequest):
    topic: str = Field(min_length=1, max_length=240)


class TopicReviewResponse(BaseModel):
    submitted_topic: str
    normalized_topic: str
    is_research_topic: bool
    guidance: str
    existing_workspace: dict[str, str] | None = None
    can_create: bool
    model: str | None = None
    source_paper: dict[str, str] | None = None
    topic_review_token: str | None = None


# Pipeline construction is model-locked (see WorkspacePipelineService), so
# neither request takes a model. Only the agent does.
class CreateWorkspaceRequest(ClientRequest):
    topic: str = Field(min_length=1, max_length=240)
    topic_review_token: str = Field(min_length=1, max_length=128)
    # The reader's optional steer for construction — what to emphasize, exclude,
    # or anchor on. Empty is the same as absent.
    instructions: str = Field(default="", max_length=2_000)


class PipelineRerunApiRequest(ClientRequest):
    start_stage: str
    expected_version_hash: str | None = None


class PipelineRunResponse(BaseModel):
    pipeline_run: dict[str, Any]


class PipelineRunsResponse(BaseModel):
    # Absent when the list spans the account rather than one workspace.
    workspace_id: str | None = None
    pipeline_runs: list[dict[str, Any]]


class RestoreWorkspaceRequest(ClientRequest):
    expected_version_hash: str | None = None
    reason: str = "restored from workspace history"


class DeleteWorkspaceRequest(ClientRequest):
    expected_version_hash: str | None = None


class WorkspaceMutationResponse(BaseModel):
    workspace_id: str
    workspace_version_hash: str
    changed: bool


class WorkspaceEditRequest(ClientRequest):
    """A change made by hand on the canvas, as structured operations.

    The operations are the ones `workspace/operations.py` applies for the
    assistant's proposals (`set` a branch label, `move` or `remove` a
    `paper_placement`), so a hand edit and an approved proposal write the
    same shape.
    """

    expected_version_hash: str | None = None
    operations: list[dict[str, Any]] = Field(min_length=1, max_length=50)


class WorkspaceEditResponse(BaseModel):
    workspace_id: str
    workspace_version_hash: str
    # What the edit replaced; restoring it is the undo.
    previous_version_hash: str
    changed: bool
    # The version reason, as history shows it.
    summary: str
    warnings: list[str] = Field(default_factory=list)


class WorkspaceEventsResponse(BaseModel):
    workspace_id: str
    events: list[dict[str, Any]]


class WorkspaceReviewsResponse(BaseModel):
    workspace_id: str
    reviews: list[dict[str, Any]]


class PaperContentResponse(BaseModel):
    workspace_id: str
    paper_id: str
    status: str
    source_type: str | None = None
    source_url: str | None = None
    page_count: int | None = None
    figure_count: int | None = None
    truncated: bool = False
    retrieved_at: str | None = None
    full_text: str


class PaperAnnotationsResponse(BaseModel):
    workspace_id: str
    paper_id: str
    generated_at: str | None = None
    model: str | None = None
    retrieval_mode: str | None = None
    annotations: list[PaperAnnotation]


class AnnotationJobResponse(BaseModel):
    job_id: str
    status: str
    workspace_id: str | None = None
    paper_id: str | None = None
    retrieval_mode: str | None = None
    error_code: str | None = None
    error_status: int | None = None
    detail: str | None = None
    created_at: str | None = None


class WorkspaceReviewResponse(BaseModel):
    workspace_id: str
    review_id: str
    review: dict[str, Any]


class AgentRunRequest(ClientRequest):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_history: list[dict[str, str]] = Field(default_factory=list, max_length=24)
    # A thread id names the account and the workspace it belongs to ahead of
    # its own suffix, so it is longer than a workspace id; the characters are
    # the same set, since ids are persisted into event payloads.
    thread_id: str | None = Field(
        default=None, max_length=254, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    allow_pipeline_rerun: bool = False
    # `require_approval` was removed: proposals and reruns always become pending
    # reviews. Old clients still sending it are ignored by pydantic.
    model: str = Field(default=DEFAULT_MODEL, min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$")


class AgentRunResponse(BaseModel):
    workspace_id: str
    status: str
    thread_id: str | None = None
    agent_run_id: str | None = None
    review_id: str | None = None
    interrupt_payload: dict[str, Any] | None = None
    final_response: str | None = None
    diff_summary: dict[str, Any] | None = None
    validation_summary: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    persisted_event_ids: list[str] = Field(default_factory=list)


class ReviewDecisionRequest(ClientRequest):
    reason: str | None = None
    approval_decision: dict[str, Any] = Field(default_factory=dict)


class ReviewEditRequest(ClientRequest):
    proposed_workspace: dict[str, Any]
    approval_decision: dict[str, Any] = Field(default_factory=dict)


class ReviewActionResponse(BaseModel):
    workspace_id: str
    review_id: str
    status: str
    idempotent: bool = False
    workspace_version_hash: str | None = None
    # Set only when approving a workspace patch; a rejected or rerun review
    # publishes no version.
    persisted_version_hash: str | None = None
    persisted_event_ids: list[str] = Field(default_factory=list)
    # Set only when approving a pipeline_rerun review.
    pipeline_run: dict[str, Any] | None = None


class ReviewEditResponse(BaseModel):
    """Answers 200 even when the edit fails validation.

    A rejected edit is a result the caller has to render — which papers moved
    where, and which rules the edit broke — not a transport error. `status` is
    `"failed_validation"` in that case and `validation_summary` carries the
    reasons; a new review is only created when the edit validates.
    """

    workspace_id: str
    review_id: str
    new_review_id: str | None = None
    status: str
    idempotent: bool = False
    workspace_version_hash: str | None = None
    interrupt_payload: dict[str, Any] | None = None
    diff_summary: dict[str, Any] | None = None
    validation_summary: dict[str, Any] | None = None
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    persisted_event_ids: list[str] = Field(default_factory=list)


class SessionUser(BaseModel):
    id: str
    email: str
    name: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False
    is_verified: bool = False


class SessionResponse(BaseModel):
    auth_mode: str
    google_sign_in: bool = False
    user: SessionUser
