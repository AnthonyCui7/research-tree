from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, TypeVar


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from langgraph.types import Command
from pydantic import BaseModel

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.models import (
    AgentIntent,
    AgentNextAction,
    WorkspaceCritique,
    WorkspaceCritiqueFinding,
)
from research_tree.retrieval.candidate_preparation import PipelineConfig
from research_tree.workspace.context import workspace_version_hash
from research_tree.workspace.schemas import (
    CandidatePaperMetadata,
    candidate_papers_from_artifact,
)
from research_tree.workspace.similar_papers import (
    ScoredSimilarPaperCandidate,
    build_similar_papers,
)


DEFAULT_OUTPUT_PATH = (
    Path(__file__).resolve().parent
    / "output"
    / "workspace_agent_adversarial_report.json"
)
StructuredModelT = TypeVar("StructuredModelT", bound=BaseModel)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run adversarial deterministic Research Tree workspace-agent scenarios "
            "and write an LLM-readable JSON report."
        )
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_PATH,
        help=f"Report path. Default: {DEFAULT_OUTPUT_PATH}",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        help="Also print the report JSON to stdout.",
    )
    args = parser.parse_args(argv)

    report = build_adversarial_report()
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if args.print:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Wrote workspace agent adversarial report: {args.output_json}")
    return 0 if report["overall_passed"] else 1


def build_adversarial_report() -> dict[str, Any]:
    workspace = _workspace()
    candidate_artifact = _candidate_artifact()
    cases = {
        "chat_explain": _run_chat_case(workspace, candidate_artifact),
        "critique_too_flat": _run_critique_case(workspace, candidate_artifact),
        "remake_less_papers_pipeline": _run_less_papers_pipeline_case(
            workspace,
            candidate_artifact,
        ),
        "guardrail_reject_algorithm_change": _run_guardrail_rejection_case(
            workspace,
            candidate_artifact,
        ),
        "invalid_proposal_repair": _run_repair_case(workspace, candidate_artifact),
        "edited_review_revalidation": _run_edited_review_case(
            workspace,
            candidate_artifact,
        ),
        "reject_review_no_apply": _run_reject_case(workspace, candidate_artifact),
    }
    return {
        "report_schema_version": "research_tree_workspace_agent_adversarial_report.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "Exercise the LangGraph workspace agent with natural-language prompts "
            "that are more error-prone than unit tests: reducing visible papers, "
            "flattening branches, rerunning guarded candidate prep, constructing "
            "a replacement proposal, enriching similar papers, repairing invalid "
            "proposals, editing review payloads, and rejecting proposals."
        ),
        "dependency_command": "python -m pip install -e '.[dev]'",
        "script_command": "python tests/workspace_agent_adversarial_report.py",
        "script_args": {
            "--output-json": "Optional report path. Defaults to tests/output/workspace_agent_adversarial_report.json.",
            "--print": "Also print the JSON report to stdout.",
        },
        "fixture": {
            "workspace_id": workspace["workspace_id"],
            "topic": workspace["topic"],
            "workspace_version_hash": workspace_version_hash(workspace),
            "visible_paper_count": len(workspace["paper_cards"]),
            "branch_count": len(workspace["tree"]["nodes"]),
            "candidate_paper_ids": _candidate_ids(candidate_artifact),
        },
        "safety_expectations": [
            "Natural-language prompts enter the graph normally; scenarios do not call nodes directly.",
            "Candidate preparation reruns are mocked but routed through the agent guardrail and PipelineConfig.",
            "Construct workspace calls are mocked but routed through construct_workspace_modification or repair nodes.",
            "Similar-paper enrichment is run with fake deterministic retriever/reranker after approval.",
            "Modification proposals pause at interrupt() before any in-memory patch application.",
            "Approved changes are applied only in memory and include version lineage metadata.",
            "Rejected changes do not expose an updated_workspace.",
            "No persistent workspace JSON or database state is mutated.",
        ],
        "cases": cases,
        "overall_passed": all(case["passed"] for case in cases.values()),
    }


class PromptAwareLlm:
    def complete_structured(
        self,
        *,
        prompt: str,
        response_model: type[StructuredModelT],
        model_name: str | None = None,
    ) -> StructuredModelT:
        payload = _prompt_payload(prompt)
        user_message = str(payload.get("user_message") or "").casefold()
        if response_model is AgentIntent:
            return response_model.model_validate(_intent_for_prompt(user_message))
        if response_model is AgentNextAction:
            return response_model.model_validate(
                _next_action_for_prompt(user_message, payload)
            )
        if response_model is WorkspaceCritique:
            return response_model.model_validate(_critique_for_prompt(user_message))
        return response_model.model_validate({})

    def complete_text(
        self,
        *,
        prompt: str,
        model_name: str | None = None,
    ) -> str:
        payload = _prompt_payload(prompt)
        user_message = str(payload.get("user_message") or "").casefold()
        if "explain" in user_message:
            return (
                "The workspace currently separates reasoning, retrieval, and evaluation. "
                "The reasoning branch contains the core papers that teach how LLMs "
                "produce or search over reasoning traces, while faithfulness papers "
                "belong nearby as a check on whether those traces are reliable."
            )
        return "Prompt-aware smoke LLM response."


