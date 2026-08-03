from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from langgraph.types import Command

from research_tree.agents.workspace.graph import build_workspace_agent_graph
from research_tree.agents.workspace.llm import DeterministicWorkspaceAgentLlmClient
from research_tree.agents.workspace.models import (
    AgentIntent,
    AgentNextAction,
    WorkspaceCritique,
    WorkspaceCritiqueFinding,
)
from research_tree.workspace.context import workspace_version_hash


DEFAULT_OUTPUT_PATH = Path(__file__).resolve().parent / "output" / "workspace_agent_smoke_report.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Run deterministic Research Tree LangGraph workspace-agent smoke checks "
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
        help="Also print the JSON report to stdout.",
    )
    args = parser.parse_args(argv)

    report = build_smoke_report()
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(
        json.dumps(report, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    if args.print:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Wrote workspace agent smoke report: {args.output_json}")
    return 0 if report["overall_passed"] else 1


def build_smoke_report() -> dict[str, Any]:
    workspace = _workspace()
    candidate_artifact = _candidate_artifact()
    chat_case = _run_chat_case(workspace, candidate_artifact)
    critique_case = _run_critique_case(workspace, candidate_artifact)
    modify_case = _run_modify_case(workspace, candidate_artifact)
    reject_case = _run_reject_case(workspace, candidate_artifact)
    cases = {
        "chat_path": chat_case,
        "critique_path": critique_case,
        "modify_path": modify_case,
        "reject_path": reject_case,
    }
    return {
        "report_schema_version": "research_tree_workspace_agent_smoke_report.v1",
        "generated_at": datetime.now(UTC).isoformat(),
        "purpose": (
            "Offline deterministic smoke report for another reviewer or LLM to "
            "inspect whether the LangGraph workspace agent routes chat, critique, "
            "and modification flows safely."
        ),
        "important_safety_expectations": [
            "Chat and critique paths do not create proposed or updated workspaces.",
            "Modify path creates a proposed workspace plus operations, diff, validation, and interrupt payload.",
            "No persistent workspace JSON is mutated by this script.",
            "Approval applies the proposal only in memory.",
            "Rejecting an approval leaves no updated workspace.",
            "Diff summaries and in-memory provenance contain workspace version hashes for revert/compare workflows.",
        ],
        "fixture": {
            "workspace_topic": workspace["topic"],
            "workspace_id": workspace["workspace_id"],
            "before_workspace_version_hash": workspace_version_hash(workspace),
            "candidate_paper_ids": _candidate_ids(candidate_artifact),
        },
        "cases": cases,
        "overall_passed": all(case["passed"] for case in cases.values()),
    }


def _run_chat_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=DeterministicWorkspaceAgentLlmClient(
            structured_outputs=[
                _intent("chat"),
                AgentNextAction(action_type="answer_chat", reason="answer from context"),
            ],
            text_outputs=[
                (
                    "The reasoning branch groups papers that develop LLM reasoning "
                    "methods: Chain-of-Thought as the basic prompting pattern, "
                    "Tree-of-Thought/search as explicit exploration, and faithfulness "
                    "work as a check on whether rationales reflect actual model behavior."
                )
            ],
        )
    )
    result = graph.invoke(
        {
            "workspace": deepcopy(workspace),
            "candidate_artifact": deepcopy(candidate_artifact),
            "user_message": "Explain the reasoning branch.",
        },
        {"configurable": {"thread_id": "smoke-chat"}},
    )
    checks = {
        "has_final_response": bool(result.get("final_response")),
        "has_no_proposed_workspace": result.get("proposed_workspace") is None,
        "has_no_updated_workspace": result.get("updated_workspace") is None,
        "status_completed": result.get("status") == "completed",
    }
    return {
        "command": "Explain the reasoning branch.",
        "expected": "final response, no proposed workspace, no mutation",
        "passed": all(checks.values()),
        "checks": checks,
        "final_response": result.get("final_response"),
        "status": result.get("status"),
        "workspace_hash_before": workspace_version_hash(workspace),
        "updated_workspace_present": result.get("updated_workspace") is not None,
        "proposed_workspace_present": result.get("proposed_workspace") is not None,
        "node_trace": result.get("node_trace") or [],
    }


def _run_critique_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=DeterministicWorkspaceAgentLlmClient(
            structured_outputs=[
                _intent("critique_workspace"),
                AgentNextAction(action_type="critique_workspace", reason="critique requested"),
                WorkspaceCritique(
                    summary=(
                        "The workspace is too flat for reasoning research because "
                        "CoT, search-style reasoning, and faithfulness critiques are "
                        "currently collapsed into one branch."
                    ),
                    findings=[
                        WorkspaceCritiqueFinding(
                            finding_type="too_flat",
                            target_ids={"branch_id": "branch-reasoning"},
                            severity="medium",
                            explanation=(
                                "The branch mixes method families and critique papers "
                                "that would be clearer as child branches."
                            ),
                            suggested_fix=(
                                "Split the branch into Chain-of-Thought, search-based "
                                "reasoning, and faithfulness/evaluation."
                            ),
                        )
                    ],
                    should_modify_workspace=False,
                ),
            ]
        )
    )
    result = graph.invoke(
        {
            "workspace": deepcopy(workspace),
            "candidate_artifact": deepcopy(candidate_artifact),
            "user_message": "Is this workspace too flat?",
        },
        {"configurable": {"thread_id": "smoke-critique"}},
    )
    checks = {
        "has_final_response": bool(result.get("final_response")),
        "mentions_flatness": "too flat" in str(result.get("final_response", "")).casefold(),
        "has_no_proposed_workspace": result.get("proposed_workspace") is None,
        "has_no_updated_workspace": result.get("updated_workspace") is None,
    }
    return {
        "command": "Is this workspace too flat?",
        "expected": "critique response, no mutation",
        "passed": all(checks.values()),
        "checks": checks,
        "final_response": result.get("final_response"),
        "status": result.get("status"),
        "workspace_hash_before": workspace_version_hash(workspace),
        "updated_workspace_present": result.get("updated_workspace") is not None,
        "proposed_workspace_present": result.get("proposed_workspace") is not None,
        "node_trace": result.get("node_trace") or [],
    }


