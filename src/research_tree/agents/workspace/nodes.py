from __future__ import annotations

import json
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Callable, Literal, Mapping
from uuid import uuid4

from langgraph.config import get_stream_writer
from langgraph.types import Command
from pydantic import ValidationError

from research_tree.agents.workspace.cache import needs_similar_paper_context
from research_tree.agents.workspace.llm import (
    AGENT_CRITIQUE_PROFILE,
    AGENT_SKEPTIC_PROFILE,
    AGENT_TOOL_LOOP_PROFILE,
    AgentRequestProfile,
    OpenAIResponsesAgentClient,
    ToolCall,
    WorkspaceAgentLlmClient,
    default_workspace_agent_llm_client,
)
from research_tree.agents.workspace.models import (
    PipelineRerunRequest,
    ProposalSkepticNotes,
    WorkspaceCritique,
    WorkspaceValidationSummary,
)
from research_tree.agents.workspace.prompts import (
    build_agent_loop_prompt,
    build_proposal_skeptic_prompt,
    build_workspace_critique_prompt,
)
from research_tree.agents.workspace.tools import (
    ALL_TOOLS,
    ToolContext,
    run_tool,
    tool_schemas,
    web_search_enabled,
)
from research_tree.agents.workspace.state import WorkspaceAgentState
from research_tree.llm import DEFAULT_MODEL
from research_tree.paths import data_root
from research_tree.retrieval.pipeline_args import validate_pipeline_rerun_request
from research_tree.retrieval.semantic_scholar import paper_from_semantic_scholar
from research_tree.retrieval.text import looks_like_survey
from research_tree.workspace.construction import (
    DERIVED_PAPER_CARD_FIELDS,
    construct_workspace,
)
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
from research_tree.workspace.operations import (
    WorkspacePatchError,
    operation_target_ids as _operation_target_ids,
    apply_structured_workspace_patch,
    remove_visible_paper_operation,
)
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

# Model turns per request. Each round is one LLM call plus its tools, so this
# bounds both cost and latency for a single user message. Twelve rounds fit a
# realistic search -> resolve -> read -> propose sequence; eight did not.
MAX_TOOL_ROUNDS = 12

# Editing and critique both need the heavy workspace context, so they route
# through the node that builds it; a rerun does not.
_TERMINAL_TOOL_NODES = {
    "propose_workspace_edit": "build_workspace_context",
    "propose_pipeline_rerun": "prepare_retrieval_rerun",
    "critique_workspace": "build_workspace_context",
}