def _intent_for_prompt(user_message: str) -> dict[str, Any]:
    if "different scoring" in user_message or "disable dedupe" in user_message:
        return {
            "intent_type": "retrieve_more_papers",
            "confidence": 0.96,
            "target_branch_id": None,
            "target_paper_ids": [],
            "requires_workspace_modification": False,
            "requires_more_papers": True,
            "reason": "The prompt asks for a retrieval rerun with forbidden changes.",
        }
    if any(
        term in user_message
        for term in (
            "less papers",
            "less branching",
            "newer search",
            "merge everything",
            "remove weak",
            "manual edit",
            "reject",
        )
    ):
        return {
            "intent_type": "modify_workspace",
            "confidence": 0.97,
            "target_branch_id": "branch-reasoning",
            "target_paper_ids": ["p-cot", "p-search", "p-faith"],
            "requires_workspace_modification": True,
            "requires_more_papers": any(
                term in user_message
                for term in ("less papers", "newer search")
            ),
            "reason": "The prompt asks for a workspace-changing operation.",
        }
    if any(term in user_message for term in ("too flat", "critique")):
        return {
            "intent_type": "critique_workspace",
            "confidence": 0.94,
            "target_branch_id": None,
            "target_paper_ids": [],
            "requires_workspace_modification": False,
            "requires_more_papers": False,
            "reason": "The prompt asks for critique without mutation.",
        }
    return {
        "intent_type": "chat",
        "confidence": 0.9,
        "target_branch_id": None,
        "target_paper_ids": [],
        "requires_workspace_modification": False,
        "requires_more_papers": False,
        "reason": "The prompt asks for explanation.",
    }


