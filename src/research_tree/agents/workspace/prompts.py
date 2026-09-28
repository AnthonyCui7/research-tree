from __future__ import annotations

import json
from typing import Any, Mapping

from research_tree.workspace.prompts import (
    workspace_description_rules,
    workspace_importance_rules,
)


def build_agent_loop_prompt(
    *,
    user_message: str,
    conversation_history: list[Mapping[str, Any]] | None = None,
    workspace_summary: Mapping[str, Any],
) -> str:
    """The opening turn of the tool loop.

    The summary carries the workspace's shape — branches with their tree
    structure, and each paper's title, year, citation count, and one-sentence
    TLDR. Card bodies, abstracts, and extracted full text stay behind the read
    tools: inlining them cost ~112k tokens on a 25-paper workspace, and the
    loop replays its transcript every round.

    The model picks what to do by calling a tool, so the rules here are about
    *how to decide*, not about a taxonomy of intents.
    """

    return _prompt(
        "Help the user understand and edit this Research Tree workspace.",
        {
            "user_message": user_message,
            "conversation_history": _conversation_history_for_prompt(conversation_history),
            "workspace_summary": workspace_summary,
            "rules": [
                "The summary shows the workspace's shape: branches with parent/leaf structure, and each paper's title, authors, year, citation count, and one-sentence TLDR. Answer shape, authorship, and recency questions from it directly; for any claim beyond a TLDR, read the card, branch, or full text with the tools first.",
                "Use the fewest tool rounds that answer well: batch independent lookups by calling several read tools in one round (for example, read every branch you need at once) instead of one per round.",
                "Search Semantic Scholar or the web only when the workspace cannot answer the question.",
                "A paper found through web search must be resolved with get_semantic_scholar_paper before you propose adding it; propose ids, never metadata you wrote yourself.",
                "The summary lists every visible paper. When asked to add one that is already there, say so plainly; propose a move or reorder only when the request implies one.",
                "Call propose_workspace_edit only when the user asked for a change. Explaining a change is not making one — and once you know the edit, make the tool call instead of narrating it; tool rounds are limited.",
                "Semantic Scholar allows about one request per second; a handful of searches is the budget for a turn.",
                "Answer in prose, citing paper titles rather than ids.",
            ],
        },
    )


def build_proposal_skeptic_prompt(
    *,
    user_message: str,
    instruction: str,
    diff_summary: Mapping[str, Any],
    proposed_operations: list[Mapping[str, Any]],
    workspace_summary: Mapping[str, Any],
) -> str:
    """One second reading of a validated proposal, before the user reviews it.

    Operations are compacted to type, targets, and rationale — the full
    before/after payloads would dwarf the signal.
    """

    return _prompt(
        "Second-read this validated Research Tree workspace proposal before the user reviews it.",
        {
            "user_message": user_message,
            "agent_instruction": instruction,
            "diff_summary": diff_summary,
            "proposed_operations": [
                {
                    "operation_type": operation.get("operation_type"),
                    "target_ids": operation.get("target_ids"),
                    "rationale": operation.get("rationale"),
                }
                for operation in proposed_operations
                if isinstance(operation, Mapping)
            ],
            "workspace_summary": workspace_summary,
            "rules": [
                "You are the second reader, not the author: try to refute the change.",
                "Judge editorial quality — does the change improve the reader's model of the field?",
                "Return at most two objections, and none when the change is sound.",
                "One concrete sentence per objection, grounded in the workspace shape or the papers named. No generic caution.",
            ],
        },
    )


def build_workspace_critique_prompt(
    *,
    user_message: str,
    conversation_history: list[Mapping[str, Any]] | None = None,
    workspace_context: Mapping[str, Any],
) -> str:
    return _prompt(
        "Critique the Research Tree workspace without mutating it.",
        {
            "user_message": user_message,
            "conversation_history": _conversation_history_for_prompt(conversation_history),
            # The context carries the workspace and copies of its cards, paths
            # and reading order; the model reads the workspace once.
            "workspace": workspace_context.get("workspace") or {},
            "workspace_context": _context_beyond_the_workspace(workspace_context),
            "look_for": [
                "weak branches",
                "misplaced papers",
                "missing conceptual branches",
                "too-flat tree structure",
                "duplicate concepts",
                "missing survey anchors",
                "over-broad or over-narrow branches",
                "papers that should be off-path",
            ],
            "rules": [
                "Return critique only.",
                "Do not propose hidden workspace rewrites.",
                "Write concise, professional feedback for a research-literate academic reader.",
                "Prioritize concrete structural evidence over generic advice.",
            ],
        },
    )