def _run_modify_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=_modify_llm(),
        workspace_constructor=_split_reasoning_constructor,
    )
    config = {"configurable": {"thread_id": "smoke-modify"}}
    chunks = list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Split the reasoning branch into CoT, search, and faithfulness.",
            },
            config,
        )
    )
    interrupted = _interrupt_payload_from_chunks(chunks)
    state = graph.get_state(config).values
    approved = graph.invoke(Command(resume={"choice": "approve"}), config)
    checks = {
        "has_proposed_workspace": isinstance(state.get("proposed_workspace"), dict),
        "has_operations": bool(state.get("proposed_operations")),
        "has_diff_summary": isinstance(state.get("diff_summary"), dict),
        "validation_valid": bool((state.get("validation_summary") or {}).get("valid")),
        "has_interrupt_payload": isinstance(interrupted, dict),
        "interrupt_type_is_review": (interrupted or {}).get("type") == "workspace_patch_review",
        "approval_applied_in_memory": isinstance(approved.get("updated_workspace"), dict),
    }
    return {
        "command": "Split the reasoning branch into CoT, search, and faithfulness.",
        "expected": "proposed workspace, operations/diff, validation summary, interrupt approval payload",
        "passed": all(checks.values()),
        "checks": checks,
        "before_workspace_hash": workspace_version_hash(workspace),
        "proposed_workspace_hash": workspace_version_hash(state["proposed_workspace"]),
        "approved_workspace_hash": workspace_version_hash(approved["updated_workspace"]),
        "before_summary": _workspace_summary(workspace),
        "proposed_summary": _workspace_summary(state["proposed_workspace"]),
        "approved_summary": _workspace_summary(approved["updated_workspace"]),
        "before_tree": workspace["tree"],
        "proposed_tree": state["proposed_workspace"]["tree"],
        "proposed_paper_paths": state["proposed_workspace"]["paper_paths"],
        "proposed_operations": state.get("proposed_operations") or [],
        "diff_summary": state.get("diff_summary") or {},
        "validation_summary": state.get("validation_summary") or {},
        "interrupt_payload": interrupted,
        "approval_result": {
            "status": approved.get("status"),
            "final_response": approved.get("final_response"),
            "updated_workspace_present": approved.get("updated_workspace") is not None,
            "latest_agent_update_provenance": _latest_agent_update(approved.get("updated_workspace")),
        },
        "revert_note": (
            "This smoke script does not persist mutations. A UI/backend can revert "
            "by restoring the workspace whose hash is before_workspace_hash or by "
            "rejecting the approval before apply_patch_in_memory runs."
        ),
        "node_trace_before_approval": state.get("node_trace") or [],
    }