def _next_action_for_prompt(
    user_message: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    action_history = payload.get("action_history") or []
    has_retrieved = any(
        isinstance(item, Mapping)
        and item.get("action_type") == "prepare_retrieval_rerun"
        for item in action_history
    )
    if "different scoring" in user_message or "disable dedupe" in user_message:
        return {
            "action_type": "prepare_retrieval_rerun",
            "reason": "The user asked to rebuild retrieval with forbidden algorithm changes.",
            "retrieval_request": {
                "topic": "LLM reasoning methods",
                "max_candidates": 6,
                "alpha": 1.25,
                "scoring_algorithm": "replace age-adjusted citation scoring",
                "dedupe_logic": "disable dedupe",
                "reason": "Adversarial guardrail test.",
            },
        }
    if any(term in user_message for term in ("less papers", "newer search")) and not has_retrieved:
        return {
            "action_type": "prepare_retrieval_rerun",
            "reason": "The request benefits from a smaller/refreshed candidate set before reconstruction.",
            "retrieval_request": {
                "topic": "LLM reasoning methods",
                "query_overrides": ["LLM reasoning chain-of-thought search faithfulness"],
                "max_initial_results": 4,
                "max_candidates": 3,
                "alpha": 0.75 if "less papers" in user_message else 2.0,
                "reason": "Build a compact candidate handoff for a smaller workspace.",
            },
        }
    if any(
        term in user_message
        for term in (
            "less papers",
            "less branching",
            "newer search",
            "merge everything",
            "remove weak",
            "manual edit",
            "reject",
        )
    ):
        return {
            "action_type": "construct_workspace_modification",
            "reason": "The user asked to modify workspace structure.",
            "target_branch_id": "branch-reasoning",
            "target_paper_ids": ["p-cot", "p-search", "p-faith"],
            "modification_instruction": (
                "Produce a smaller workspace with fewer visible papers and less branching "
                "while preserving schema correctness and provenance."
            ),
        }
    if any(term in user_message for term in ("too flat", "critique")):
        return {
            "action_type": "critique_workspace",
            "reason": "The user asked for critique.",
        }
    return {
        "action_type": "answer_chat",
        "reason": "The user asked for an explanation.",
    }


def _critique_for_prompt(user_message: str) -> dict[str, Any]:
    return WorkspaceCritique(
        summary=(
            "The workspace is mildly too flat: the reasoning branch mixes CoT, "
            "search, and faithfulness papers, while retrieval and evaluation are "
            "already separate enough for this small fixture."
        ),
        findings=[
            WorkspaceCritiqueFinding(
                finding_type="too_flat",
                target_ids={"branch_id": "branch-reasoning"},
                severity="medium",
                explanation="Reasoning papers cover different conceptual jobs in one branch.",
                suggested_fix="Split only if the user wants deeper reasoning structure; otherwise keep it compact.",
            )
        ],
        should_modify_workspace=False,
    ).model_dump()


def _run_chat_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(llm_client=PromptAwareLlm())
    result = graph.invoke(
        {
            "workspace": deepcopy(workspace),
            "candidate_artifact": deepcopy(candidate_artifact),
            "user_message": "Explain how the reasoning branch relates to evaluation.",
        },
        {"configurable": {"thread_id": "adversarial-chat"}},
    )
    checks = {
        "completed": result.get("status") == "completed",
        "has_response": bool(result.get("final_response")),
        "no_proposal": result.get("proposed_workspace") is None,
        "no_update": result.get("updated_workspace") is None,
    }
    return _case(
        command="Explain how the reasoning branch relates to evaluation.",
        expected="Answer from workspace context without proposal or mutation.",
        checks=checks,
        details={
            "final_response": result.get("final_response"),
            "node_trace": result.get("node_trace") or [],
        },
    )


def _run_critique_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(llm_client=PromptAwareLlm())
    result = graph.invoke(
        {
            "workspace": deepcopy(workspace),
            "candidate_artifact": deepcopy(candidate_artifact),
            "user_message": "Is this workspace too flat?",
        },
        {"configurable": {"thread_id": "adversarial-critique"}},
    )
    checks = {
        "completed": result.get("status") == "completed",
        "has_critique": "too flat" in str(result.get("final_response", "")).casefold(),
        "no_proposal": result.get("proposed_workspace") is None,
        "no_update": result.get("updated_workspace") is None,
    }
    return _case(
        command="Is this workspace too flat?",
        expected="Critique response without mutation.",
        checks=checks,
        details={
            "final_response": result.get("final_response"),
            "node_trace": result.get("node_trace") or [],
        },
    )


def _run_less_papers_pipeline_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    retrieval_calls: list[dict[str, Any]] = []
    construct_calls: list[dict[str, Any]] = []
    graph = build_workspace_agent_graph(
        llm_client=PromptAwareLlm(),
        workspace_constructor=_recording_constructor(
            construct_calls,
            mode="compact",
        ),
        retrieval_runner=_recording_retrieval_runner(retrieval_calls),
    )
    config = {"configurable": {"thread_id": "adversarial-less-papers"}}
    chunks = list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Remake the workspace with less papers and less branching.",
                "allow_pipeline_rerun": True,
                "require_approval": False,
            },
            config,
        )
    )
    state = graph.get_state(config).values
    interrupt_payload = _interrupt_payload_from_chunks(chunks)
    approved = graph.invoke(Command(resume={"choice": "approve"}), config)
    enrichment = _run_enrichment(approved["updated_workspace"])
    before_summary = _workspace_summary(workspace)
    proposed_summary = _workspace_summary(state["proposed_workspace"])
    approved_summary = _workspace_summary(approved["updated_workspace"])
    checks = {
        "retrieval_runner_called": len(retrieval_calls) == 1,
        "constructor_called": len(construct_calls) == 1,
        "proposed_has_fewer_visible_papers": proposed_summary["visible_paper_count"]
        < before_summary["visible_paper_count"],
        "proposed_has_fewer_branches": proposed_summary["branch_count"]
        < before_summary["branch_count"],
        "validation_valid": bool((state.get("validation_summary") or {}).get("valid")),
        "has_interrupt_payload": (interrupt_payload or {}).get("type")
        == "workspace_patch_review",
        "approval_applied_in_memory": isinstance(approved.get("updated_workspace"), dict),
        "similar_enrichment_ran": enrichment["selected_total"] > 0,
    }
    return _case(
        command="Remake the workspace with less papers and less branching.",
        expected=(
            "Agent autonomously reroutes through guarded candidate prep, constructs a smaller proposal, "
            "validates, interrupts for review, applies only after approval, and can enrich similar papers."
        ),
        checks=checks,
        details={
            "before_summary": before_summary,
            "proposed_summary": proposed_summary,
            "approved_summary": approved_summary,
            "before_workspace": workspace,
            "proposed_workspace": state.get("proposed_workspace"),
            "approved_workspace": approved.get("updated_workspace"),
            "pipeline_calls": {
                "prepare_workspace_candidates": retrieval_calls,
                "construct_workspace": construct_calls,
                "enrich_workspace_similar_papers": enrichment["call"],
            },
            "retrieval_request": state.get("retrieval_request"),
            "retrieval_guardrail_result": state.get("retrieval_guardrail_result"),
            "diff_summary": state.get("diff_summary"),
            "proposed_operations": state.get("proposed_operations"),
            "validation_summary": state.get("validation_summary"),
            "interrupt_payload": interrupt_payload,
            "approval_result": {
                "status": approved.get("status"),
                "final_response": approved.get("final_response"),
                "latest_agent_update_provenance": _latest_agent_update(
                    approved.get("updated_workspace")
                ),
            },
            "similar_papers_enrichment": enrichment,
            "node_trace_before_approval": state.get("node_trace") or [],
        },
    )


