from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


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


class ProposalSkepticNotes(BaseModel):
    """Second-reader objections attached to a pending review.

    Every field defaults, so the keyless fallback client's empty payload
    validates to "no objections" and the proposal proceeds.
    """

    objections: list[str] = Field(default_factory=list, max_length=2)


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


class WorkspaceValidationSummary(BaseModel):
    valid: bool
    error_count: int
    warning_count: int
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    validators_run: list[str] = Field(default_factory=list)
    repair_attempts: int = 0
    validation_round: int = 0
