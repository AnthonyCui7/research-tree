from __future__ import annotations

from typing import Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, field_validator


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


FindingType = Literal[
    "weak_branch",
    "misplaced_paper",
    "missing_branch",
    "too_flat",
    "duplicate_concept",
    "budget_issue",
    "paper_path_issue",
    "other",
]
Severity = Literal["low", "medium", "high"]


class WorkspaceCritiqueFinding(BaseModel):
    finding_type: FindingType
    target_ids: dict[str, Any] = Field(default_factory=dict)
    severity: Severity
    explanation: str
    suggested_fix: str | None = None

    # The response format is not strict, so a label can come back outside the
    # list. The two labels are only ever printed beside the explanation, and
    # one unfamiliar word used to cost the reader the whole critique.
    @field_validator("finding_type", mode="before")
    @classmethod
    def _an_unfamiliar_type_is_other(cls, value: Any) -> Any:
        return value if value in get_args(FindingType) else "other"

    @field_validator("severity", mode="before")
    @classmethod
    def _an_unfamiliar_severity_is_medium(cls, value: Any) -> Any:
        return value if value in get_args(Severity) else "medium"


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
