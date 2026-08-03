from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class AgentIntent(BaseModel):
    intent_type: Literal[
        "chat",
        "explain_paper",
        "explain_branch",
        "explain_workspace",
        "recommend_papers",
        "critique_workspace",
        "modify_workspace",
        "expand_branch",
        "retrieve_more_papers",
        "repair_workspace",
    ]
    confidence: float = Field(ge=0.0, le=1.0)
    target_branch_id: str | None = None
    target_paper_ids: list[str] = Field(default_factory=list)
    requires_workspace_modification: bool
    requires_more_papers: bool
    reason: str


class AgentNextAction(BaseModel):
    action_type: Literal[
        "answer_chat",
        "critique_workspace",
        "construct_workspace_modification",
        "prepare_retrieval_rerun",
        "repair_workspace_proposal",
        "finalize",
    ]
    reason: str
    target_branch_id: str | None = None
    target_paper_ids: list[str] = Field(default_factory=list)
    needs_more_context: bool = False
    retrieval_request: dict[str, Any] | None = None
    modification_instruction: str | None = None


class PipelineRerunRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    topic: str
    query_overrides: list[str] = Field(default_factory=list)
    max_initial_results: int | None = None
    max_candidates: int | None = None
    top_k_depth: int | None = None
    alpha: float | None = None
    reason: str
    requested_by_agent: bool = True


class WorkspaceCritiqueFinding(BaseModel):
    finding_type: Literal[
        "weak_branch",
        "misplaced_paper",
        "missing_branch",
        "too_flat",
        "duplicate_concept",
        "budget_issue",
        "paper_path_issue",
        "other",
    ]
    target_ids: dict[str, Any] = Field(default_factory=dict)
    severity: Literal["low", "medium", "high"]
    explanation: str
    suggested_fix: str | None = None


class WorkspaceCritique(BaseModel):
    summary: str
    findings: list[WorkspaceCritiqueFinding] = Field(default_factory=list)
    should_modify_workspace: bool = False


class WorkspaceChatResponse(BaseModel):
    answer: str
    referenced_paper_ids: list[str] = Field(default_factory=list)
    referenced_branch_ids: list[str] = Field(default_factory=list)


class WorkspaceValidationSummary(BaseModel):
    valid: bool
    error_count: int
    warning_count: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validators_run: list[str] = Field(default_factory=list)
    repair_attempts: int = 0
    validation_round: int = 0