def _run_guardrail_rejection_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    retrieval_calls: list[dict[str, Any]] = []
    graph = build_workspace_agent_graph(
        llm_client=PromptAwareLlm(),
        retrieval_runner=_recording_retrieval_runner(retrieval_calls),
    )
    config = {"configurable": {"thread_id": "adversarial-guardrail"}}
    result = graph.invoke(
        {
            "workspace": deepcopy(workspace),
            "candidate_artifact": deepcopy(candidate_artifact),
            "user_message": "Use a totally different scoring algorithm and disable dedupe to rebuild the workspace.",
            "allow_pipeline_rerun": True,
        },
        config,
    )
    state = graph.get_state(config).values
    checks = {
        "status_failed": result.get("status") == "failed",
        "guardrail_rejected": not (state.get("retrieval_guardrail_result") or {}).get(
            "allowed",
            True,
        ),
        "retrieval_runner_not_called": not retrieval_calls,
        "no_proposal": result.get("proposed_workspace") is None,
        "no_update": result.get("updated_workspace") is None,
    }
    return _case(
        command="Use a totally different scoring algorithm and disable dedupe to rebuild the workspace.",
        expected="Guardrail rejection before any retrieval runner call.",
        checks=checks,
        details={
            "final_response": result.get("final_response"),
            "errors": result.get("errors") or [],
            "retrieval_request": state.get("retrieval_request"),
            "retrieval_guardrail_result": state.get("retrieval_guardrail_result"),
            "retrieval_calls": retrieval_calls,
            "node_trace": state.get("node_trace") or [],
        },
    )


def _run_repair_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    construct_calls: list[dict[str, Any]] = []
    graph = build_workspace_agent_graph(
        llm_client=PromptAwareLlm(),
        workspace_constructor=_recording_constructor(
            construct_calls,
            mode="invalid_then_repair",
        ),
    )
    config = {"configurable": {"thread_id": "adversarial-repair"}}
    chunks = list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Merge everything into one branch and remove weak papers.",
            },
            config,
        )
    )
    state = graph.get_state(config).values
    interrupt_payload = _interrupt_payload_from_chunks(chunks)
    checks = {
        "constructor_called_twice": len(construct_calls) == 2,
        "repair_mode_used": any(
            call["construction_mode"] == "workspace_repair"
            for call in construct_calls
        ),
        "repair_attempts_incremented": state.get("repair_attempts") == 1,
        "final_validation_valid": bool((state.get("validation_summary") or {}).get("valid")),
        "interrupted_for_review_after_repair": (interrupt_payload or {}).get("type")
        == "workspace_patch_review",
    }
    return _case(
        command="Merge everything into one branch and remove weak papers.",
        expected="Invalid first proposal should route to repair, then validate and interrupt.",
        checks=checks,
        details={
            "construct_calls": construct_calls,
            "repair_attempts": state.get("repair_attempts"),
            "validation_summary": state.get("validation_summary"),
            "proposed_operations": state.get("proposed_operations"),
            "diff_summary": state.get("diff_summary"),
            "interrupt_payload": interrupt_payload,
            "proposed_workspace": state.get("proposed_workspace"),
            "node_trace": state.get("node_trace") or [],
        },
    )


def _run_edited_review_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=PromptAwareLlm(),
        workspace_constructor=_recording_constructor([], mode="compact"),
    )
    config = {"configurable": {"thread_id": "adversarial-edited-review"}}
    list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Manual edit this proposal to make the compact workspace even smaller.",
            },
            config,
        )
    )
    edited_workspace = _one_paper_workspace(workspace)
    chunks = list(
        graph.stream(
            Command(
                resume={
                    "choice": "edit",
                    "proposed_workspace": edited_workspace,
                }
            ),
            config,
        )
    )
    state = graph.get_state(config).values
    interrupt_payload = _interrupt_payload_from_chunks(chunks)
    checks = {
        "edited_workspace_in_state": state["proposed_workspace"]["workspace_id"]
        == edited_workspace["workspace_id"],
        "edited_visible_count_one": len(state["proposed_workspace"]["paper_cards"]) == 1,
        "revalidated_valid": bool((state.get("validation_summary") or {}).get("valid")),
        "interrupted_again": (interrupt_payload or {}).get("type")
        == "workspace_patch_review",
    }
    return _case(
        command="Resume review with an edited one-paper proposal.",
        expected="Edited proposal is revalidated and review interrupt is emitted again.",
        checks=checks,
        details={
            "edited_workspace": edited_workspace,
            "state_proposed_workspace": state.get("proposed_workspace"),
            "validation_summary": state.get("validation_summary"),
            "diff_summary": state.get("diff_summary"),
            "interrupt_payload": interrupt_payload,
            "node_trace": state.get("node_trace") or [],
        },
    )