def build_agent_modify_workspace_prompt(
    *,
    user_message: str,
    workspace: Mapping[str, Any],
    workspace_context: Mapping[str, Any],
    candidate_artifact: Mapping[str, Any] | None,
    agent_instruction: str,
    target_branch_id: str | None,
    target_paper_ids: list[str],
    similar_papers_context: Mapping[str, Any] | None,
) -> str:
    return _prompt(
        "Modify an existing Research Tree workspace by returning a JSON edit "
        "delta: only the parts the instruction changes. Everything the delta "
        "does not mention is kept exactly as it is.",
        {
            "user_message": user_message,
            "agent_instruction": agent_instruction,
            "target_branch_id": target_branch_id,
            "target_paper_ids": target_paper_ids,
            "workspace": workspace,
            # The context is derived from the same workspace, so only the parts
            # that are not already above go in. Sending it whole meant the model
            # read the workspace three times in one prompt.
            "workspace_context": _context_beyond_the_workspace(workspace_context),
            "candidate_artifact": candidate_artifact or {},
            "similar_papers_context": similar_papers_context or {},
            "rules": _workspace_delta_rules(),
        },
    )


def _context_beyond_the_workspace(
    workspace_context: Mapping[str, Any],
) -> dict[str, Any]:
    duplicated = {"workspace", "visible_paper_cards", "paper_paths", "reading_order"}
    return {
        key: value
        for key, value in workspace_context.items()
        if key not in duplicated
    }


def build_workspace_repair_prompt(
    *,
    user_message: str,
    base_workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
    validation_errors: list[str],
    original_instruction: str,
    operation_history: list[Mapping[str, Any]],
    candidate_artifact: Mapping[str, Any] | None,
) -> str:
    return _prompt(
        "Repair an invalid Research Tree workspace proposal by returning a "
        "JSON edit delta. The delta is applied on top of proposed_workspace.",
        {
            "user_message": user_message,
            "original_instruction": original_instruction,
            "base_workspace": base_workspace,
            "proposed_workspace": proposed_workspace,
            "validation_errors": validation_errors,
            "operation_history": operation_history,
            "candidate_artifact": candidate_artifact or {},
            "rules": [
                *_workspace_delta_rules(),
                "Change only what the validation errors require.",
                "Do not broaden the edit.",
            ],
        },
    )


def _workspace_delta_rules() -> list[str]:
    return [
        "Return one JSON object using only these keys, and only the ones this "
        "edit needs: title, topic, root, upsert_tree_nodes, "
        "remove_tree_node_ids, upsert_paper_paths, remove_paper_path_ids, "
        "upsert_paper_cards, remove_paper_ids.",
        "Everything the delta does not mention keeps its current value, so "
        "never restate unchanged branches, paths, cards, or fields.",
        "Upserts carry an id plus only the fields being changed. A list field "
        "you include replaces that list wholesale; write it complete.",
        "upsert_paper_cards values may set only primary_tree_location, "
        "secondary_tags, and importance; all other card data is filled "
        "deterministically.",
        "Move a paper by writing the updated primary_paper_ids of the branches "
        "involved (and any affected paper path); card locations and "
        "memberships are kept consistent for you.",
        "Add a visible paper from the candidate artifact by listing its id on "
        "a branch and, unless it is a survey, on that branch's paper path. Its "
        "metadata is filled from the artifact; never write metadata yourself.",
        "Remove papers or branches only through remove_paper_ids and "
        "remove_tree_node_ids.",
        "Paper paths are ordered reading sequences at leaf branches; keep "
        "paper_ids in the same order as paper_steps[].paper_id.",
        "Do not place survey papers in paper paths; use them only as root or "
        "branch overview anchors.",
        *workspace_description_rules(),
        *workspace_importance_rules(),
        "Return structured JSON only, with no Markdown or commentary.",
        "Do not exceed the visible paper budget without an explicit reason.",
    ]


def _conversation_history_for_prompt(
    history: list[Mapping[str, Any]] | None,
) -> list[dict[str, str]]:
    prompt_history: list[dict[str, str]] = []
    for item in history or []:
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        text = str(item.get("text") or "").strip()
        if text:
            prompt_history.append({"role": role, "text": text})
    return prompt_history


def _prompt(instruction: str, payload: Mapping[str, Any]) -> str:
    return (
        f"# Instruction\n{instruction}\n\n# Payload\n"
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)
    )