def _run_reject_case(
    workspace: dict[str, Any],
    candidate_artifact: dict[str, Any],
) -> dict[str, Any]:
    graph = build_workspace_agent_graph(
        llm_client=_modify_llm(),
        workspace_constructor=_split_reasoning_constructor,
    )
    config = {"configurable": {"thread_id": "smoke-reject"}}
    list(
        graph.stream(
            {
                "workspace": deepcopy(workspace),
                "candidate_artifact": deepcopy(candidate_artifact),
                "user_message": "Split the reasoning branch into CoT, search, and faithfulness.",
            },
            config,
        )
    )
    rejected = graph.invoke(Command(resume={"choice": "reject"}), config)
    checks = {
        "status_rejected": rejected.get("status") == "rejected",
        "no_updated_workspace": rejected.get("updated_workspace") is None,
        "still_has_proposal_for_review_history": isinstance(
            rejected.get("proposed_workspace"),
            dict,
        ),
    }
    return {
        "command": "Reject the proposed split.",
        "expected": "no in-memory patch application; proposed workspace remains review history only",
        "passed": all(checks.values()),
        "checks": checks,
        "status": rejected.get("status"),
        "final_response": rejected.get("final_response"),
        "updated_workspace_present": rejected.get("updated_workspace") is not None,
        "proposed_workspace_hash": (
            workspace_version_hash(rejected["proposed_workspace"])
            if isinstance(rejected.get("proposed_workspace"), dict)
            else None
        ),
        "base_workspace_hash": workspace_version_hash(workspace),
    }


def _modify_llm() -> DeterministicWorkspaceAgentLlmClient:
    return DeterministicWorkspaceAgentLlmClient(
        structured_outputs=[
            _intent("modify_workspace", modifies=True),
            AgentNextAction(
                action_type="construct_workspace_modification",
                reason="The user asked for a structural branch split.",
                target_branch_id="branch-reasoning",
                target_paper_ids=["p-cot", "p-search", "p-faithfulness"],
                modification_instruction=(
                    "Split branch-reasoning into child branches for CoT, search, "
                    "and faithfulness while preserving visible papers."
                ),
            ),
        ]
    )


def _intent(
    intent_type: str,
    *,
    modifies: bool = False,
    more: bool = False,
) -> AgentIntent:
    return AgentIntent(
        intent_type=intent_type,
        confidence=0.95,
        target_branch_id="branch-reasoning" if modifies else None,
        target_paper_ids=["p-cot", "p-search", "p-faithfulness"] if modifies else [],
        requires_workspace_modification=modifies,
        requires_more_papers=more,
        reason="deterministic smoke-test routing",
    )


