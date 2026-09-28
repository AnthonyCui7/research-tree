from __future__ import annotations

from typing import Annotated, Any, Literal, TypedDict


WorkspaceAgentStatus = Literal[
    "started",
    "loaded",
    "planning",
    "retrieving",
    "constructing",
    "validating",
    "awaiting_approval",
    "approved",
    "rejected",
    "failed",
    "completed",
]


def reset_on_none(left: list[Any] | None, right: list[Any] | None) -> list[Any]:
    """Accumulate list updates within a turn; a `None` update clears the channel.

    A conversation thread reuses one checkpointed state across user turns, so
    the turn-scoped channels (errors, warnings, traces, validator results)
    are cleared by `begin_turn` at the start of each run instead of
    accumulating for the life of the thread.
    """

    if right is None:
        return []
    return [*(left or []), *right]


class WorkspaceAgentState(TypedDict, total=False):
    user_message: str
    conversation_history: list[dict[str, str]]
    final_response: str | None

    agent_run_id: str
    workspace_id: str | None
    user_id: str | None
    thread_id: str
    status: WorkspaceAgentStatus
    allow_pipeline_rerun: bool
    agent_model: str

    workspace: dict[str, Any] | None
    workspace_version_hash: str | None
    workspace_summary: dict[str, Any] | None
    candidate_artifact: dict[str, Any] | None
    candidate_artifact_path: str | None
    candidate_pool: list[dict[str, Any]]
    similar_papers_context: dict[str, Any]

    next_action: dict[str, Any] | None

    # The tool loop. `transcript_items` holds raw Responses items — the model's
    # own output (including encrypted reasoning) plus our tool results — and is
    # replayed in full on each turn because `store` is false.
    transcript_items: list[dict[str, Any]]
    tool_rounds: int
    max_tool_rounds: int
    turn_started_at: float
    # Survives across turns on a thread: papers found in an earlier message
    # stay proposable in later ones.
    session_discovered_papers: dict[str, dict[str, Any]]
    semantic_scholar_calls: int

    chat_context: dict[str, Any] | None

    retrieval_request: dict[str, Any] | None
    retrieval_guardrail_result: dict[str, Any] | None

    proposed_workspace: dict[str, Any] | None
    proposed_operations: list[dict[str, Any]]
    diff_summary: dict[str, Any] | None
    # Visible papers plus session-discovered additions; the set an edit may
    # draw papers from, for both the constructor and the validators.
    proposal_candidate_artifact: dict[str, Any] | None
    skeptic_notes: list[str]

    selected_validators: list[str]
    validation_results: Annotated[list[dict[str, Any]], reset_on_none]
    validation_summary: dict[str, Any] | None
    validation_round: int
    repair_attempts: int
    max_repair_attempts: int

    approval_payload: dict[str, Any] | None
    approval_decision: dict[str, Any] | None
    approval_required: bool
    review_id: str | None
    review_status: str | None
    persisted_event_ids: Annotated[list[str], reset_on_none]

    warnings: Annotated[list[str], reset_on_none]
    errors: Annotated[list[str], reset_on_none]
    node_trace: Annotated[list[dict[str, Any]], reset_on_none]


class WorkspaceAgentInput(TypedDict, total=False):
    workspace_id: str | None
    workspace: dict[str, Any] | None
    candidate_artifact: dict[str, Any] | None
    candidate_artifact_path: str | None
    conversation_history: list[dict[str, str]]
    user_message: str
    user_id: str | None
    thread_id: str | None
    allow_pipeline_rerun: bool
    agent_model: str
    max_repair_attempts: int


class WorkspaceAgentOutput(TypedDict, total=False):
    status: str
    final_response: str | None
    proposed_operations: list[dict[str, Any]]
    proposed_workspace: dict[str, Any] | None
    diff_summary: dict[str, Any] | None
    validation_summary: dict[str, Any] | None
    warnings: list[str]
    errors: list[str]
    approval_required: bool
    review_id: str | None
    review_status: str | None
    persisted_event_ids: list[str]