class WorkspaceAgentNodes:
    def __init__(
        self,
        *,
        llm_client: WorkspaceAgentLlmClient | None = None,
        workspace_constructor: Callable[..., dict[str, Any]] = construct_workspace,
        workspace_repository: WorkspaceRepository | None = None,
        repo_root: Path = REPO_ROOT,
    ) -> None:
        self.llm_client = llm_client or default_workspace_agent_llm_client()
        self.workspace_constructor = workspace_constructor
        self.workspace_repository = workspace_repository
        self.repo_root = repo_root

    def _request_profile_kwargs(
        self,
        request_profile: AgentRequestProfile,
    ) -> dict[str, AgentRequestProfile]:
        if isinstance(self.llm_client, OpenAIResponsesAgentClient):
            return {"request_profile": request_profile}
        return {}

    def begin_turn(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """Clear the previous turn's accumulated channels on a reused thread.

        `None` resets each channel (see `state.reset_on_none`); everything else
        turn-scoped is overwritten by `load_workspace`.
        """

        return {
            "warnings": None,
            "errors": None,
            "node_trace": None,
            "validation_results": None,
            "persisted_event_ids": None,
        }

    def load_workspace(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """Load the workspace and start the turn from a clean run state.

        A conversation thread reuses one checkpointed state across user turns,
        so the workspace is refetched — an approval may have moved current.json
        since the last turn — and every per-turn key is reset here.
        `session_discovered_papers` survives on purpose: papers found in an
        earlier turn stay proposable in later ones.
        """

        workspace = state.get("workspace")
        workspace_id = state.get("workspace_id")
        candidate_artifact = state.get("candidate_artifact")
        candidate_artifact_path = state.get("candidate_artifact_path")
        errors: list[str] = []
        if workspace_id and self.workspace_repository is not None:
            workspace = self.workspace_repository.get_current_workspace(
                str(workspace_id)
            )
        elif workspace is None and workspace_id and Path(str(workspace_id)).is_file():
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
            # One agent run per user message, even on a reused thread.
            "agent_run_id": f"agent_run_{uuid4().hex}",
            "allow_pipeline_rerun": bool(state.get("allow_pipeline_rerun", False)),
            "agent_model": str(state.get("agent_model") or DEFAULT_MODEL),
            "approval_required": False,
            "repair_attempts": 0,
            "max_repair_attempts": int(state.get("max_repair_attempts", 2)),
            "validation_round": 0,
            "transcript_items": [],
            "tool_rounds": 0,
            "next_action": None,
            "semantic_scholar_calls": 0,
            "chat_context": None,
            "similar_papers_context": {},
            "off_path_papers": [],
            "retrieval_request": None,
            "retrieval_guardrail_result": None,
            "proposed_workspace": None,
            "proposed_operations": [],
            "diff_summary": None,
            "validation_summary": None,
            "proposal_candidate_artifact": None,
            "skeptic_notes": [],
            "approval_payload": None,
            "approval_decision": None,
            "review_id": None,
            "review_status": None,
            "final_response": None,
            "errors": errors,
            "node_trace": [_trace("load_workspace")],
            "conversation_history": state.get("conversation_history") or [],
        }

    def agent_loop(
        self,
        state: WorkspaceAgentState,
    ) -> Command[
        Literal[
            "execute_tools",
            "build_workspace_context",
            "prepare_retrieval_rerun",
            "finalize_response",
        ]
    ]:
        """Ask the model what to do next; it answers by calling a tool or not.

        This replaced a pair of LLM calls that classified an intent and then
        planned an action from a fixed taxonomy. Tool selection is the same
        decision made once, by a model that can look things up first.
        """

        _report_progress({"kind": "thinking"})
        rounds = int(state.get("tool_rounds", 0)) + 1
        max_rounds = int(state.get("max_tool_rounds", MAX_TOOL_ROUNDS))
        transcript = list(state.get("transcript_items") or [])
        if not transcript:
            transcript = [
                {
                    "role": "user",
                    "content": build_agent_loop_prompt(
                        user_message=state.get("user_message", ""),
                        conversation_history=state.get("conversation_history") or [],
                        workspace_summary=state.get("workspace_summary") or {},
                    ),
                }
            ]

        # On the last round, offer no tools. Running out of budget should end in
        # an answer built from what was already gathered, not an error.
        out_of_budget = rounds >= max_rounds
        if out_of_budget:
            transcript = [
                *transcript,
                {
                    "role": "user",
                    "content": (
                        "You have no tool calls left. Answer now using what you "
                        "have gathered, and say plainly what you could not check."
                    ),
                },
            ]

        turn = self.llm_client.complete_with_tools(
            input_items=transcript,
            tools=(
                []
                if out_of_budget
                else tool_schemas(
                    include_web_search=self._web_search_available(),
                    # A rerun the caller has not allowed is refused by the
                    # guardrail either way; not offering the tool keeps the
                    # model from spending a turn on a proposal that cannot land.
                    include_pipeline_rerun=bool(state.get("allow_pipeline_rerun", False)),
                )
            ),
            model_name=state.get("agent_model"),
            **self._request_profile_kwargs(AGENT_TOOL_LOOP_PROFILE),
        )
        # Every output item is replayed verbatim next turn: with store=false a
        # reasoning model needs its own encrypted reasoning back alongside the
        # calls it made.
        transcript = [*transcript, *turn.output_items]
        for item in turn.output_items:
            if item.get("type") == "web_search_call":
                _report_progress(_web_search_progress(item))

        # A name that is not a tool is answered as one in `execute_tools`; it
        # does not stop a real terminal call beside it from being honoured.
        terminal = next(
            (
                call
                for call in turn.tool_calls
                if call.name in ALL_TOOLS and ALL_TOOLS[call.name].terminal
            ),
            None,
        )

        update: dict[str, Any] = {
            "transcript_items": transcript,
            "tool_rounds": rounds,
            "status": "planning",
            "node_trace": [_trace("agent_loop")],
        }
        if terminal is not None and not out_of_budget:
            next_action = _next_action_from_tool_call(terminal, state)
            mistake = _unknown_removal_ids(next_action, state)
            if mistake is None:
                update["next_action"] = next_action
                return Command(update=update, goto=_TERMINAL_TOOL_NODES[terminal.name])
            # A removal is all or nothing, and a model copying forty-character
            # ids gets one wrong now and then. The call is answered like any
            # other tool's, so the model corrects it inside the turn, within
            # the same budget of rounds, instead of the turn ending on a typo.
            update["transcript_items"] = [
                *transcript,
                {
                    "type": "function_call_output",
                    "call_id": terminal.call_id,
                    "output": json.dumps(mistake),
                },
            ]
            return Command(update=update, goto="execute_tools")
        if turn.tool_calls and not out_of_budget:
            return Command(update=update, goto="execute_tools")

        if not turn.output_text:
            # No words and no calls: the answer was cut off, or there was none.
            # Reporting that as a completed turn showed the reader a stock
            # sentence in place of whatever had gone wrong.
            update["final_response"] = "The model returned no answer to that. Try asking again."
            update["status"] = "failed"
            update["errors"] = ["the model's turn carried no text and no tool call"]
            return Command(update=update, goto="finalize_response")
        update["final_response"] = turn.output_text
        update["status"] = "completed"
        if out_of_budget:
            update["warnings"] = [
                "The assistant reached its tool-call limit for this message."
            ]
        return Command(update=update, goto="finalize_response")

    def execute_tools(
        self,
        state: WorkspaceAgentState,
    ) -> Command[Literal["agent_loop"]]:
        """Run the read tools the model asked for, one at a time.

        Strictly sequential: Semantic Scholar enforces roughly one request per
        second across every endpoint, process-wide.
        """

        context = ToolContext(
            workspace=_required_mapping(state.get("workspace"), "workspace"),
            workspace_id=_workspace_id(state),
            repository=self.workspace_repository,
            repo_root=self.repo_root,
            discovered_papers=dict(state.get("session_discovered_papers") or {}),
            semantic_scholar_calls=int(state.get("semantic_scholar_calls", 0)),
        )
        outputs: list[dict[str, Any]] = []
        executed: list[str] = []
        for call in _pending_tool_calls(state):
            _report_progress(_tool_progress(call, state))
            outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call["call_id"],
                    "output": run_tool(call["name"], context, call["arguments"]),
                }
            )
            executed.append(call["name"])
        return Command(
            update={
                "transcript_items": [*(state.get("transcript_items") or []), *outputs],
                "session_discovered_papers": context.discovered_papers,
                "semantic_scholar_calls": context.semantic_scholar_calls,
                "node_trace": [_trace(f"execute_tools:{','.join(executed)}")],
            },
            goto="agent_loop",
        )

    def _web_search_available(self) -> bool:
        # The built-in tool is executed by OpenAI, so it only exists on the
        # live client.
        return isinstance(self.llm_client, OpenAIResponsesAgentClient) and web_search_enabled()

    def build_workspace_context(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """Assemble the heavy context the editing and critique nodes need.

        This runs only on the paths that use it. It downloads nothing, but it
        does read every relevant paper's stored full text and rank similar
        papers, which is wasted work for a question the model can answer by
        calling a read tool.

        The node is cached (see `graph.py`), so it returns only the context
        it built. Which node reads it is decided by the edge after it: a
        routing decision inside these writes would be replayed from the cache.
        """

        _report_progress({"kind": "stage", "stage": "reading_workspace"})
        workspace = _required_mapping(state.get("workspace"), "workspace")
        next_action = state.get("next_action") or {}
        target_branch_id = next_action.get("target_branch_id")
        target_paper_ids = sorted(set(next_action.get("target_paper_ids") or []))
        similar_papers_context = (
            _similar_papers_context_for_scope(
                workspace=workspace,
                paper_ids=_similar_paper_target_ids(workspace, {
                    "target_branch_id": target_branch_id,
                    "target_paper_ids": target_paper_ids,
                }),
                repository=self.workspace_repository,
                repo_root=self.repo_root,
            )
            if needs_similar_paper_context(state)
            else {}
        )
        context = build_workspace_chat_context(
            workspace=workspace,
            candidate_artifact=state.get("candidate_artifact"),
            include_similar_papers=False,
            max_similar_per_paper=5,
            target_branch_id=target_branch_id,
            target_paper_ids=target_paper_ids,
            similar_papers_context=similar_papers_context,
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
            "similar_papers_context": similar_papers_context,
            "off_path_papers": context.get("off_path_papers") or [],
            "workspace_summary": build_workspace_summary(workspace),
            "warnings": content_warnings,
            "node_trace": [_trace("build_workspace_context")],
        }

    def critique_workspace(self, state: WorkspaceAgentState) -> dict[str, Any]:
        _report_progress({"kind": "stage", "stage": "critiquing"})
        prompt = build_workspace_critique_prompt(
            user_message=state.get("user_message", ""),
            conversation_history=state.get("conversation_history") or [],
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
        }

    def prepare_retrieval_rerun(self, state: WorkspaceAgentState) -> dict[str, Any]:
        next_action = state.get("next_action") or {}
        raw_request = next_action.get("retrieval_request")
        if not isinstance(raw_request, Mapping):
            raw_request = {
                "topic": _workspace_topic(state),
                "max_candidates": None,
                "alpha": None,
            }
        raw_request = {
            **dict(raw_request),
            "topic": raw_request.get("topic") or _workspace_topic(state),
            "reason": raw_request.get("reason")
            or next_action.get("reason")
            or "More candidate papers are needed for the requested workspace action.",
        }
        try:
            request = PipelineRerunRequest.model_validate(raw_request)
        except ValidationError as error:
            # The tool schemas are not strict, so the model can hand over a
            # number where text belongs. That is a request to refuse with a
            # reason, the way the guardrail refuses one, not a crashed turn.
            fields = ", ".join(
                ".".join(str(part) for part in item.get("loc") or ()) or "request"
                for item in error.errors()
            )
            return {
                "retrieval_request": {},
                "retrieval_guardrail_result": {
                    "allowed": False,
                    "normalized_args": {},
                    "rejection_reason": f"the rerun request was malformed ({fields})",
                    "warnings": [],
                    "expensive": False,
                    "prior_defaults": {},
                    "new_values": {},
                },
                "status": "retrieving",
                "node_trace": [_trace("prepare_retrieval_rerun:malformed")],
            }
        return {
            "retrieval_request": request.model_dump(),
            "status": "retrieving",
            "node_trace": [_trace("prepare_retrieval_rerun")],
        }

    def validate_rerun_args(self, state: WorkspaceAgentState) -> dict[str, Any]:
        already_refused = state.get("retrieval_guardrail_result")
        if isinstance(already_refused, Mapping) and already_refused.get("allowed") is False:
            return {"node_trace": [_trace("validate_rerun_args:skipped")]}
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

    def persist_rerun_review(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """Save a rerun as a pending review the user approves or rejects.

        Approving it triggers the pipeline through the same reviews API that
        applies workspace patches, so there is one approval path in the product
        rather than two.
        """

        guardrail = state.get("retrieval_guardrail_result") or {}
        request = state.get("retrieval_request") or {}
        stage = str(request.get("stage") or "candidates")
        reason = str(request.get("reason") or "")
        message_to_user = str(
            (state.get("next_action") or {}).get("message_to_user") or ""
        ).strip()
        review_id = state.get("review_id") or f"review_{uuid4().hex}"
        payload = {
            "type": "pipeline_rerun_approval",
            "review_id": review_id,
            "question": f"Rerun the {stage} stage of the pipeline?",
            # What is approved is the stage, so that is what is shown. The
            # guardrail's other numbers never reach the run, and a card that
            # listed them described a build nobody would get.
            "stage": stage,
            "reason": reason,
            "warnings": guardrail.get("warnings") or [],
            "choices": ["approve", "reject"],
        }
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
                proposed_workspace={},
                proposed_operations=[],
                diff_summary={},
                validation_summary={},
                interrupt_payload=payload,
                review_type="pipeline_rerun",
                pipeline_rerun={"stage": stage, "reason": reason},
            )
            run_event_id = self._append_agent_run_event(
                state,
                status="pending_review",
                payload={"review_id": review_id, "pipeline_rerun_stage": stage},
                actor_type="agent",
            )
            if run_event_id:
                persisted_event_ids.append(run_event_id)
        return {
            "approval_payload": payload,
            "approval_required": True,
            "review_id": review_id,
            "review_status": "pending",
            "status": "awaiting_approval",
            "final_response": message_to_user
            or (
                f"Rerunning the {stage} stage would {reason or 'refresh this workspace'}. "
                "Approve it and I will start the run."
            ),
            "persisted_event_ids": persisted_event_ids,
            "node_trace": [_trace("persist_rerun_review")],
        }

    def construct_workspace_modification(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """Build the proposed workspace for the edit kind the model declared.

        Routing follows the tool call's explicit `edit_kind`, never message
        wording: a removal deletes exactly the ids the model named, a
        similar-paper refresh recomputes recommendations, and everything else
        goes to the construction model.
        """

        _report_progress({"kind": "stage", "stage": "constructing"})
        next_action = state.get("next_action") or {}
        edit_kind = str(next_action.get("edit_kind") or "structural")
        workspace = _required_mapping(state.get("workspace"), "workspace")
        candidate_artifact, artifact_warnings = self._modification_candidate_artifact(
            state
        )
        update: dict[str, Any] = {"proposal_candidate_artifact": candidate_artifact}
        if artifact_warnings:
            update["warnings"] = artifact_warnings

        if edit_kind == "remove_papers":
            return {**update, **_propose_paper_removal(workspace, next_action)}
        if edit_kind == "refresh_similar_papers":
            return {**update, **self._adjust_similar_papers(state, next_action)}

        try:
            proposed = self.workspace_constructor(
                candidate_artifact=candidate_artifact,
                base_workspace=state.get("workspace"),
                construction_mode="agent_modify_workspace",
                agent_instruction=next_action.get("modification_instruction")
                or state.get("user_message", ""),
                target_branch_id=next_action.get("target_branch_id"),
                target_paper_ids=next_action.get("target_paper_ids") or [],
                similar_papers_context=state.get("similar_papers_context") or {},
                run_metadata={
                    "user_message": state.get("user_message", ""),
                    "add_paper_ids": next_action.get("add_paper_ids") or [],
                },
                model=str(state.get("agent_model") or DEFAULT_MODEL),
            )
        except ValueError as error:
            return {**update, **_unreadable_draft(workspace, "construct_workspace_modification", error)}
        return {
            **update,
            "proposed_workspace": proposed,
            "status": "constructing",
            "node_trace": [_trace("construct_workspace_modification")],
        }

    def _modification_candidate_artifact(
        self,
        state: WorkspaceAgentState,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """The candidate set an edit may draw papers from.

        Visible workspace papers, plus every Semantic Scholar paper the model
        discovered this conversation — always the provider's recorded
        payloads, never metadata the model wrote. All discoveries ride along,
        not only the ones named in `add_paper_ids`: a terminal call that
        forgot the field must not silently strip the paper the instruction
        names from the proposal (observed live — the edit came back as pure
        provenance churn). The same artifact goes to the constructor, the
        repair call, and the validators, so a paper outside it can neither
        enter nor survive a proposal.
        """

        artifact = _workspace_only_candidate_artifact(
            state.get("workspace"),
            state.get("candidate_artifact"),
        )
        next_action = state.get("next_action") or {}
        add_ids = [str(pid) for pid in next_action.get("add_paper_ids") or []]
        discovered = state.get("session_discovered_papers") or {}
        if artifact is None or (not add_ids and not discovered):
            return artifact, []
        known_ids = {
            str(paper.get("paper_id"))
            for key in ("non_survey_papers", "survey_papers")
            for paper in artifact.get(key) or []
            if isinstance(paper, Mapping)
        }
        warnings: list[str] = []
        for paper_id in add_ids:
            if paper_id not in known_ids and not isinstance(
                discovered.get(paper_id), Mapping
            ):
                warnings.append(
                    f"Paper {paper_id!r} was not fetched from Semantic Scholar in "
                    "this conversation, so the edit could not offer it."
                )
        for payload in discovered.values():
            if not isinstance(payload, Mapping):
                continue
            candidate = paper_from_semantic_scholar(dict(payload)).to_json()
            candidate_id = str(candidate.get("paper_id"))
            if candidate_id in known_ids:
                continue
            known_ids.add(candidate_id)
            # Survey-ness from the title alone. Semantic Scholar's 'Review'
            # publication type is noisy on methods papers (FacTool carries it),
            # and a false survey label makes materialization silently delete
            # the very paper the user asked to add.
            candidate["is_survey"] = looks_like_survey(str(candidate.get("title") or ""))
            key = "survey_papers" if candidate.get("is_survey") else "non_survey_papers"
            artifact.setdefault(key, []).append(candidate)
        return artifact, warnings

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
        try:
            proposed, _debug = build_similar_papers(
                workspace=workspace,
                paper_database=paper_database,
                paper_ids=target_ids,
                **policy,
            )
        except Exception as error:
            return {
                "proposed_workspace": workspace,
                "status": "failed",
                "final_response": "Similar-paper refresh failed before producing a workspace change.",
                "errors": [str(error)],
                "node_trace": [_trace("adjust_similar_papers:failed")],
            }
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
        _report_progress({"kind": "stage", "stage": "validating"})
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
            "skeptic_review_proposal",
            "repair_workspace_proposal",
            "finalize_validation_failure",
            "finalize_response",
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
        if state.get("errors"):
            goto = "finalize_response"
        elif not errors and _proposal_has_no_changes(state):
            goto = "finalize_response"
        elif not errors:
            goto = "skeptic_review_proposal"
        elif int(state.get("repair_attempts", 0)) < int(state.get("max_repair_attempts", 2)):
            goto = "repair_workspace_proposal"
        else:
            goto = "finalize_validation_failure"
        update: dict[str, Any] = {
            "validation_summary": summary,
            "warnings": warnings,
            "status": "failed" if state.get("errors") else "validating",
            "node_trace": [_trace("combine_validation_results")],
        }
        if goto == "finalize_response" and not state.get("errors"):
            update["final_response"] = "I could not find a workspace change to propose."
        return Command(
            update=update,
            goto=goto,
        )

    def skeptic_review_proposal(self, state: WorkspaceAgentState) -> dict[str, Any]:
        """One second-reader call that tries to refute the validated proposal.

        Deterministic code chains it after validation; its objections ride on
        the review card so the human decides with both sides in view. It is
        enrichment, not a gate: a failed call never blocks the proposal.
        """

        _report_progress({"kind": "stage", "stage": "skeptic"})
        prompt = build_proposal_skeptic_prompt(
            user_message=state.get("user_message", ""),
            instruction=str(
                (state.get("next_action") or {}).get("modification_instruction") or ""
            ),
            diff_summary=state.get("diff_summary") or {},
            proposed_operations=state.get("proposed_operations") or [],
            workspace_summary=state.get("workspace_summary") or {},
        )
        try:
            notes = self.llm_client.complete_structured(
                prompt=prompt,
                response_model=ProposalSkepticNotes,
                model_name=state.get("agent_model"),
                **self._request_profile_kwargs(AGENT_SKEPTIC_PROFILE),
            )
        except Exception:
            return {
                "skeptic_notes": [],
                "node_trace": [_trace("skeptic_review_proposal:skipped")],
            }
        objections = [
            str(objection).strip()
            for objection in notes.objections
            if str(objection).strip()
        ][:2]
        return {
            "skeptic_notes": objections,
            "node_trace": [_trace("skeptic_review_proposal")],
        }

    def persist_pending_review(self, state: WorkspaceAgentState) -> dict[str, Any]:
        _report_progress({"kind": "stage", "stage": "saving_review"})
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
        message_to_user = str(
            (state.get("next_action") or {}).get("message_to_user") or ""
        ).strip()
        return {
            "approval_payload": payload,
            "approval_required": True,
            "review_id": review_id,
            "review_status": "pending" if review_id else None,
            "status": "awaiting_approval",
            "final_response": message_to_user
            or "I prepared a workspace change for your review. Approve it to apply it.",
            "persisted_event_ids": persisted_event_ids,
            "node_trace": [_trace("persist_pending_review")],
        }

    def repair_workspace_proposal(self, state: WorkspaceAgentState) -> dict[str, Any]:
        _report_progress({"kind": "stage", "stage": "repairing"})
        validation_summary = state.get("validation_summary") or {}
        next_action = state.get("next_action") or {}
        candidate_artifact = state.get("proposal_candidate_artifact")
        if candidate_artifact is None:
            candidate_artifact, _ = self._modification_candidate_artifact(state)
        try:
            proposed = self.workspace_constructor(
                candidate_artifact=candidate_artifact,
                base_workspace=state.get("workspace"),
                construction_mode="workspace_repair",
                agent_instruction=next_action.get("modification_instruction")
                or state.get("user_message", ""),
                target_branch_id=next_action.get("target_branch_id"),
                target_paper_ids=next_action.get("target_paper_ids") or [],
                similar_papers_context=state.get("similar_papers_context") or {},
                run_metadata={
                    "user_message": state.get("user_message", ""),
                    "add_paper_ids": next_action.get("add_paper_ids") or [],
                    "proposed_workspace": state.get("proposed_workspace") or {},
                    "validation_errors": validation_summary.get("errors") or [],
                },
                model=str(state.get("agent_model") or DEFAULT_MODEL),
            )
        except ValueError as error:
            return {
                "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
                **_unreadable_draft(
                    _required_mapping(state.get("workspace"), "workspace"),
                    "repair_workspace_proposal",
                    error,
                ),
            }
        return {
            "proposed_workspace": proposed,
            "repair_attempts": int(state.get("repair_attempts", 0)) + 1,
            "status": "constructing",
            "node_trace": [_trace("repair_workspace_proposal")],
        }

    def finalize_response(self, state: WorkspaceAgentState) -> dict[str, Any]:
        final_response = state.get("final_response")
        if not final_response:
            final_response = (
                "Assistant stopped with errors."
                if state.get("errors")
                else "Assistant completed."
            )
        return {
            "final_response": final_response,
            "status": "completed" if not state.get("errors") else state.get("status", "failed"),
            "node_trace": [_trace("finalize_response")],
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
        return self.workspace_repository.append_agent_run_event(
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
        "skeptic_notes": [str(note) for note in state.get("skeptic_notes") or []],
        "warnings": state.get("warnings") or [],
        "choices": ["approve", "edit", "reject"],
    }
    if review_id:
        payload["review_id"] = review_id
    return payload


def _next_action_from_tool_call(
    call: ToolCall,
    state: WorkspaceAgentState,
) -> dict[str, Any]:
    """Translate a terminal tool call into the shape the action nodes read."""

    arguments = call.arguments
    if call.name == "propose_pipeline_rerun":
        return {
            "action_type": "prepare_retrieval_rerun",
            "reason": str(arguments.get("reason") or ""),
            "message_to_user": str(arguments.get("message_to_user") or ""),
            "retrieval_request": {
                # Anything the model supplied is forwarded, including keys the
                # tool schema does not define, so the guardrail stays the single
                # authority on what a rerun may change.
                **arguments,
                "topic": arguments.get("topic") or _workspace_topic(state),
                "reason": arguments.get("reason") or "",
                "stage": arguments.get("stage") or "candidates",
            },
        }
    if call.name == "critique_workspace":
        return {
            "action_type": "critique_workspace",
            "reason": str(arguments.get("focus") or "workspace critique"),
        }
    # The tool schemas are not strict, so every field is coerced to the shape
    # the action nodes read: a list of ids handed over as one string is one
    # id, not a list of its characters.
    return {
        "action_type": "construct_workspace_modification",
        "reason": str(arguments.get("instruction") or ""),
        "modification_instruction": str(arguments.get("instruction") or "") or None,
        "edit_kind": str(arguments.get("edit_kind") or "structural"),
        "message_to_user": str(arguments.get("message_to_user") or ""),
        "target_branch_id": str(arguments.get("target_branch_id") or "") or None,
        "target_paper_ids": _id_list(arguments.get("target_paper_ids")),
        "add_paper_ids": _id_list(arguments.get("add_paper_ids")),
    }


def _unknown_removal_ids(
    next_action: Mapping[str, Any], state: WorkspaceAgentState
) -> dict[str, Any] | None:
    """What to tell the model when a removal names papers the workspace does not show."""

    if next_action.get("edit_kind") != "remove_papers":
        return None
    workspace = state.get("workspace")
    cards = workspace.get("paper_cards") if isinstance(workspace, Mapping) else None
    visible = [str(paper_id) for paper_id in cards] if isinstance(cards, Mapping) else []
    unknown = [
        paper_id for paper_id in next_action.get("target_paper_ids") or [] if paper_id not in visible
    ]
    if not unknown:
        return None
    return {
        "error": "These target_paper_ids are not visible workspace papers. Nothing was proposed.",
        "unknown_ids": unknown,
        "closest_visible_ids": {
            paper_id: get_close_matches(paper_id, visible, n=1, cutoff=0.8) for paper_id in unknown
        },
        "note": "Call propose_workspace_edit again with exact ids.",
    }


def _id_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value] if value.strip() else []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _pending_tool_calls(state: WorkspaceAgentState) -> list[dict[str, Any]]:
    """Read the calls from the last model turn that still need results."""

    answered = {
        str(item.get("call_id"))
        for item in state.get("transcript_items") or []
        if isinstance(item, Mapping) and item.get("type") == "function_call_output"
    }
    calls: list[dict[str, Any]] = []
    for item in state.get("transcript_items") or []:
        if not isinstance(item, Mapping) or item.get("type") != "function_call":
            continue
        call_id = str(item.get("call_id") or item.get("id") or "")
        if call_id in answered:
            continue
        try:
            arguments = json.loads(item.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        calls.append(
            {
                "call_id": call_id,
                "name": str(item.get("name") or ""),
                "arguments": arguments if isinstance(arguments, dict) else {},
            }
        )
    return calls


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


def _similar_papers_context_for_scope(
    *,
    workspace: Mapping[str, Any],
    paper_ids: set[str],
    repository: WorkspaceRepository | None,
    repo_root: Path,
) -> dict[str, Any]:
    cards = workspace.get("paper_cards")
    if not isinstance(cards, Mapping) or not paper_ids:
        return {}

    paper_database_by_id: dict[str, Any] = {}
    paper_database_path = _paper_database_path_for_workspace(
        workspace,
        repository,
        repo_root,
    )
    if paper_database_path is not None:
        paper_database_by_id = {
            paper.paper_id: paper
            for paper in paper_database_from_artifact(
                load_json_artifact(paper_database_path)
            )
        }

    context: dict[str, Any] = {}
    for paper_id in sorted(paper_ids):
        card = cards.get(paper_id)
        if not isinstance(card, Mapping):
            continue
        similar_papers = [
            _similar_paper_context_item(item, paper_database_by_id)
            for item in card.get("similar_papers") or []
            if isinstance(item, Mapping)
        ]
        if similar_papers:
            context[paper_id] = {
                "source_workspace_paper": {
                    "paper_id": card.get("paper_id") or paper_id,
                    "title": card.get("title"),
                    "primary_tree_location": card.get("primary_tree_location"),
                },
                "similar_papers": similar_papers,
            }
    return context


def _similar_paper_context_item(
    item: Mapping[str, Any],
    paper_database_by_id: Mapping[str, Any],
) -> dict[str, Any]:
    paper_id = str(item.get("paper_id") or "")
    paper = paper_database_by_id.get(paper_id)
    if paper is not None:
        return {
            "paper_id": paper.paper_id,
            "title": paper.title,
            "authors": paper.authors,
            "year": paper.year,
            "publication_date": paper.publication_date,
            "venue": paper.venue,
            "abstract": paper.abstract,
            "tldr": _candidate_tldr(paper),
            "citation_count": paper.citation_count,
            "primary_link": paper.primary_link,
            "arxiv_link": paper.arxiv_link,
            "s2_link": paper.s2_link,
            "provider": item.get("provider"),
            "rank": item.get("rank"),
            "age_adjusted_citation_score": item.get("age_adjusted_citation_score"),
            "similarity_score": item.get("similarity_score"),
            "cross_encoder_score": item.get("cross_encoder_score"),
        }
    return {
        "paper_id": paper_id,
        "title": item.get("title"),
        "authors": item.get("authors") or [],
        "year": item.get("year"),
        "publication_date": item.get("publication_date"),
        "venue": item.get("venue"),
        "citation_count": item.get("citation_count"),
        "primary_link": item.get("primary_link"),
        "arxiv_link": item.get("arxiv_link"),
        "s2_link": item.get("s2_link"),
        "provider": item.get("provider"),
        "rank": item.get("rank"),
        "age_adjusted_citation_score": item.get("age_adjusted_citation_score"),
        "similarity_score": item.get("similarity_score"),
        "cross_encoder_score": item.get("cross_encoder_score"),
    }


def _proposal_has_no_changes(state: Mapping[str, Any]) -> bool:
    diff_summary = state.get("diff_summary")
    if not isinstance(diff_summary, Mapping):
        return False
    if int(diff_summary.get("operation_count") or 0) != 0:
        return False
    return (
        diff_summary.get("workspace_version_hash_before")
        == diff_summary.get("workspace_version_hash_after")
    )


def _candidate_tldr(paper: Any) -> str | None:
    direct = getattr(paper, "tldr", None)
    if isinstance(direct, str):
        return direct
    metadata = getattr(paper, "semantic_scholar_metadata", None)
    if isinstance(metadata, Mapping):
        tldr = metadata.get("tldr")
        if isinstance(tldr, Mapping):
            text = tldr.get("text")
            return str(text) if text else None
        if isinstance(tldr, str):
            return tldr
    return None


def _workspace_only_candidate_artifact(
    workspace: Any,
    candidate_artifact: Any,
) -> dict[str, Any] | None:
    if not isinstance(workspace, Mapping):
        return None
    cards = workspace.get("paper_cards")
    if not isinstance(cards, Mapping):
        return None
    visible_ids = {str(paper_id) for paper_id in cards}
    source = candidate_artifact if isinstance(candidate_artifact, Mapping) else {}
    artifact: dict[str, Any] = {
        "schema_version": source.get("schema_version"),
        "topic": source.get("topic") or workspace.get("topic"),
        "workspace": source.get("workspace"),
        "candidate_set_purpose": (
            "Workspace-only prompt context; hidden candidate papers omitted."
        ),
        "candidate_pool_order": source.get("candidate_pool_order"),
        "citation_age_exponent": source.get("citation_age_exponent"),
        "non_survey_papers": [],
        "survey_papers": [],
    }
    source_by_id: dict[str, Mapping[str, Any]] = {}
    if isinstance(candidate_artifact, Mapping):
        for key in ("non_survey_papers", "survey_papers"):
            for item in candidate_artifact.get(key) or []:
                if isinstance(item, Mapping) and item.get("paper_id"):
                    source_by_id[str(item["paper_id"])] = item
    for paper_id, card in cards.items():
        if str(paper_id) not in visible_ids or not isinstance(card, Mapping):
            continue
        source_paper = source_by_id.get(str(paper_id))
        payload = dict(source_paper) if source_paper is not None else dict(card)
        payload["paper_id"] = payload.get("paper_id") or str(paper_id)
        payload["is_survey"] = bool(payload.get("is_survey")) or _card_is_survey(card)
        # Same derived payloads the editing prompt drops: the model selects
        # papers by id, and deterministic post-processing restores their
        # metadata from the real artifact.
        for derived_field in DERIVED_PAPER_CARD_FIELDS:
            payload.pop(derived_field, None)
        if _card_is_survey(card):
            artifact["survey_papers"].append(payload)
        else:
            artifact["non_survey_papers"].append(payload)
    # Survey anchors are referenced by the root and branches, sometimes without
    # a paper card of their own. Validation treats an anchor outside the
    # artifact as an error, so an anchor the workspace already carries must not
    # invalidate every later edit.
    included_ids = {
        str(paper.get("paper_id"))
        for key in ("non_survey_papers", "survey_papers")
        for paper in artifact[key]
    }
    for anchor_id in _survey_anchor_ids(workspace):
        if anchor_id in included_ids:
            continue
        source_paper = source_by_id.get(anchor_id)
        payload = (
            dict(source_paper)
            if source_paper is not None
            else {"paper_id": anchor_id, "title": anchor_id}
        )
        payload["paper_id"] = payload.get("paper_id") or anchor_id
        payload["is_survey"] = True
        for derived_field in DERIVED_PAPER_CARD_FIELDS:
            payload.pop(derived_field, None)
        artifact["survey_papers"].append(payload)
        included_ids.add(anchor_id)
    # Discarded candidates ride along too: the workspace document references
    # them, validation requires every referenced id to be a candidate, and
    # keeping them offerable lets an edit resurrect a discarded paper by id.
    for item in workspace.get("discarded_candidates") or []:
        if not isinstance(item, Mapping) or not item.get("paper_id"):
            continue
        paper_id = str(item["paper_id"])
        if paper_id in included_ids:
            continue
        source_paper = source_by_id.get(paper_id)
        payload = dict(source_paper) if source_paper is not None else dict(item)
        payload["paper_id"] = paper_id
        for derived_field in DERIVED_PAPER_CARD_FIELDS:
            payload.pop(derived_field, None)
        key = "survey_papers" if payload.get("is_survey") else "non_survey_papers"
        artifact[key].append(payload)
        included_ids.add(paper_id)
    return artifact


def _survey_anchor_ids(workspace: Mapping[str, Any]) -> list[str]:
    anchors: list[str] = []
    root = workspace.get("root")
    if isinstance(root, Mapping):
        anchors.extend(
            str(paper_id)
            for paper_id in root.get("survey_anchor_paper_ids") or []
            if paper_id
        )
    tree = workspace.get("tree")
    nodes = tree.get("nodes") if isinstance(tree, Mapping) else []
    for node in nodes or []:
        if isinstance(node, Mapping) and node.get("survey_anchor_paper_id"):
            anchors.append(str(node["survey_anchor_paper_id"]))
    seen: set[str] = set()
    return [anchor for anchor in anchors if not (anchor in seen or seen.add(anchor))]


def _propose_paper_removal(
    workspace: Mapping[str, Any],
    next_action: Mapping[str, Any],
) -> dict[str, Any]:
    """Deterministically remove exactly the papers the model named.

    All-or-nothing: removals are destructive, so an unresolvable id fails the
    whole request with a clear message instead of guessing at a partial edit.
    The loop hands a wrong id back to the model before it gets here, so this
    is what is left when there were no ids at all.
    """

    cards = workspace.get("paper_cards")
    visible = set(cards) if isinstance(cards, Mapping) else set()
    requested = sorted(
        {str(paper_id) for paper_id in next_action.get("target_paper_ids") or []}
    )
    unknown = [paper_id for paper_id in requested if paper_id not in visible]
    if not requested or unknown:
        detail = (
            "no target_paper_ids were given"
            if not requested
            else "these ids are not visible workspace papers: " + ", ".join(unknown)
        )
        return {
            "proposed_workspace": dict(workspace),
            "status": "failed",
            "final_response": f"I could not propose that removal: {detail}. "
            "No change was proposed.",
            "errors": [f"paper removal failed: {detail}"],
            "node_trace": [
                _trace("construct_workspace_modification:remove_papers_failed")
            ],
        }
    operations = [
        remove_visible_paper_operation(paper_id=paper_id) for paper_id in requested
    ]
    try:
        proposed = apply_structured_workspace_patch(
            base_workspace=workspace,
            operations=operations,
        )
    except WorkspacePatchError as error:
        return {
            "proposed_workspace": dict(workspace),
            "status": "failed",
            "final_response": "I could not apply that removal to the workspace. "
            "No change was proposed.",
            "errors": [f"paper removal failed: {error}"],
            "node_trace": [
                _trace("construct_workspace_modification:remove_papers_failed")
            ],
        }
    return {
        "proposed_workspace": proposed,
        "status": "constructing",
        "node_trace": [_trace("construct_workspace_modification:remove_papers")],
    }


def _unreadable_draft(
    workspace: Mapping[str, Any], node_name: str, error: ValueError
) -> dict[str, Any]:
    """The model's answer could not be read as an edit: not JSON, or a field of
    the wrong kind. That ends the turn as a failure with its reason, the way a
    removal that names an unknown paper does, rather than as a crash."""

    return {
        "proposed_workspace": dict(workspace),
        "status": "failed",
        "final_response": (
            "I could not turn the model's answer into a workspace change. Try asking again."
        ),
        "errors": [f"the draft could not be read: {error}"],
        "node_trace": [_trace(f"{node_name}:unreadable")],
    }


def _card_is_survey(card: Mapping[str, Any]) -> bool:
    return "survey" in str(card.get("paper_role") or "").casefold()


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
    try:
        alpha = float(prior.get("citation_age_exponent") or DEFAULT_SIMILAR_CITATION_AGE_EXPONENT)
        floor = float(prior.get("citation_score_floor") or DEFAULT_SIMILAR_CITATION_SCORE_FLOOR)
        k = int(prior.get("k") or DEFAULT_SIMILAR_PAPERS_K)
    except (TypeError, ValueError, OverflowError):
        # The policy sits in a document a reader can edit and nothing checks
        # its numbers. One that is not a number is no policy, not a reason for
        # every refresh of that workspace to fail.
        alpha, floor, k = (
            DEFAULT_SIMILAR_CITATION_AGE_EXPONENT,
            DEFAULT_SIMILAR_CITATION_SCORE_FLOOR,
            DEFAULT_SIMILAR_PAPERS_K,
        )
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
    """A pipeline artifact the document names, if it is one this process may read.

    The path comes out of the workspace's provenance, which a review edit can
    rewrite, so only a file under the data directory counts: that is where
    every pipeline artifact lives, and nothing else on disk is an answer.
    """

    if not value:
        return None
    path = Path(str(value))
    if not path.is_absolute():
        path = repo_root / path
    path = path.resolve()
    if not path.is_relative_to(data_root().resolve()):
        return None
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


def _required_mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping.")
    return dict(value)


def _trace(node_name: str) -> dict[str, Any]:
    return {"node": node_name}


# --- progress -----------------------------------------------------------------
#
# A turn can run for minutes. The nodes that take time say what they are about
# to do, and a caller streaming the graph (`run.py`) forwards those events to
# the client. `thinking` is a model turn; `tool` names one read tool and what
# it is being asked about; `stage` names a deterministic step of an edit or
# critique. The copy lives in the frontend; this is only the fact.

PROGRESS_SUBJECT_CHARACTERS = 160


def _report_progress(event: dict[str, Any]) -> None:
    try:
        writer = get_stream_writer()
    except RuntimeError:
        # A node called outside a graph run (unit tests) has no listener.
        return
    writer(event)


def _tool_progress(call: Mapping[str, Any], state: WorkspaceAgentState) -> dict[str, Any]:
    name = str(call.get("name") or "")
    arguments = call.get("arguments") or {}
    workspace = state.get("workspace")
    workspace = workspace if isinstance(workspace, Mapping) else {}
    subject: str | None = None
    if name in {"search_workspace", "search_semantic_scholar"}:
        subject = str(arguments.get("query") or "")
    elif name in {"get_paper", "get_paper_full_text"}:
        paper_id = str(arguments.get("paper_id") or "")
        cards = workspace.get("paper_cards")
        card = cards.get(paper_id) if isinstance(cards, Mapping) else None
        subject = _title_or(card, paper_id)
    elif name == "get_semantic_scholar_paper":
        paper_id = str(arguments.get("paper_id") or "")
        found = (state.get("session_discovered_papers") or {}).get(paper_id)
        subject = _title_or(found, paper_id)
    elif name == "get_branch":
        branch_id = str(arguments.get("branch_id") or "")
        tree = workspace.get("tree")
        nodes = tree.get("nodes") if isinstance(tree, Mapping) else None
        branch = next(
            (
                node
                for node in nodes or []
                if isinstance(node, Mapping) and str(node.get("node_id")) == branch_id
            ),
            None,
        )
        subject = (
            str(branch.get("label"))
            if isinstance(branch, Mapping) and branch.get("label")
            else branch_id
        )
    return {"kind": "tool", "name": name, "subject": _bounded_subject(subject)}


def _web_search_progress(item: Mapping[str, Any]) -> dict[str, Any]:
    action = item.get("action")
    query = action.get("query") if isinstance(action, Mapping) else None
    return {
        "kind": "tool",
        "name": "web_search",
        "subject": _bounded_subject(str(query) if query else None),
    }


def _title_or(record: Any, fallback: str) -> str:
    if isinstance(record, Mapping) and record.get("title"):
        return str(record["title"])
    return fallback


def _bounded_subject(subject: str | None) -> str | None:
    text = (subject or "").strip()
    if not text:
        return None
    if len(text) > PROGRESS_SUBJECT_CHARACTERS:
        return text[: PROGRESS_SUBJECT_CHARACTERS - 1].rstrip() + "…"
    return text
