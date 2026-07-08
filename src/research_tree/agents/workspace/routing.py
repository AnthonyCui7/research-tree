from __future__ import annotations

from typing import Any, Mapping

from langgraph.types import Send


def route_after_rerun_guardrail(state: Mapping[str, Any]) -> str:
    guardrail = state.get("retrieval_guardrail_result")
    if not isinstance(guardrail, Mapping) or not guardrail.get("allowed"):
        return "answer_with_guardrail_rejection"
    if guardrail.get("expensive") or state.get("require_approval"):
        return "maybe_review_expensive_rerun"
    return "rerun_candidate_pipeline"


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
                "candidate_artifact": state.get("candidate_artifact"),
                "diff_summary": state.get("diff_summary") or {},
                "retrieval_guardrail_result": state.get("retrieval_guardrail_result"),
                "validation_round": state.get("validation_round") or 0,
            },
        )
        for validator_name in selected
    ]