def _run_reject_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=PromptAwareLlm(),
        workspace_constructor=_recording_constructor([], mode="compact"),
    )
    config = {"configurable": {"thread_id": "adversarial-reject"}}
    list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Reject the compact-remake proposal after review.",
            },
            config,
        )
    )
    rejected = graph.invoke(Command(resume={"choice": "reject"}), config)
    checks = {
        "status_rejected": rejected.get("status") == "rejected",
        "no_updated_workspace": rejected.get("updated_workspace") is None,
        "proposal_kept_as_review_history": isinstance(
            rejected.get("proposed_workspace"),
            dict,
        ),
    }
    return _case(
        command="Reject the compact-remake proposal after review.",
        expected="No in-memory patch application after rejection.",
        checks=checks,
        details={
            "status": rejected.get("status"),
            "final_response": rejected.get("final_response"),
            "updated_workspace_present": rejected.get("updated_workspace") is not None,
            "proposed_workspace_hash": (
                workspace_version_hash(rejected["proposed_workspace"])
                if isinstance(rejected.get("proposed_workspace"), dict)
                else None
            ),
            "base_workspace_hash": workspace_version_hash(workspace),
            "node_trace": rejected.get("node_trace") or [],
        },
    )


def _recording_retrieval_runner(
    calls: list[dict[str, Any]],
):
    def run(config: PipelineConfig) -> dict[str, Any]:
        payload = asdict(config)
        payload["repo_root"] = str(config.repo_root)
        calls.append(payload)
        return {
            "schema_version": "llm_candidate_papers.v1",
            "topic": config.topic,
            "workspace": config.topic,
            "candidate_pool_order": "age_adjusted_citation_score_desc",
            "citation_age_exponent": config.citation_age_exponent,
            "non_survey_papers": [
                _candidate("p-cot", "Chain-of-Thought Prompting Elicits Reasoning"),
                _candidate("p-search", "Tree of Thoughts: Deliberate Problem Solving"),
                _candidate("p-faith", "Faithfulness of Chain-of-Thought Explanations"),
            ][: config.k],
            "survey_papers": [
                {
                    **_candidate("p-survey", "A Survey of LLM Reasoning Methods"),
                    "is_survey": True,
                }
            ],
            "run_dir": "mock://workspace_candidate_preparation/run-compact",
            "warnings": [],
        }

    return run


def _recording_constructor(
    calls: list[dict[str, Any]],
    *,
    mode: str,
):
    call_count = {"count": 0}

    def construct(**kwargs: Any) -> dict[str, Any]:
        call_count["count"] += 1
        calls.append(
            {
                "construction_mode": kwargs.get("construction_mode"),
                "agent_instruction": kwargs.get("agent_instruction"),
                "target_branch_id": kwargs.get("target_branch_id"),
                "target_paper_ids": kwargs.get("target_paper_ids") or [],
                "candidate_count": _candidate_count(kwargs.get("candidate_artifact")),
                "similar_context_count": len(kwargs.get("similar_papers_context") or {}),
                "call_index": call_count["count"],
            }
        )
        base = deepcopy(kwargs["base_workspace"])
        if mode == "invalid_then_repair" and kwargs.get("construction_mode") != "workspace_repair":
            invalid = deepcopy(base)
            invalid["paper_cards"] = {}
            return invalid
        if mode == "one_paper":
            return _one_paper_workspace(base)
        return _compact_workspace(base)

    return construct


