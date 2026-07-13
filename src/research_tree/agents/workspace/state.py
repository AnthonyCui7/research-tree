from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langchain_core.messages import AnyMessage
from langgraph.graph.message import add_messages


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


class WorkspaceAgentState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    user_message: str
    final_response: str | None

    agent_run_id: str
    workspace_id: str | None
    user_id: str | None
    thread_id: str
    status: WorkspaceAgentStatus
    allow_pipeline_rerun: bool
    require_approval: bool
    agent_model: str

    workspace: dict[str, Any] | None
    workspace_version_hash: str | None
    workspace_summary: dict[str, Any] | None
    candidate_artifact: dict[str, Any] | None
    candidate_artifact_path: str | None
    candidate_pool: list[dict[str, Any]]
    similar_papers_context: dict[str, Any]
    off_path_papers: list[dict[str, Any]]

    intent: dict[str, Any] | None
    next_action: dict[str, Any] | None
    action_history: Annotated[list[dict[str, Any]], operator.add]
    action_iteration_count: int
    max_action_iterations: int

    chat_context: dict[str, Any] | None
    modification_context: dict[str, Any] | None

    retrieval_request: dict[str, Any] | None
    retrieval_guardrail_result: dict[str, Any] | None
    retrieval_result: dict[str, Any] | None

    proposed_workspace: dict[str, Any] | None
    proposed_operations: list[dict[str, Any]]
    diff_summary: dict[str, Any] | None
    updated_workspace: dict[str, Any] | None

    selected_validators: list[str]
    validation_results: Annotated[list[dict[str, Any]], operator.add]
    validation_summary: dict[str, Any] | None
    validation_round: int
    repair_attempts: int
    max_repair_attempts: int

    approval_payload: dict[str, Any] | None
    approval_decision: dict[str, Any] | None
    approval_required: bool
    review_id: str | None
    review_status: str | None
    persisted_version_hash: str | None
    persisted_event_ids: Annotated[list[str], operator.add]

    warnings: Annotated[list[str], operator.add]
    errors: Annotated[list[str], operator.add]
    node_trace: Annotated[list[dict[str, Any]], operator.add]


class WorkspaceAgentInput(TypedDict, total=False):
    workspace_id: str | None
    workspace: dict[str, Any] | None
    candidate_artifact: dict[str, Any] | None
    candidate_artifact_path: str | None
    user_message: str
    user_id: str | None
    thread_id: str | None
    allow_pipeline_rerun: bool
    require_approval: bool
    agent_model: str
    max_repair_attempts: int


class WorkspaceAgentOutput(TypedDict, total=False):
    status: str
    final_response: str | None
    proposed_operations: list[dict[str, Any]]
    proposed_workspace: dict[str, Any] | None
    updated_workspace: dict[str, Any] | None
    diff_summary: dict[str, Any] | None
    validation_summary: dict[str, Any] | None
    warnings: list[str]
    errors: list[str]
    approval_required: bool
    review_id: str | None
    review_status: str | None
    persisted_version_hash: str | None
    persisted_event_ids: list[str]
