from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Mapping

from langgraph.types import Command, interrupt

from research_tree.agents.workspace.llm import (
    WorkspaceAgentLlmClient,
    default_workspace_agent_llm_client,
)
from research_tree.agents.workspace.models import (
    AgentIntent,
    AgentNextAction,
    PipelineRerunRequest,
    WorkspaceChatResponse,
    WorkspaceCritique,
    WorkspaceValidationSummary,
)
from research_tree.agents.workspace.prompts import (
    build_intent_prompt,
    build_next_action_prompt,
    build_workspace_chat_prompt,
    build_workspace_critique_prompt,
)
from research_tree.agents.workspace.state import WorkspaceAgentState
from research_tree.retrieval.candidate_preparation import (
    run_workspace_candidate_preparation_pipeline,
)
from research_tree.retrieval.pipeline_args import (
    pipeline_config_from_normalized_args,
    validate_pipeline_rerun_request,
)
from research_tree.workspace.construction import construct_workspace
from research_tree.workspace.context import (
    build_workspace_chat_context,
    build_workspace_summary,
    candidate_pool_from_artifact,
    workspace_version_hash,
)
from research_tree.workspace.diff import derive_operations_and_diff_summary
from research_tree.workspace.operations import apply_workspace_patch_in_memory
from research_tree.workspace.serialization import load_candidate_artifact, load_json_artifact
from research_tree.workspace.validators import (
    run_workspace_validator,
    select_workspace_validators,
)


REPO_ROOT = Path(__file__).resolve().parents[4]


