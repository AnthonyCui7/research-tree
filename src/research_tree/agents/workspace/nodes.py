from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from uuid import uuid4

from langgraph.types import Command, interrupt

from research_tree.agents.workspace.llm import (
    AGENT_ACTION_PROFILE,
    AGENT_CHAT_PROFILE,
    AGENT_CRITIQUE_PROFILE,
    AGENT_INTENT_PROFILE,
    AgentRequestProfile,
    OpenAIResponsesAgentClient,
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
from research_tree.workspace.enrichment import (
    load_paper_content_context,
)
from research_tree.workspace.operations import apply_workspace_patch_in_memory
from research_tree.workspace.repository import WorkspaceRepository
from research_tree.workspace.serialization import load_candidate_artifact, load_json_artifact
from research_tree.workspace.schemas import paper_database_from_artifact
from research_tree.workspace.similar_papers import (
    DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
    DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
    DEFAULT_SIMILAR_PAPERS_K,
    build_similar_papers,
)
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
        workspace_repository: WorkspaceRepository | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self.llm_client = llm_client or default_workspace_agent_llm_client()
        self.workspace_constructor = workspace_constructor
        self.retrieval_runner = retrieval_runner
        self.workspace_repository = workspace_repository
        self.repo_root = repo_root

    def _request_profile_kwargs(
        self,
        request_profile: AgentRequestProfile,
    ) -> dict[str, AgentRequestProfile]:
        if isinstance(self.llm_client, OpenAIResponsesAgentClient):
            return {"request_profile": request_profile}
        return {}

    def load_workspace(self, state: WorkspaceAgentState) -> dict[str, Any]:
        workspace = state.get("workspace")
        candidate_artifact = state.get("candidate_artifact")
        candidate_artifact_path = state.get("candidate_artifact_path")
        errors: list[str] = []
        if workspace is None:
            workspace_id = state.get("workspace_id")
            if workspace_id and self.workspace_repository is not None:
                workspace = self.workspace_repository.get_current_workspace(
                    str(workspace_id)
                )
            elif workspace_id and Path(str(workspace_id)).is_file():
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
            "agent_run_id": state.get("agent_run_id") or f"agent_run_{uuid4().hex}",
            "allow_pipeline_rerun": bool(state.get("allow_pipeline_rerun", False)),
            "require_approval": bool(state.get("require_approval", True)),
            "agent_model": str(state.get("agent_model") or "gpt-5.6-luna"),
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
            model_name=state.get("agent_model"),
            **self._request_profile_kwargs(AGENT_INTENT_PROFILE),
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
        include_similar_context = _is_similar_paper_adjustment(state) or bool(
            target_paper_ids
        )
        context = build_workspace_chat_context(
            workspace=workspace,
            candidate_artifact=state.get("candidate_artifact"),
            include_similar_papers=include_similar_context,
            max_similar_per_paper=5,
            target_branch_id=target_branch_id,
            target_paper_ids=target_paper_ids,
        )
        content_warnings: list[str] = []
        if self.workspace_repository is not None:
            content_paper_ids = _paper_ids_for_explanation(
                workspace,
                target_branch_id=str(target_branch_id) if target_branch_id else None,
                target_paper_ids=target_paper_ids,
            )
            paper_contents, content_warnings = load_paper_content_context(
                self.workspace_repository,
                workspace_id=_workspace_id(state),
                paper_ids=content_paper_ids,
            )
            context["paper_full_text"] = _bounded_full_text_context(paper_contents)
            context["requested_full_text_paper_ids"] = content_paper_ids
        return {
            "chat_context": context,
            "modification_context": context,
            "similar_papers_context": context.get("similar_papers_context") or {},
            "off_path_papers": context.get("off_path_papers") or [],
            "workspace_summary": build_workspace_summary(workspace),
            "warnings": content_warnings,
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
                    "errors": ["Assistant exceeded max action iterations."],
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
            model_name=state.get("agent_model"),
            **self._request_profile_kwargs(AGENT_ACTION_PROFILE),
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
        answer = self.llm_client.complete_text(
            prompt=prompt,
            model_name=state.get("agent_model"),
            **self._request_profile_kwargs(AGENT_CHAT_PROFILE),
        )
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
            model_name=state.get("agent_model"),
            **self._request_profile_kwargs(AGENT_CRITIQUE_PROFILE),
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
        run_event_id = self._append_agent_run_event(
            state,
            status="failed_guardrail",
            payload={
                "error_message": reason,
                "errors": [reason],
                "retrieval_guardrail_result": guardrail,
            },
            actor_type="system",
            error_message=reason,
            errors=[reason],
        )
        return {
            "status": "failed",
            "final_response": response,
            "errors": [reason],
            "persisted_event_ids": [run_event_id] if run_event_id else [],
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
        event_id = self._append_event(
            state,
            event_type="workspace_candidate_preparation_completed",
            before_hash=state.get("workspace_version_hash"),
            after_hash=state.get("workspace_version_hash"),
            payload={"retrieval_result": result},
            actor_type="system",
        )
        return {
            "candidate_artifact": candidate_artifact,
            "candidate_pool": candidate_pool_from_artifact(candidate_artifact),
            "retrieval_result": result,
            "status": "retrieving",
            "persisted_event_ids": [event_id] if event_id else [],
            "node_trace": [_trace("rerun_candidate_pipeline")],
        }

    def construct_workspace_modification(self, state: WorkspaceAgentState) -> dict[str, Any]:
        next_action = state.get("next_action") or {}
        if _is_similar_paper_adjustment(state):
            return self._adjust_similar_papers(state, next_action)
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
            model=str(state.get("agent_model") or "gpt-5.6-luna"),
        )
        return {
            "proposed_workspace": proposed,
            "status": "constructing",
            "node_trace": [_trace("construct_workspace_modification")],
        }

    def _adjust_similar_papers(
        self,
        state: WorkspaceAgentState,
        next_action: Mapping[str, Any],
    ) -> dict[str, Any]:
        workspace = _required_mapping(state.get("workspace"), "workspace")
        target_ids = _similar_paper_target_ids(workspace, next_action)
        if not target_ids:
            return {
                "proposed_workspace": workspace,
                "status": "failed",
                "errors": ["No workspace papers matched the requested similar-paper scope."],
                "node_trace": [_trace("adjust_similar_papers")],
            }
        policy = _similar_paper_policy(workspace, str(state.get("user_message") or ""))
        paper_database_path = _paper_database_path_for_workspace(
            workspace,
            self.workspace_repository,
            self.repo_root,
        )
        if paper_database_path is None:
            return {
                "proposed_workspace": workspace,
                "status": "failed",
                "errors": [
                    "Similar-paper refresh requires the original candidate paper database."
                ],
                "node_trace": [_trace("adjust_similar_papers")],
            }
        paper_database = paper_database_from_artifact(
            load_json_artifact(paper_database_path)
        )
        proposed, _debug = build_similar_papers(
            workspace=workspace,
            paper_database=paper_database,
            paper_ids=target_ids,
            **policy,
        )
        provenance = proposed.setdefault("provenance", {})
        if isinstance(provenance, dict):
            provenance["similar_papers_policy"] = {
                **policy,
                "target_paper_ids": sorted(target_ids),
            }
        return {
            "proposed_workspace": proposed,
            "status": "constructing",
            "node_trace": [_trace("adjust_similar_papers")],
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
            "persist_pending_review",
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
            goto = "persist_pending_review"
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

    def persist_pending_review(self, state: WorkspaceAgentState) -> dict[str, Any]:
        review_id = state.get("review_id")
        if self.workspace_repository is not None:
            review_id = review_id or f"review_{uuid4().hex}"
        payload = _review_interrupt_payload(state, review_id=review_id)
        persisted_event_ids: list[str] = []
        if self.workspace_repository is not None:
            self.workspace_repository.save_pending_review(
                _workspace_id(state),
                review_id=review_id,
                agent_run_id=str(state.get("agent_run_id") or ""),
                base_workspace_version_hash=str(
                    state.get("workspace_version_hash") or ""
                ),
                user_message=state.get("user_message", ""),
                proposed_workspace=dict(
                    _required_mapping(
                        state.get("proposed_workspace"),
                        "proposed_workspace",
                    )
                ),
                proposed_operations=state.get("proposed_operations") or [],
                diff_summary=state.get("diff_summary") or {},
                validation_summary=state.get("validation_summary") or {},
                interrupt_payload=payload,
            )
            run_event_id = self._append_agent_run_event(
                state,
                status="pending_review",
                payload={"review_id": review_id},
                actor_type="agent",
            )
            if run_event_id:
                persisted_event_ids.append(run_event_id)
        return {
            "approval_payload": payload,
            "approval_required": True,
            "review_id": review_id,
            "review_status": "pending" if review_id else None,
            "status": "awaiting_approval",
            "persisted_event_ids": persisted_event_ids,
            "node_trace": [_trace("persist_pending_review")],
        }

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
            model=str(state.get("agent_model") or "gpt-5.6-luna"),
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
        payload = state.get("approval_payload")
        if not isinstance(payload, Mapping):
            payload = _review_interrupt_payload(
                state,
                review_id=state.get("review_id"),
            )
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
            edited_workspace_dict = dict(edited_workspace)
            updates["proposed_workspace"] = edited_workspace_dict
            if self.workspace_repository is not None and state.get("review_id"):
                result = self.workspace_repository.edit_review_once(
                    _workspace_id(state),
                    str(state["review_id"]),
                    edited_workspace=edited_workspace_dict,
                    target_ids=_operation_target_ids(
                        state.get("proposed_operations") or []
                    ),
                    approval_decision=decision,
                )
                updates["persisted_event_ids"] = result.get("persisted_event_ids") or []
                if not result.get("ok"):
                    error_message = (
                        result.get("error_message")
                        or "edited review payload could not be persisted."
                    )
                    updates["status"] = "failed"
                    updates["errors"] = [str(error_message)]
                    return updates
            else:
                event_id = self._append_event(
                    state,
                    event_type="workspace_patch_edited",
                    before_hash=state.get("workspace_version_hash"),
                    after_hash=workspace_version_hash(edited_workspace_dict),
                    payload={
                        "approval_decision": decision,
                        "diff_summary": state.get("diff_summary") or {},
                        "review_id": state.get("review_id"),
                    },
                    actor_type="user",
                )
                if event_id:
                    updates["persisted_event_ids"] = [event_id]
            updates["review_id"] = None
            updates["review_status"] = "edited"
            updates["approval_payload"] = None
        else:
            updates["errors"] = ["edited review payload did not include a proposed_workspace."]
        return updates

    def apply_patch_in_memory(self, state: WorkspaceAgentState) -> dict[str, Any]:
        if self.workspace_repository is not None and state.get("review_id"):
            result = self.workspace_repository.approve_review_once(
                _workspace_id(state),
                str(state["review_id"]),
                reason=(state.get("next_action") or {}).get("reason")
                or state.get("user_message", "approved workspace agent patch"),
                target_ids=_operation_target_ids(state.get("proposed_operations") or []),
                approval_decision=state.get("approval_decision") or {},
            )
            if not result.get("ok"):
                error_message = (
                    result.get("error_message")
                    or "Workspace review could not be approved."
                )
                return {
                    "status": "failed",
                    "review_status": result.get("status"),
                    "final_response": str(error_message),
                    "errors": [str(error_message)],
                    "persisted_event_ids": result.get("persisted_event_ids") or [],
                    "node_trace": [_trace("apply_patch_in_memory:approval_failed")],
                }
            return {
                "updated_workspace": result.get("updated_workspace"),
                "status": "approved",
                "review_status": result.get("status"),
                "persisted_version_hash": result.get("persisted_version_hash"),
                "persisted_event_ids": result.get("persisted_event_ids") or [],
                "node_trace": [_trace("apply_patch_in_memory")],
            }

        updated_workspace = apply_workspace_patch_in_memory(
            base_workspace=_required_mapping(state.get("workspace"), "workspace"),
            proposed_workspace=_required_mapping(
                state.get("proposed_workspace"),
                "proposed_workspace",
            ),
            validation_summary=state.get("validation_summary"),
        )
        persisted_version_hash = None
        persisted_event_ids: list[str] = []
        if self.workspace_repository is not None:
            workspace_id = _workspace_id(state)
            parent_hash = state.get("workspace_version_hash")
            persisted_version_hash = self.workspace_repository.save_workspace_version(
                workspace_id,
                updated_workspace,
                actor="agent",
                actor_type="agent",
                actor_id="workspace_agent",
                parent_version_hash=parent_hash,
                reason=(state.get("next_action") or {}).get("reason")
                or state.get("user_message", "approved workspace agent patch"),
                agent_run_id=state.get("agent_run_id"),
            )
            event_id = self._append_event(
                state,
                event_type="workspace_patch_approved_applied",
                before_hash=parent_hash,
                after_hash=persisted_version_hash,
                payload={
                    "proposed_operations": state.get("proposed_operations") or [],
                    "diff_summary": state.get("diff_summary") or {},
                    "validation_summary": state.get("validation_summary") or {},
                    "approval_decision": state.get("approval_decision") or {},
                },
                actor_type="user",
            )
            if event_id:
                persisted_event_ids.append(event_id)
            run_event_id = self._append_agent_run_event(
                state,
                status="approved_applied",
                payload={
                    "version_hash": persisted_version_hash,
                    "event_id": event_id,
                },
                actor_type="user",
            )
            if run_event_id:
                persisted_event_ids.append(run_event_id)
        return {
            "updated_workspace": updated_workspace,
            "status": "approved",
            "review_status": "approved_applied" if state.get("review_id") else None,
            "persisted_version_hash": persisted_version_hash,
            "persisted_event_ids": persisted_event_ids,
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
                final_response = "Assistant stopped with errors."
            else:
                final_response = "Assistant completed."
        return {
            "final_response": final_response,
            "status": "completed" if not state.get("errors") else state.get("status", "failed"),
            "node_trace": [_trace("finalize_response")],
        }

    def finalize_rejection(self, state: WorkspaceAgentState) -> dict[str, Any]:
        if self.workspace_repository is not None and state.get("review_id"):
            result = self.workspace_repository.reject_review_once(
                _workspace_id(state),
                str(state["review_id"]),
                reason="user rejected workspace patch",
                target_ids=_operation_target_ids(state.get("proposed_operations") or []),
                approval_decision=state.get("approval_decision") or {},
            )
            if not result.get("ok"):
                error_message = (
                    result.get("error_message")
                    or "Workspace review could not be rejected."
                )
                return {
                    "status": "failed",
                    "review_status": result.get("status"),
                    "final_response": str(error_message),
                    "errors": [str(error_message)],
                    "persisted_event_ids": result.get("persisted_event_ids") or [],
                    "node_trace": [_trace("finalize_rejection:failed")],
                }
            return {
                "status": "rejected",
                "review_status": result.get("status"),
                "final_response": "Workspace change was rejected. No patch was applied.",
                "persisted_event_ids": result.get("persisted_event_ids") or [],
                "node_trace": [_trace("finalize_rejection")],
            }

        persisted_event_ids: list[str] = []
        event_id = self._append_event(
            state,
            event_type="workspace_patch_rejected",
            before_hash=state.get("workspace_version_hash"),
            after_hash=None,
            payload={
                "approval_decision": state.get("approval_decision") or {},
                "proposed_operations": state.get("proposed_operations") or [],
                "diff_summary": state.get("diff_summary") or {},
                "validation_summary": state.get("validation_summary") or {},
            },
            actor_type="user",
        )
        if event_id:
            persisted_event_ids.append(event_id)
        run_event_id = self._append_agent_run_event(
            state,
            status="rejected",
            payload={"event_id": event_id},
            actor_type="user",
        )
        if run_event_id:
            persisted_event_ids.append(run_event_id)
        return {
            "status": "rejected",
            "review_status": "rejected" if state.get("review_id") else None,
            "final_response": "Workspace change was rejected. No patch was applied.",
            "persisted_event_ids": persisted_event_ids,
            "node_trace": [_trace("finalize_rejection")],
        }

    def _append_event(
        self,
        state: WorkspaceAgentState,
        *,
        event_type: str,
        before_hash: str | None,
        after_hash: str | None,
        payload: dict[str, Any],
        actor_type: str = "agent",
        actor_id: str | None = None,
    ) -> str | None:
        if self.workspace_repository is None:
            return None
        return self.workspace_repository.append_workspace_event(
            _workspace_id(state),
            actor=actor_type,
            actor_type=actor_type,
            actor_id=actor_id,
            event_type=event_type,
            target_ids=_operation_target_ids(state.get("proposed_operations") or []),
            before_hash=before_hash,
            after_hash=after_hash,
            payload={
                **payload,
                "agent_run_id": state.get("agent_run_id"),
                "thread_id": state.get("thread_id"),
            },
        )

    def _append_agent_run_event(
        self,
        state: WorkspaceAgentState,
        *,
        status: str,
        payload: dict[str, Any],
        actor_type: str = "system",
        actor_id: str | None = None,
        error_message: str | None = None,
        errors: list[str] | None = None,
    ) -> str | None:
        if self.workspace_repository is None:
            return None
        append_run_event = getattr(
            self.workspace_repository,
            "append_agent_run_event",
            None,
        )
        if append_run_event is None:
            return None
        return append_run_event(
            _workspace_id(state),
            agent_run_id=str(state.get("agent_run_id") or ""),
            status=status,
            actor_type=actor_type,
            actor_id=actor_id,
            error_message=error_message,
            errors=errors,
            payload={
                **payload,
                "thread_id": state.get("thread_id"),
                "workspace_version_hash": state.get("workspace_version_hash"),
            },
        )

    def finalize_validation_failure(self, state: WorkspaceAgentState) -> dict[str, Any]:
        summary = state.get("validation_summary") or {}
        errors = [str(error) for error in summary.get("errors") or []]
        failure_status = (
            "failed_repair_exhausted"
            if int(state.get("repair_attempts", 0))
            >= int(state.get("max_repair_attempts", 2))
            else "failed_validation"
        )
        run_event_id = self._append_agent_run_event(
            state,
            status=failure_status,
            payload={
                "error_message": (
                    "Workspace proposal failed validation and could not be repaired."
                ),
                "errors": errors,
                "validation_summary": summary,
                "review_id": state.get("review_id"),
            },
            actor_type="system",
            error_message=(
                "Workspace proposal failed validation and could not be repaired."
            ),
            errors=errors,
        )
        return {
            "status": "failed",
            "final_response": "Workspace proposal failed validation and could not be repaired.",
            "errors": errors,
            "persisted_event_ids": [run_event_id] if run_event_id else [],
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


def _review_interrupt_payload(
    state: WorkspaceAgentState,
    *,
    review_id: str | None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "type": "workspace_patch_review",
        "question": "Approve, edit, or reject this workspace change?",
        "diff_summary": state.get("diff_summary") or {},
        "proposed_operations": state.get("proposed_operations") or [],
        "validation_summary": state.get("validation_summary") or {},
        "warnings": state.get("warnings") or [],
        "choices": ["approve", "edit", "reject"],
    }
    if review_id:
        payload["review_id"] = review_id
    return payload


def _workspace_topic(state: WorkspaceAgentState) -> str:
    workspace = state.get("workspace")
    if isinstance(workspace, Mapping) and workspace.get("topic"):
        return str(workspace["topic"])
    return "research topic"


def _workspace_id(state: WorkspaceAgentState) -> str:
    if state.get("workspace_id"):
        return str(state["workspace_id"])
    workspace = state.get("workspace")
    if isinstance(workspace, Mapping) and workspace.get("workspace_id"):
        return str(workspace["workspace_id"])
    raise ValueError("workspace_id is required for persistence.")


def _operation_target_ids(operations: list[dict[str, Any]]) -> dict[str, Any]:
    branch_ids: set[str] = set()
    paper_ids: set[str] = set()
    path_ids: set[str] = set()
    operation_types: set[str] = set()
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        operation_types.add(str(operation.get("operation_type") or ""))
        target_ids = operation.get("target_ids")
        if not isinstance(target_ids, Mapping):
            continue
        for key, value in target_ids.items():
            if value is None:
                continue
            values = value if isinstance(value, list) else [value]
            for item in values:
                text = str(item)
                if "paper" in key:
                    paper_ids.add(text)
                elif "path" in key:
                    path_ids.add(text)
                elif "branch" in key:
                    branch_ids.add(text)
    return {
        "operation_types": sorted(item for item in operation_types if item),
        "branch_ids": sorted(branch_ids),
        "paper_ids": sorted(paper_ids),
        "path_ids": sorted(path_ids),
    }


def _paper_ids_for_explanation(
    workspace: Mapping[str, Any],
    *,
    target_branch_id: str | None,
    target_paper_ids: list[str],
) -> list[str]:
    cards = workspace.get("paper_cards")
    visible_ids = set(cards) if isinstance(cards, Mapping) else set()
    selected = {paper_id for paper_id in target_paper_ids if paper_id in visible_ids}
    if not target_branch_id:
        return sorted(selected)

    tree = workspace.get("tree")
    nodes = tree.get("nodes") if isinstance(tree, Mapping) else []
    nodes_by_id = {
        str(node.get("node_id")): node
        for node in nodes or []
        if isinstance(node, Mapping) and node.get("node_id")
    }
    pending = [target_branch_id]
    visited: set[str] = set()
    while pending:
        node_id = pending.pop()
        if node_id in visited:
            continue
        visited.add(node_id)
        node = nodes_by_id.get(node_id)
        if not isinstance(node, Mapping):
            continue
        selected.update(str(item) for item in node.get("primary_paper_ids") or [])
        selected.update(str(item) for item in node.get("secondary_paper_ids") or [])
        pending.extend(str(item) for item in node.get("child_node_ids") or [])
    for path in workspace.get("paper_paths") or []:
        if isinstance(path, Mapping) and str(path.get("branch_node_id")) in visited:
            selected.update(str(item) for item in path.get("paper_ids") or [])
    return sorted(paper_id for paper_id in selected if paper_id in visible_ids)


def _is_similar_paper_adjustment(state: Mapping[str, Any]) -> bool:
    message = str(state.get("user_message") or "").casefold()
    next_action = state.get("next_action")
    instruction = (
        str(next_action.get("modification_instruction") or "").casefold()
        if isinstance(next_action, Mapping)
        else ""
    )
    text = f"{message} {instruction}"
    return "similar paper" in text or "related paper" in text


def _similar_paper_target_ids(
    workspace: Mapping[str, Any],
    next_action: Mapping[str, Any],
) -> set[str]:
    cards = workspace.get("paper_cards")
    visible_ids = set(cards) if isinstance(cards, Mapping) else set()
    requested = {
        str(paper_id)
        for paper_id in next_action.get("target_paper_ids") or []
        if str(paper_id) in visible_ids
    }
    if requested:
        return requested
    branch_id = str(next_action.get("target_branch_id") or "")
    if not branch_id:
        return visible_ids
    tree = workspace.get("tree")
    nodes_by_id = {
        str(node.get("node_id")): node
        for node in ((tree.get("nodes") or []) if isinstance(tree, Mapping) else [])
        if isinstance(node, Mapping) and node.get("node_id")
    }
    branch_ids = {branch_id}
    pending = [branch_id]
    while pending:
        node = nodes_by_id.get(pending.pop())
        if not isinstance(node, Mapping):
            continue
        for child_id in node.get("child_node_ids") or []:
            child_id = str(child_id)
            if child_id not in branch_ids:
                branch_ids.add(child_id)
                pending.append(child_id)
    return {
        paper_id
        for paper_id, card in cards.items()
        if isinstance(card, Mapping)
        and isinstance(card.get("primary_tree_location"), Mapping)
        and str(card["primary_tree_location"].get("node_id") or "") in branch_ids
    }


def _similar_paper_policy(
    workspace: Mapping[str, Any],
    user_message: str,
) -> dict[str, float | int]:
    provenance = workspace.get("provenance")
    prior = (
        provenance.get("similar_papers_policy")
        if isinstance(provenance, Mapping)
        and isinstance(provenance.get("similar_papers_policy"), Mapping)
        else {}
    )
    alpha = float(prior.get("citation_age_exponent") or DEFAULT_SIMILAR_CITATION_AGE_EXPONENT)
    floor = float(prior.get("citation_score_floor") or DEFAULT_SIMILAR_CITATION_SCORE_FLOOR)
    k = int(prior.get("k") or DEFAULT_SIMILAR_PAPERS_K)
    request = user_message.casefold()
    if any(term in request for term in ("newer", "newest", "recent", "recency")):
        alpha = max(alpha, 1.75)
    if any(term in request for term in ("well-known", "well known", "established", "highly cited")):
        floor = max(floor, 25.0)
    return {"k": k, "citation_age_exponent": alpha, "citation_score_floor": floor}


def _paper_database_path_for_workspace(
    workspace: Mapping[str, Any],
    repository: WorkspaceRepository | None,
    repo_root: Path,
) -> Path | None:
    provenance = workspace.get("provenance")
    if isinstance(provenance, Mapping):
        pipeline_run = provenance.get("pipeline_run")
        if isinstance(pipeline_run, Mapping):
            path = _existing_artifact_path(pipeline_run.get("paper_database_json"), repo_root)
            if path is not None:
                return path

    workspace_id = str(workspace.get("workspace_id") or "")
    if not workspace_id or repository is None:
        return None
    for run in repository.list_pipeline_runs(workspace_id):
        if run.get("status") not in {"completed", "completed_with_warnings"}:
            continue
        artifacts = run.get("artifacts")
        if not isinstance(artifacts, Mapping):
            continue
        path = _existing_artifact_path(artifacts.get("paper_database_json"), repo_root)
        if path is not None:
            return path
    return None


def _existing_artifact_path(value: Any, repo_root: Path) -> Path | None:
    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve()
    return path if path.is_file() else None


def _bounded_full_text_context(
    contents: Mapping[str, Mapping[str, Any]],
    *,
    max_characters: int = 180_000,
) -> dict[str, dict[str, Any]]:
    """Bound a model request without ever disguising truncation as full text."""

    total = 0
    result: dict[str, dict[str, Any]] = {}
    for paper_id, content in contents.items():
        text = str(content.get("full_text") or "")
        if total + len(text) > max_characters:
            result[paper_id] = {
                "status": "omitted_context_limit",
                "source_status": content.get("status"),
                "source_url": content.get("source_url"),
                "full_text": "",
                "error": "Full text was not sent because this branch exceeds the agent context safety limit.",
            }
            continue
        total += len(text)
        result[paper_id] = {
            "status": content.get("status"),
            "source_url": content.get("source_url"),
            "page_count": content.get("page_count"),
            "truncated": bool(content.get("truncated")),
            "full_text": text,
        }
    return result


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