def _split_reasoning_constructor(**kwargs: Any) -> dict[str, Any]:
    base = deepcopy(kwargs["base_workspace"])
    proposed = deepcopy(base)
    proposed["tree"]["nodes"] = [
        {
            "node_id": "branch-reasoning",
            "parent_id": "root",
            "label": "Reasoning Branch",
            "description": "Parent branch for major LLM reasoning threads.",
            "why_it_matters": "It orients the workspace around how reasoning methods differ.",
            "is_leaf": False,
            "child_node_ids": [
                "branch-cot",
                "branch-search",
                "branch-faithfulness",
            ],
            "primary_paper_ids": [],
            "secondary_paper_ids": [],
            "tags": ["reasoning"],
            "open_questions": [],
        },
        _branch_node(
            "branch-cot",
            "branch-reasoning",
            "Chain-of-Thought",
            ["p-cot"],
            "Prompting methods that elicit step-by-step reasoning traces.",
        ),
        _branch_node(
            "branch-search",
            "branch-reasoning",
            "Search-Based Reasoning",
            ["p-search"],
            "Methods that explore multiple reasoning paths before choosing an answer.",
        ),
        _branch_node(
            "branch-faithfulness",
            "branch-reasoning",
            "Faithfulness and Evaluation",
            ["p-faithfulness"],
            "Work that tests whether reasoning traces are reliable explanations.",
        ),
    ]
    proposed["paper_paths"] = [
        _paper_path("path-cot", "branch-cot", "CoT path", ["p-cot"]),
        _paper_path("path-search", "branch-search", "Search path", ["p-search"]),
        _paper_path(
            "path-faithfulness",
            "branch-faithfulness",
            "Faithfulness path",
            ["p-faithfulness"],
        ),
    ]
    _move_card(proposed, "p-cot", "branch-cot", "Chain-of-Thought")
    _move_card(proposed, "p-search", "branch-search", "Search-Based Reasoning")
    _move_card(proposed, "p-faithfulness", "branch-faithfulness", "Faithfulness and Evaluation")
    proposed["provenance"] = {
        **proposed["provenance"],
        "agent_proposal": {
            "construction_mode": kwargs.get("construction_mode"),
            "agent_instruction": kwargs.get("agent_instruction"),
            "target_branch_id": kwargs.get("target_branch_id"),
            "base_workspace_version_hash": workspace_version_hash(base),
            "proposal_note": "Deterministic smoke-test proposal; not persisted.",
        },
    }
    return proposed