def _compact_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    compact = deepcopy(workspace)
    compact["scope"] = {
        **compact["scope"],
        "scope_label": "compact_topic",
        "scope_rationale": "Agent proposal reduces visible papers and branch count.",
        "visible_paper_budget": {
            "target_min": 1,
            "target_max": 3,
            "hard_max_default": 4,
        },
    }
    compact["tree"]["nodes"] = [
        {
            "node_id": "branch-core-reasoning",
            "parent_id": "root",
            "label": "Core Reasoning Methods",
            "description": "Compact branch focused on the minimum papers needed for orientation.",
            "why_it_matters": "It keeps the workspace readable while preserving the core reasoning thread.",
            "is_leaf": True,
            "child_node_ids": [],
            "primary_paper_ids": ["p-cot", "p-search"],
            "secondary_paper_ids": [],
            "tags": ["reasoning", "compact"],
            "open_questions": [],
        }
    ]
    compact["paper_paths"] = [
        _paper_path(
            "path-core-reasoning",
            "branch-core-reasoning",
            "Core reasoning path",
            ["p-cot", "p-search"],
        )
    ]
    compact["paper_cards"] = {
        "p-cot": _moved_card(
            compact["paper_cards"]["p-cot"],
            "branch-core-reasoning",
            "Core Reasoning Methods",
        ),
        "p-search": _moved_card(
            compact["paper_cards"]["p-search"],
            "branch-core-reasoning",
            "Core Reasoning Methods",
        ),
    }
    compact["reading_order"] = [
        {"order": 1, "paper_id": "p-cot", "reason": "Core prompting baseline."},
        {"order": 2, "paper_id": "p-search", "reason": "Search-style extension."},
    ]
    compact["discarded_candidates"] = [
        {
            "paper_id": "p-faith",
            "title": "Faithfulness of Chain-of-Thought Explanations",
            "discard_reason": "Important but moved off-path for compact overview.",
            "possible_future_use": "off_path",
        }
    ]
    compact["provenance"] = {
        **compact["provenance"],
        "agent_proposal": {
            "proposal_type": "compact_workspace",
            "base_workspace_version_hash": workspace_version_hash(workspace),
            "persistence": "not_persisted_by_agent",
        },
    }
    return compact


def _one_paper_workspace(workspace: dict[str, Any]) -> dict[str, Any]:
    one = _compact_workspace(workspace)
    one["tree"]["nodes"][0]["primary_paper_ids"] = ["p-cot"]
    one["paper_paths"][0]["paper_ids"] = ["p-cot"]
    one["paper_cards"] = {"p-cot": one["paper_cards"]["p-cot"]}
    one["reading_order"] = [
        {"order": 1, "paper_id": "p-cot", "reason": "Single core anchor."}
    ]
    one["discarded_candidates"] = [
        {
            "paper_id": "p-search",
            "title": "Tree of Thoughts: Deliberate Problem Solving",
            "discard_reason": "Reviewer edited proposal to keep only the anchor paper.",
            "possible_future_use": "off_path",
        },
        *one["discarded_candidates"],
    ]
    one["provenance"] = {
        **one["provenance"],
        "review_edit": {
            "edited_by": "smoke_test",
            "reason": "Exercise edited-review revalidation.",
        },
    }
    return one


def _run_enrichment(workspace: dict[str, Any]) -> dict[str, Any]:
    candidate_artifact = _candidate_artifact()
    paper_database = list(candidate_papers_from_artifact(candidate_artifact).values())
    enriched, debug = build_similar_papers(
        workspace=workspace,
        paper_database=paper_database,
        k=2,
        retriever=FakeSimilarRetriever(),
        reranker=FakeSimilarReranker(),
    )
    selected_total = sum(
        len(card.get("similar_papers") or [])
        for card in enriched.get("paper_cards", {}).values()
        if isinstance(card, Mapping)
    )
    return {
        "call": {
            "function": "build_similar_papers",
            "k": 2,
            "paper_database_count": len(paper_database),
            "retriever": "FakeSimilarRetriever",
            "reranker": "FakeSimilarReranker",
        },
        "selected_total": selected_total,
        "debug": debug,
        "enriched_workspace_hash": workspace_version_hash(enriched),
        "sample_similar_papers": {
            paper_id: card.get("similar_papers") or []
            for paper_id, card in enriched.get("paper_cards", {}).items()
            if isinstance(card, Mapping)
        },
    }


class FakeSimilarRetriever:
    def rank(
        self,
        query: str,
        papers: list[CandidatePaperMetadata],
        top_n: int,
    ) -> list[ScoredSimilarPaperCandidate]:
        return [
            ScoredSimilarPaperCandidate(paper=paper, similarity_score=1.0 - index / 100)
            for index, paper in enumerate(papers[:top_n])
        ]


class FakeSimilarReranker:
    def rerank(
        self,
        query: str,
        candidates: list[ScoredSimilarPaperCandidate],
    ) -> list[ScoredSimilarPaperCandidate]:
        return candidates


