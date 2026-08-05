from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from research_tree.annotation.models import PaperAnnotation
from research_tree.llm import DEFAULT_MODEL


class HealthResponse(BaseModel):
    status: str


class ApiKeyStatus(BaseModel):
    configured: bool
    """Last four characters only — a whole key never leaves the server."""
    masked: str | None = None
    """Where the key came from: `environment` today, an account store later."""
    source: str | None = None


class ApiKeysResponse(BaseModel):
    openai: ApiKeyStatus


class SaveApiKeyRequest(BaseModel):
    provider: str = Field(pattern=r"^openai$")
    api_key: str = Field(min_length=8, max_length=400)


class SaveApiKeyResponse(BaseModel):
    stored: bool
    detail: str


class BugReportRequest(BaseModel):
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


class TopicReviewRequest(BaseModel):
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
class CreateWorkspaceRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=240)
    topic_review_token: str = Field(min_length=1, max_length=128)
    # The reader's optional steer for construction — what to emphasize, exclude,
    # or anchor on. Empty is the same as absent.
    instructions: str = Field(default="", max_length=2_000)


class PipelineRerunApiRequest(BaseModel):
    start_stage: str
    expected_version_hash: str | None = None


class PipelineRunResponse(BaseModel):
    pipeline_run: dict[str, Any]


class PipelineRunsResponse(BaseModel):
    workspace_id: str
    pipeline_runs: list[dict[str, Any]]


class RestoreWorkspaceRequest(BaseModel):
    expected_version_hash: str | None = None
    reason: str = "restored from workspace history"


class DeleteWorkspaceRequest(BaseModel):
    expected_version_hash: str | None = None


class WorkspaceMutationResponse(BaseModel):
    workspace_id: str
    workspace_version_hash: str
    changed: bool


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
    annotations: list[PaperAnnotation]


class WorkspaceReviewResponse(BaseModel):
    workspace_id: str
    review_id: str
    review: dict[str, Any]


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_history: list[dict[str, str]] = Field(default_factory=list, max_length=24)
    # Thread ids are persisted into event payloads, so they are constrained the
    # same way workspace ids are.
    thread_id: str | None = Field(
        default=None, max_length=180, pattern=r"^[A-Za-z0-9_.:-]+$"
    )
    allow_pipeline_rerun: bool = False
    require_approval: bool = True
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


class ReviewDecisionRequest(BaseModel):
    reason: str | None = None
    approval_decision: dict[str, Any] = Field(default_factory=dict)


class ReviewEditRequest(BaseModel):
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