def _workspace() -> dict[str, Any]:
    return {
        "schema_version": "research_tree_workspace.v1",
        "workspace_id": "llm-reasoning__smoke",
        "topic": "LLM reasoning methods",
        "title": "LLM Reasoning Methods",
        "scope": {
            "scope_label": "narrow_topic",
            "scope_rationale": "Small deterministic smoke-test workspace.",
            "visible_paper_budget": {
                "target_min": 1,
                "target_max": 6,
                "hard_max_default": 8,
            },
        },
        "source_candidate_artifact": {
            "path": "<in-memory-smoke-fixture>",
            "schema_version": "llm_candidate_papers.v1",
        },
        "root": {
            "node_id": "root",
            "label": "LLM Reasoning Methods",
            "overview": "Workspace for understanding major LLM reasoning method families.",
            "root_survey_type": "human_survey",
            "survey_anchor_paper_ids": ["p-survey"],
            "representative_paper_ids": ["p-cot"],
            "key_terms": ["chain-of-thought", "search", "faithfulness"],
            "open_questions": [],
            "suggested_reading_direction": "Start with CoT, then search methods, then faithfulness critiques.",
        },
        "tree": {
            "root_node_id": "root",
            "nodes": [
                _branch_node(
                    "branch-reasoning",
                    "root",
                    "Reasoning Branch",
                    ["p-cot", "p-search", "p-faithfulness"],
                    "Flat branch containing reasoning method and critique papers.",
                )
            ],
        },
        "paper_paths": [
            _paper_path(
                "path-reasoning",
                "branch-reasoning",
                "Reasoning overview path",
                ["p-cot", "p-search", "p-faithfulness"],
            )
        ],
        "paper_cards": {
            "p-cot": _paper_card(
                "p-cot",
                "Chain-of-Thought Prompting Elicits Reasoning",
                "branch-reasoning",
                "Reasoning Branch",
                "foundational",
            ),
            "p-search": _paper_card(
                "p-search",
                "Tree of Thoughts: Deliberate Problem Solving",
                "branch-reasoning",
                "Reasoning Branch",
                "method",
            ),
            "p-faithfulness": _paper_card(
                "p-faithfulness",
                "Faithfulness of Chain-of-Thought Explanations",
                "branch-reasoning",
                "Reasoning Branch",
                "critique",
            ),
        },
        "reading_order": [
            {"order": 1, "paper_id": "p-cot", "reason": "Core prompting pattern."},
            {"order": 2, "paper_id": "p-search", "reason": "Extends reasoning into search."},
            {"order": 3, "paper_id": "p-faithfulness", "reason": "Tests whether rationales are reliable."},
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
            "workspace_constructor": "smoke_fixture",
            "model": "deterministic",
            "prompt_version": "workspace_agent_smoke.v1",
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
            _candidate("p-faithfulness", "Faithfulness of Chain-of-Thought Explanations"),
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
        "abstract": f"{title} abstract for deterministic smoke testing.",
        "year": 2024,
        "authors": ["Smoke Test"],
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "citation_count": 10,
        "age_adjusted_rank": 1,
        "cross_encoder_relevance": 0.5,
        "is_survey": False,
        "found_by": ["smoke"],
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
        "rationale": "Deterministic smoke-test paper path.",
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
        "authors": ["Smoke Test"],
        "year": 2024,
        "venue": "Test Venue",
        "primary_link": f"https://example.com/{paper_id}",
        "doi": None,
        "arxiv_id": None,
        "abstract": f"{title} abstract for deterministic smoke testing.",
        "primary_tree_location": {
            "node_id": branch_id,
            "path": ["LLM Reasoning Methods", branch_label],
        },
        "secondary_tags": [],
        "reading_status": "unread",
        "paper_role": role,
        "one_sentence_contribution": f"{title} represents the {branch_label} area.",
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


def _move_card(
    workspace: dict[str, Any],
    paper_id: str,
    branch_id: str,
    branch_label: str,
) -> None:
    card = workspace["paper_cards"][paper_id]
    card["primary_tree_location"] = {
        "node_id": branch_id,
        "path": ["LLM Reasoning Methods", "Reasoning Branch", branch_label],
    }
    card["why_it_belongs"] = f"It is the representative paper for {branch_label}."


def _candidate_ids(candidate_artifact: Mapping[str, Any]) -> list[str]:
    ids: list[str] = []
    for key in ("non_survey_papers", "survey_papers"):
        ids.extend(
            str(paper["paper_id"])
            for paper in candidate_artifact.get(key) or []
            if isinstance(paper, Mapping) and paper.get("paper_id")
        )
    return ids


def _workspace_summary(workspace: Mapping[str, Any]) -> dict[str, Any]:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    nodes = [node for node in tree.get("nodes") or [] if isinstance(node, Mapping)]
    return {
        "workspace_id": workspace.get("workspace_id"),
        "topic": workspace.get("topic"),
        "workspace_version_hash": workspace_version_hash(workspace),
        "branch_labels": [node.get("label") for node in nodes],
        "leaf_branch_labels": [node.get("label") for node in nodes if node.get("is_leaf")],
        "visible_paper_count": len(workspace.get("paper_cards") or {}),
        "paper_path_count": len(workspace.get("paper_paths") or []),
    }


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


if __name__ == "__main__":
    raise SystemExit(main())