class WorkspaceAgentNodes:
    def __init__(
        self,
        *,
        llm_client: WorkspaceAgentLlmClient | None = None,
        workspace_constructor: Callable[..., dict[str, Any]] = construct_workspace,
        retrieval_runner: Callable[[Any], dict[str, Any]] = run_workspace_candidate_preparation_pipeline,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self.llm_client = llm_client or default_workspace_agent_llm_client()
        self.workspace_constructor = workspace_constructor
        self.retrieval_runner = retrieval_runner
        self.repo_root = repo_root

    def load_workspace(self, state: WorkspaceAgentState) -> dict[str, Any]:
        workspace = state.get("workspace")
        candidate_artifact = state.get("candidate_artifact")
        candidate_artifact_path = state.get("candidate_artifact_path")
        errors: list[str] = []
        if workspace is None:
            workspace_id = state.get("workspace_id")
            if workspace_id and Path(str(workspace_id)).is_file():
                payload = load_json_artifact(Path(str(workspace_id)))
                if isinstance(payload, dict):
                    workspace = payload
            if workspace is None:
                errors.append("workspace or local workspace JSON path is required.")

        if candidate_artifact is None and candidate_artifact_path:
            candidate_artifact = load_candidate_artifact(Path(candidate_artifact_path))

        workspace_hash = (
            workspace_version_hash(workspace) if isinstance(workspace, Mapping) else None
        )
        workspace_summary = (
            build_workspace_summary(workspace) if isinstance(workspace, Mapping) else None
        )
        return {
            "workspace": dict(workspace) if isinstance(workspace, Mapping) else None,
            "workspace_id": (
                str(workspace.get("workspace_id"))
                if isinstance(workspace, Mapping) and workspace.get("workspace_id")
                else state.get("workspace_id")
            ),
            "workspace_version_hash": workspace_hash,
            "workspace_summary": workspace_summary,
            "candidate_artifact": candidate_artifact,
            "candidate_pool": candidate_pool_from_artifact(candidate_artifact),
            "status": "loaded" if not errors else "failed",
            "allow_pipeline_rerun": bool(state.get("allow_pipeline_rerun", False)),
            "require_approval": bool(state.get("require_approval", True)),
            "approval_required": False,
            "repair_attempts": int(state.get("repair_attempts", 0)),
            "max_repair_attempts": int(state.get("max_repair_attempts", 2)),
            "validation_round": int(state.get("validation_round", 0)),
            "action_iteration_count": int(state.get("action_iteration_count", 0)),
            "max_action_iterations": int(state.get("max_action_iterations", 6)),
            "warnings": [],
            "errors": errors,
            "node_trace": [_trace("load_workspace")],
            "messages": [{"role": "user", "content": state.get("user_message", "")}],
        }

    def classify_intent(
        self,
        state: WorkspaceAgentState,
    ) -> Command[Literal["build_workspace_context"]]:
        prompt = build_intent_prompt(
            user_message=state.get("user_message", ""),
            workspace_summary=state.get("workspace_summary"),
        )
        intent = self.llm_client.complete_structured(
            prompt=prompt,
            response_model=AgentIntent,
        )
        return Command(
            update={
                "intent": intent.model_dump(),
                "status": "planning",
                "node_trace": [_trace("classify_intent")],
            },
            goto="build_workspace_context",
        )

    def build_workspace_context(self, state: WorkspaceAgentState) -> dict[str, Any]:
        workspace = _required_mapping(state.get("workspace"), "workspace")
        intent = state.get("intent") or {}
        next_action = state.get("next_action") or {}
        target_branch_id = (
            next_action.get("target_branch_id") or intent.get("target_branch_id")
        )
        target_paper_ids = sorted(
            set(intent.get("target_paper_ids") or [])
            | set(next_action.get("target_paper_ids") or [])
        )
        context = build_workspace_chat_context(
            workspace=workspace,
            candidate_artifact=state.get("candidate_artifact"),
            include_similar_papers=True,
            max_similar_per_paper=10,
            target_branch_id=target_branch_id,
            target_paper_ids=target_paper_ids,
        )
        return {
            "chat_context": context,
            "modification_context": context,
            "similar_papers_context": context.get("similar_papers_context") or {},
            "off_path_papers": context.get("off_path_papers") or [],
            "workspace_summary": build_workspace_summary(workspace),
            "node_trace": [_trace("build_workspace_context")],
        }

    def plan_next_action(
        self,
        state: WorkspaceAgentState,
    ) -> Command[
        Literal[
            "answer_chat",
            "critique_workspace",
            "construct_workspace_modification",
            "prepare_retrieval_rerun",
            "repair_workspace_proposal",
            "finalize_response",
        ]
    ]:
        iteration = int(state.get("action_iteration_count", 0)) + 1
        if iteration > int(state.get("max_action_iterations", 6)):
            return Command(
                update={
                    "status": "failed",
                    "errors": ["workspace agent exceeded max action iterations."],
                    "node_trace": [_trace("plan_next_action")],
                },
                goto="finalize_response",
            )

        prompt = build_next_action_prompt(
            user_message=state.get("user_message", ""),
            intent=state.get("intent") or {},
            workspace_context=state.get("chat_context") or {},
            action_history=state.get("action_history") or [],
            warnings=state.get("warnings") or [],
            validation_summary=state.get("validation_summary"),
        )
        action = self.llm_client.complete_structured(
            prompt=prompt,
            response_model=AgentNextAction,
        )
        action_type = action.action_type
        if state.get("retrieval_result") and action_type == "prepare_retrieval_rerun":
            intent = state.get("intent") or {}
            action_type = (
                "construct_workspace_modification"
                if intent.get("requires_workspace_modification")
                else "finalize"
            )
        goto = "finalize_response" if action_type == "finalize" else action_type
        return Command(
            update={
                "next_action": action.model_dump(),
                "action_history": [
                    {
                        "action_type": action_type,
                        "reason": action.reason,
                        "iteration": iteration,
                    }
                ],
                "action_iteration_count": iteration,
                "status": "planning",
                "node_trace": [_trace("plan_next_action")],
            },
            goto=goto,
        )

    def answer_chat(self, state: WorkspaceAgentState) -> dict[str, Any]:
        prompt = build_workspace_chat_prompt(
            user_message=state.get("user_message", ""),
            workspace_context=state.get("chat_context") or {},
        )
        answer = self.llm_client.complete_text(prompt=prompt)
        return {
            "final_response": answer,
            "status": "completed",
            "node_trace": [_trace("answer_chat")],
            "messages": [{"role": "assistant", "content": answer}],
        }

    def critique_workspace(self, state: WorkspaceAgentState) -> dict[str, Any]:
        prompt = build_workspace_critique_prompt(
            user_message=state.get("user_message", ""),
            workspace_context=state.get("chat_context") or {},
        )
        critique = self.llm_client.complete_structured(
            prompt=prompt,
            response_model=WorkspaceCritique,
        )
        final_response = _format_critique(critique)
        return {
            "final_response": final_response,
            "status": "completed",
            "node_trace": [_trace("critique_workspace")],
            "messages": [{"role": "assistant", "content": final_response}],
        }

    def prepare_retrieval_rerun(self, state: WorkspaceAgentState) -> dict[str, Any]:
        next_action = state.get("next_action") or {}
        raw_request = next_action.get("retrieval_request")
        if not isinstance(raw_request, Mapping):
            raw_request = {
                "topic": _workspace_topic(state),
                "max_candidates": None,
                "alpha": None,
                "reason": next_action.get("reason")
                or "More candidate papers are needed for the requested workspace action.",
            }
        request = PipelineRerunRequest.model_validate(raw_request)
        return {
            "retrieval_request": request.model_dump(),
            "status": "retrieving",
            "node_trace": [_trace("prepare_retrieval_rerun")],
        }

    def validate_rerun_args(self, state: WorkspaceAgentState) -> dict[str, Any]:
        result = validate_pipeline_rerun_request(
            state.get("retrieval_request") or {},
            repo_root=self.repo_root,
            allow_pipeline_rerun=bool(state.get("allow_pipeline_rerun", False)),
        )
        return {
            "retrieval_guardrail_result": result,
            "warnings": result.get("warnings") or [],
            "node_trace": [_trace("validate_rerun_args")],
        }

    def answer_with_guardrail_rejection(self, state: WorkspaceAgentState) -> dict[str, Any]:
        guardrail = state.get("retrieval_guardrail_result") or {}
        reason = guardrail.get("rejection_reason") or "retrieval rerun rejected by guardrails"
        response = f"I cannot rerun retrieval for this request: {reason}."
        return {
            "status": "failed",
            "final_response": response,
            "errors": [reason],
            "node_trace": [_trace("answer_with_guardrail_rejection")],
        }

    def maybe_review_expensive_rerun(
        self,
        state: WorkspaceAgentState,
    ) -> Command[Literal["rerun_candidate_pipeline", "finalize_rejection"]]:
        guardrail = state.get("retrieval_guardrail_result") or {}
        payload = {
            "type": "pipeline_rerun_approval",
            "question": "Approve retrieving more papers?",
            "normalized_args": guardrail.get("normalized_args") or {},
            "warnings": guardrail.get("warnings") or [],
            "reason": (state.get("retrieval_request") or {}).get("reason") or "",
        }
        decision = interrupt(payload)
        approved = _decision_choice(decision) == "approve"
        return Command(
            update={
                "approval_decision": _decision_dict(decision),
                "approval_payload": payload,
                "node_trace": [_trace("maybe_review_expensive_rerun")],
            },
            goto="rerun_candidate_pipeline" if approved else "finalize_rejection",
        )

    def rerun_candidate_pipeline(self, state: WorkspaceAgentState) -> dict[str, Any]:
        guardrail = _required_mapping(
            state.get("retrieval_guardrail_result"),
            "retrieval_guardrail_result",
        )
        config = pipeline_config_from_normalized_args(guardrail.get("normalized_args") or {})
        candidate_artifact = self.retrieval_runner(config)
        result = {
            "reason": (state.get("retrieval_request") or {}).get("reason"),
            "normalized_args": guardrail.get("normalized_args") or {},
            "warnings": guardrail.get("warnings") or [],
            "run_dir": candidate_artifact.get("run_dir") if isinstance(candidate_artifact, Mapping) else None,
        }
        return {
            "candidate_artifact": candidate_artifact,
            "candidate_pool": candidate_pool_from_artifact(candidate_artifact),
            "retrieval_result": result,
            "status": "retrieving",
            "node_trace": [_trace("rerun_candidate_pipeline")],
        }

    def construct_workspace_modification(self, state: WorkspaceAgentState) -> dict[str, Any]:
        next_action = state.get("next_action") or {}
        proposed = self.workspace_constructor(
            candidate_artifact=state.get("candidate_artifact"),
            base_workspace=state.get("workspace"),
            construction_mode="agent_modify_workspace",
            agent_instruction=next_action.get("modification_instruction")
            or state.get("user_message", ""),
            target_branch_id=next_action.get("target_branch_id"),
            target_paper_ids=next_action.get("target_paper_ids") or [],
            similar_papers_context=state.get("similar_papers_context") or {},
            run_metadata={"user_message": state.get("user_message", "")},
        )
        return {
            "proposed_workspace": proposed,
            "status": "constructing",
            "node_trace": [_trace("construct_workspace_modification")],
        }

    def derive_operations_and_diff(self, state: WorkspaceAgentState) -> dict[str, Any]:
        operations, diff_summary, warnings = derive_operations_and_diff_summary(
            workspace=_required_mapping(state.get("workspace"), "workspace"),
            proposed_workspace=_required_mapping(
                state.get("proposed_workspace"),
                "proposed_workspace",
            ),
        )
        return {
            "proposed_operations": operations,
            "diff_summary": diff_summary,
            "warnings": warnings,
            "node_trace": [_trace("derive_operations_and_diff")],
        }

    def select_validators(self, state: WorkspaceAgentState) -> dict[str, Any]:
        validation_round = int(state.get("validation_round", 0)) + 1
        selected = select_workspace_validators(
            proposed_operations=state.get("proposed_operations") or [],
            diff_summary=state.get("diff_summary") or {},
        )
        return {
            "selected_validators": selected,
            "validation_round": validation_round,
            "status": "validating",
            "node_trace": [_trace("select_validators")],
        }

    def fan_out_validators_with_Send(self, state: WorkspaceAgentState) -> dict[str, Any]:
        return {"node_trace": [_trace("fan_out_validators_with_Send")]}

    def run_validator(self, state: Mapping[str, Any]) -> dict[str, Any]:
        validation_round = int(state.get("validation_round", 0))
        result = run_workspace_validator(
            validator_name=str(state.get("validator_name") or ""),
            payload=state,
        )
        result["validation_round"] = validation_round
        return {
            "validation_results": [result],
            "node_trace": [_trace(f"run_validator:{result['validator_name']}")],
        }

    def combine_validation_results(
        self,
        state: WorkspaceAgentState,
    ) -> Command[
        Literal[
            "human_review_proposal",
            "repair_workspace_proposal",
            "finalize_validation_failure",
        ]
    ]:
        validation_round = int(state.get("validation_round", 0))
        results = [
            result
            for result in state.get("validation_results") or []
            if int(result.get("validation_round", -1)) == validation_round
        ]
        errors = [
            error
            for result in results
            for error in result.get("errors", [])
        ]
        warnings = [
            warning
            for result in results
            for warning in result.get("warnings", [])
        ]
        summary = WorkspaceValidationSummary(
            valid=not errors,
            error_count=len(errors),
            warning_count=len(warnings),
            errors=errors,
            warnings=warnings,
            validators_run=[str(result.get("validator_name")) for result in results],
            repair_attempts=int(state.get("repair_attempts", 0)),
            validation_round=validation_round,
        ).model_dump()
        if not errors:
            goto = "human_review_proposal"
        elif int(state.get("repair_attempts", 0)) < int(state.get("max_repair_attempts", 2)):
            goto = "repair_workspace_proposal"
        else:
            goto = "finalize_validation_failure"
        return Command(
            update={
                "validation_summary": summary,
                "warnings": warnings,
                "status": "validating",
                "node_trace": [_trace("combine_validation_results")],
            },
            goto=goto,
        )

    def repair_workspace_proposal(self, state: WorkspaceAgentState) -> dict[str, Any]:
        validation_summary = state.get("validation_summary") or {}
        next_action = state.get("next_action") or {}
        proposed = self.workspace_constructor(
            candidate_artifact=state.get("candidate_artifact"),
            base_workspace=state.get("workspace"),
            construction_mode="workspace_repair",
            agent_instruction=next_action.get("modification_instruction")
            or state.get("user_message", ""),
            target_branch_id=next_action.get("target_branch_id"),
            target_paper_ids=next_action.get("target_paper_ids") or [],
            similar_papers_context=state.get("similar_papers_context") or {},
            run_metadata={
                "user_message": state.get("user_message", ""),
                "proposed_workspace": state.get("proposed_workspace") or {},
                "validation_errors": validation_summary.get("errors") or [],
                "operation_history": state.get("action_history") or [],
            },
        )
        return {
            "proposed_workspace": proposed,
            "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
            "status": "constructing",
            "node_trace": [_trace("repair_workspace_proposal")],
        }

    def human_review_proposal(
        self,
        state: WorkspaceAgentState,
    ) -> Command[
        Literal[
            "apply_patch_in_memory",
            "validate_user_edited_patch",
            "finalize_rejection",
        ]
    ]:
        payload = {
            "type": "workspace_patch_review",
            "question": "Approve, edit, or reject this workspace change?",
            "diff_summary": state.get("diff_summary") or {},
            "proposed_operations": state.get("proposed_operations") or [],
            "validation_summary": state.get("validation_summary") or {},
            "warnings": state.get("warnings") or [],
            "choices": ["approve", "edit", "reject"],
        }
        decision = interrupt(payload)
        choice = _decision_choice(decision)
        if choice == "approve":
            goto = "apply_patch_in_memory"
        elif choice == "edit":
            goto = "validate_user_edited_patch"
        else:
            goto = "finalize_rejection"
        return Command(
            update={
                "approval_payload": payload,
                "approval_decision": _decision_dict(decision),
                "approval_required": True,
                "status": "awaiting_approval",
                "node_trace": [_trace("human_review_proposal")],
            },
            goto=goto,
        )

    def validate_user_edited_patch(self, state: WorkspaceAgentState) -> dict[str, Any]:
        decision = state.get("approval_decision") or {}
        edited_workspace = decision.get("proposed_workspace") or decision.get("workspace")
        updates: dict[str, Any] = {
            "status": "validating",
            "node_trace": [_trace("validate_user_edited_patch")],
        }
        if isinstance(edited_workspace, Mapping):
            updates["proposed_workspace"] = dict(edited_workspace)
        else:
            updates["errors"] = ["edited review payload did not include a proposed_workspace."]
        return updates

    def apply_patch_in_memory(self, state: WorkspaceAgentState) -> dict[str, Any]:
        updated_workspace = apply_workspace_patch_in_memory(
            base_workspace=_required_mapping(state.get("workspace"), "workspace"),
            proposed_workspace=_required_mapping(
                state.get("proposed_workspace"),
                "proposed_workspace",
            ),
            validation_summary=state.get("validation_summary"),
        )
        return {
            "updated_workspace": updated_workspace,
            "status": "approved",
            "node_trace": [_trace("apply_patch_in_memory")],
        }

    def finalize_response(self, state: WorkspaceAgentState) -> dict[str, Any]:
        final_response = state.get("final_response")
        if not final_response:
            if state.get("updated_workspace"):
                final_response = "Workspace update approved and applied in memory."
            elif state.get("retrieval_result"):
                final_response = "Candidate retrieval completed and workspace context was refreshed."
            elif state.get("errors"):
                final_response = "Workspace agent stopped with errors."
            else:
                final_response = "Workspace agent completed."
        return {
            "final_response": final_response,
            "status": "completed" if not state.get("errors") else state.get("status", "failed"),
            "node_trace": [_trace("finalize_response")],
        }

    def finalize_rejection(self, state: WorkspaceAgentState) -> dict[str, Any]:
        return {
            "status": "rejected",
            "final_response": "Workspace change was rejected. No patch was applied.",
            "node_trace": [_trace("finalize_rejection")],
        }

    def finalize_validation_failure(self, state: WorkspaceAgentState) -> dict[str, Any]:
        summary = state.get("validation_summary") or {}
        return {
            "status": "failed",
            "final_response": "Workspace proposal failed validation and could not be repaired.",
            "errors": [str(error) for error in summary.get("errors") or []],
            "node_trace": [_trace("finalize_validation_failure")],
        }


def _format_critique(critique: WorkspaceCritique) -> str:
    if not critique.findings:
        return critique.summary
    lines = [critique.summary, ""]
    for finding in critique.findings:
        lines.append(
            f"- {finding.severity}: {finding.finding_type} - {finding.explanation}"
        )
    return "\n".join(lines)


def _workspace_topic(state: WorkspaceAgentState) -> str:
    workspace = state.get("workspace")
    if isinstance(workspace, Mapping) and workspace.get("topic"):
        return str(workspace["topic"])
    return "research topic"


def _decision_choice(decision: Any) -> str:
    if decision is True:
        return "approve"
    if decision is False or decision is None:
        return "reject"
    if isinstance(decision, str):
        return decision.casefold()
    if isinstance(decision, Mapping):
        return str(decision.get("choice") or decision.get("decision") or "reject").casefold()
    return "reject"


def _decision_dict(decision: Any) -> dict[str, Any]:
    if isinstance(decision, Mapping):
        return dict(decision)
    return {"choice": _decision_choice(decision)}


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping.")
    return dict(value)


def _trace(node_name: str) -> dict[str, Any]:
    return {"node": node_name}