def _workspace() -> dict[str, Any]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "llm-reasoning__adversarial",
        "topic": "LLM reasoning methods",
        "title": "LLM Reasoning Methods",
        "scope": {
            "scope_label": "narrow_topic",
            "scope_rationale": "Adversarial fixture with more branches and papers than needed.",
            "visible_paper_budget": {
                "target_min": 2,
                "target_max": 6,
                "hard_max_default": 8,
            },
        },
        "source_candidate_artifact": {
            "path": "<in-memory-adversarial-fixture>",
            "schema_version": "llm_candidate_papers.v1",
        },
        "root": {
            "node_id": "root",
            "label": "LLM Reasoning Methods",
            "overview": "Workspace for understanding LLM reasoning, retrieval, and evaluation methods.",
            "root_survey_type": "human_survey",
            "survey_anchor_paper_ids": ["p-survey"],
            "representative_paper_ids": ["p-cot"],
            "key_terms": ["reasoning", "retrieval", "evaluation"],
            "open_questions": [],
            "suggested_reading_direction": "Start with CoT, then search reasoning, then evaluation.",
        },
        "tree": {
            "root_node_id": "root",
            "nodes": [
                _branch_node(
                    "branch-reasoning",
                    "root",
                    "Reasoning Methods",
                    ["p-cot", "p-search", "p-faith"],
                    "Reasoning papers that could be compacted.",
                ),
                _branch_node(
                    "branch-retrieval",
                    "root",
                    "Retrieval Support",
                    ["p-rag"],
                    "Retrieval paper included as nearby context.",
                ),
                _branch_node(
                    "branch-evaluation",
                    "root",
                    "Evaluation",
                    ["p-eval"],
                    "Evaluation paper included as nearby context.",
                ),
            ],
        },
        "paper_paths": [
            _paper_path(
                "path-reasoning",
                "branch-reasoning",
                "Reasoning path",
                ["p-cot", "p-search", "p-faith"],
            ),
            _paper_path("path-retrieval", "branch-retrieval", "Retrieval path", ["p-rag"]),
            _paper_path("path-eval", "branch-evaluation", "Evaluation path", ["p-eval"]),
        ],
        "paper_cards": {
            "p-cot": _paper_card("p-cot", "Chain-of-Thought Prompting Elicits Reasoning", "branch-reasoning", "Reasoning Methods", "foundational"),
            "p-search": _paper_card("p-search", "Tree of Thoughts: Deliberate Problem Solving", "branch-reasoning", "Reasoning Methods", "method"),
            "p-faith": _paper_card("p-faith", "Faithfulness of Chain-of-Thought Explanations", "branch-reasoning", "Reasoning Methods", "critique"),
            "p-rag": _paper_card("p-rag", "Retrieval-Augmented Generation for Knowledge-Intensive NLP", "branch-retrieval", "Retrieval Support", "method"),
            "p-eval": _paper_card("p-eval", "Evaluating Verifiability in Language Model Reasoning", "branch-evaluation", "Evaluation", "evaluation"),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p-cot", "reason": "Core reasoning anchor."},
            {"order": 2, "paper_id": "p-search", "reason": "Search extension."},
            {"order": 3, "paper_id": "p-faith", "reason": "Faithfulness critique."},
            {"order": 4, "paper_id": "p-rag", "reason": "Contextual retrieval support."},
            {"order": 5, "paper_id": "p-eval", "reason": "Evaluation context."},
        ],
        "comparison_tables": [],
        "discarded_candidates": [
            {
                "paper_id": "p-app",
                "title": "Reasoning for a Narrow Application",
                "discard_reason": "application-specific",
                "possible_future_use": "off_path",
            }
        ],
        "provenance": {
            "workspace_constructor": "adversarial_fixture",
            "model": "deterministic",
            "prompt_version": "workspace_agent_adversarial.v1",
            "created_at": "2026-07-07T00:00:00+00:00",
            "warnings": [],
        },
    }


def _candidate_artifact() -> dict[str, Any]:
    return {
        "schema_version": "llm_candidate_papers.v1",
        "topic": "LLM reasoning methods",
        "workspace": "LLM reasoning methods",
        "candidate_pool_order": "age_adjusted_citation_score_desc",
        "non_survey_papers": [
            _candidate("p-cot", "Chain-of-Thought Prompting Elicits Reasoning"),
            _candidate("p-search", "Tree of Thoughts: Deliberate Problem Solving"),
            _candidate("p-faith", "Faithfulness of Chain-of-Thought Explanations"),
            _candidate("p-rag", "Retrieval-Augmented Generation for Knowledge-Intensive NLP"),
            _candidate("p-eval", "Evaluating Verifiability in Language Model Reasoning"),
            _candidate("p-app", "Reasoning for a Narrow Application"),
        ],
        "survey_papers": [
            {
                **_candidate("p-survey", "A Survey of LLM Reasoning Methods"),
                "is_survey": True,
            }
        ],
    }


def _candidate(paper_id: str, title: str) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": title,
        "abstract": f"{title} abstract for adversarial smoke testing.",
        "year": 2024,
        "authors": ["Adversarial Test"],
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "citation_count": 10,
        "age_adjusted_rank": 1,
        "cross_encoder_relevance": 0.5,
        "is_survey": False,
        "found_by": ["adversarial-smoke"],
    }


