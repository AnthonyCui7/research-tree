from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str


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


class WorkspaceEventsResponse(BaseModel):
    workspace_id: str
    events: list[dict[str, Any]]


class WorkspaceReviewsResponse(BaseModel):
    workspace_id: str
    reviews: list[dict[str, Any]]


class WorkspaceReviewResponse(BaseModel):
    workspace_id: str
    review_id: str
    review: dict[str, Any]


class AgentRunRequest(BaseModel):
    message: str = Field(min_length=1)
    thread_id: str | None = None
    allow_pipeline_rerun: bool = False
    require_approval: bool = True


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
    persisted_version_hash: str | None = None
    persisted_event_ids: list[str] = Field(default_factory=list)


class ReviewEditResponse(BaseModel):
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
