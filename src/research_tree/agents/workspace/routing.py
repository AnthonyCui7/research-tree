from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Send


def route_after_workspace_context(state: Mapping[str, Any]) -> str:
    """The critique reads the context; every other terminal edit constructs."""

    next_action = state.get("next_action")
    action_type = next_action.get("action_type") if isinstance(next_action, Mapping) else None
    if action_type == "critique_workspace":
        return "critique_workspace"
    return "construct_workspace_modification"


def route_after_rerun_guardrail(state: Mapping[str, Any]) -> str:
    """A rerun either becomes a pending review or is refused outright.

    Every pipeline stage republishes the workspace when it completes, so
    starting one is always the user's decision: approval happens through the
    reviews API, never inline in the agent run.
    """

    guardrail = state.get("retrieval_guardrail_result")
    if not isinstance(guardrail, Mapping) or not guardrail.get("allowed"):
        return "answer_with_guardrail_rejection"
    return "persist_rerun_review"


def fan_out_validators_with_send(state: Mapping[str, Any]) -> list[Send] | str:
    selected = [str(name) for name in state.get("selected_validators") or []]
    if not selected:
        return "combine_validation_results"
    return [
        Send(
            "run_validator",
            {
                "validator_name": validator_name,
                "workspace": state.get("workspace"),
                "proposed_workspace": state.get("proposed_workspace"),
                "proposed_operations": state.get("proposed_operations") or [],
                "candidate_pool": state.get("candidate_pool") or [],
                # The merged proposal artifact (visible papers plus
                # session-discovered additions) makes paper-reference checks
                # strict for agent edits.
                "candidate_artifact": state.get("proposal_candidate_artifact")
                or state.get("candidate_artifact"),
                "diff_summary": state.get("diff_summary") or {},
                "retrieval_guardrail_result": state.get("retrieval_guardrail_result"),
                "validation_round": state.get("validation_round") or 0,
            },
        )
        for validator_name in selected
    ]