def _branch_node(
    node_id: str,
    parent_id: str,
    label: str,
    paper_ids: list[str],
    description: str,
) -> dict[str, Any]:
    return {
        "node_id": node_id,
        "parent_id": parent_id,
        "label": label,
        "description": description,
        "why_it_matters": description,
        "is_leaf": True,
        "child_node_ids": [],
        "primary_paper_ids": paper_ids,
        "secondary_paper_ids": [],
        "tags": [],
        "open_questions": [],
    }


def _paper_path(
    path_id: str,
    branch_node_id: str,
    label: str,
    paper_ids: list[str],
) -> dict[str, Any]:
    return {
        "path_id": path_id,
        "branch_node_id": branch_node_id,
        "path_type": "primary_timeline",
        "label": label,
        "description": f"Read {' then '.join(paper_ids)}.",
        "paper_ids": paper_ids,
        "rationale": "Adversarial smoke-test paper path.",
    }


def _paper_card(
    paper_id: str,
    title: str,
    branch_id: str,
    branch_label: str,
    role: str,
) -> dict[str, Any]:
    return {
        "paper_id": paper_id,
        "title": title,
        "authors": ["Adversarial Test"],
        "year": 2024,
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "doi": None,
        "arxiv_id": None,
        "abstract": f"{title} abstract for adversarial smoke testing.",
        "primary_tree_location": {
            "node_id": branch_id,
            "path": ["LLM Reasoning Methods", branch_label],
        },
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": role,
        "one_sentence_contribution": f"{title} represents {branch_label}.",
        "problem": "",
        "core_idea": "",
        "method": "",
        "assumptions": "",
        "datasets_or_benchmarks": "",
        "results": "",
        "limitations": "",
        "why_it_belongs": f"It helps explain {branch_label}.",
        "read_before": [],
        "read_after": [],
        "user_notes": "",
        "similar_papers": [],
    }


def _moved_card(
    card: Mapping[str, Any],
    branch_id: str,
    branch_label: str,
) -> dict[str, Any]:
    moved = deepcopy(dict(card))
    moved["primary_tree_location"] = {
        "node_id": branch_id,
        "path": ["LLM Reasoning Methods", branch_label],
    }
    moved["why_it_belongs"] = f"It is retained as a compact representative for {branch_label}."
    return moved


def _case(
    *,
    command: str,
    expected: str,
    checks: dict[str, bool],
    details: dict[str, Any],
) -> dict[str, Any]:
    return {
        "command": command,
        "expected": expected,
        "passed": all(checks.values()),
        "checks": checks,
        **details,
    }


def _workspace_summary(workspace: Mapping[str, Any]) -> dict[str, Any]:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    nodes = [node for node in tree.get("nodes") or [] if isinstance(node, Mapping)]
    return {
        "workspace_id": workspace.get("workspace_id"),
        "topic": workspace.get("topic"),
        "workspace_version_hash": workspace_version_hash(workspace),
        "visible_paper_count": len(workspace.get("paper_cards") or {}),
        "branch_count": len(nodes),
        "branch_labels": [node.get("label") for node in nodes],
        "paper_path_count": len(workspace.get("paper_paths") or []),
    }


def _candidate_ids(candidate_artifact: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("non_survey_papers", "survey_papers"):
        ids.extend(
            str(paper["paper_id"])
            for paper in candidate_artifact.get(key) or []
            if isinstance(paper, Mapping) and paper.get("paper_id")
        )
    return ids


def _candidate_count(candidate_artifact: Any) -> int:
    if not isinstance(candidate_artifact, Mapping):
        return 0
    return len(_candidate_ids(candidate_artifact))


def _latest_agent_update(workspace: Any) -> dict[str, Any] | None:
    if not isinstance(workspace, Mapping):
        return None
    provenance = workspace.get("provenance")
    if not isinstance(provenance, Mapping):
        return None
    updates = provenance.get("agent_updates")
    if not isinstance(updates, list) or not updates:
        return None
    latest = updates[-1]
    return dict(latest) if isinstance(latest, Mapping) else None


def _interrupt_payload_from_chunks(chunks: list[dict[str, Any]]) -> dict[str, Any] | None:
    for chunk in chunks:
        interrupt = chunk.get("__interrupt__")
        if not interrupt:
            continue
        value = interrupt[0].value
        return dict(value) if isinstance(value, Mapping) else {"value": value}
    return None


def _prompt_payload(prompt: str) -> dict[str, Any]:
    marker = "# Payload\n"
    if marker not in prompt:
        return {}
    raw_payload = prompt.split(marker, 1)[1].strip()
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


if __name__ == "__main__":
    raise SystemExit(main())
