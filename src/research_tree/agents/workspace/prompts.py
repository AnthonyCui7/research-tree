from __future__ import annotations

import json
from typing import Any, Mapping


def build_intent_prompt(
    *,
    user_message: str,
    workspace_summary: Mapping[str, Any] | None,
) -> str:
    return _prompt(
        "Classify the user's Research Tree workspace request.",
        {
            "user_message": user_message,
            "workspace_summary": workspace_summary or {},
            "allowed_intents": [
                "chat",
                "explain_paper",
                "recommend_papers",
                "critique_workspace",
                "modify_workspace",
                "expand_branch",
                "retrieve_more_papers",
                "repair_workspace",
            ],
            "rules": [
                "Return only the structured intent object.",
                "Set requires_workspace_modification only when the request would change the workspace.",
                "Set requires_more_papers only when the request likely needs a candidate retrieval rerun.",
            ],
        },
    )


def build_next_action_prompt(
    *,
    user_message: str,
    intent: Mapping[str, Any],
    workspace_context: Mapping[str, Any],
    action_history: list[Mapping[str, Any]],
    warnings: list[str],
    validation_summary: Mapping[str, Any] | None,
) -> str:
    return _prompt(
        "Choose the next safe action for the single Research Tree workspace agent.",
        {
            "user_message": user_message,
            "intent": intent,
            "workspace_context": workspace_context,
            "action_history": action_history,
            "warnings": warnings,
            "validation_summary": validation_summary,
            "allowed_actions": [
                "answer_chat",
                "critique_workspace",
                "construct_workspace_modification",
                "prepare_retrieval_rerun",
                "repair_workspace_proposal",
                "finalize",
            ],
            "rules": [
                "For explanation, comparison, and reading-order questions, answer_chat.",
                "For workspace critique without mutation, critique_workspace.",
                "For branch edits, moves, renames, card rewrites, and root updates, construct_workspace_modification.",
                "For requests needing more candidates, prepare_retrieval_rerun.",
                "Do not request retrieval algorithm, dedupe, model, or scoring changes.",
                "Keep the workspace small and scoped.",
            ],
        },
    )


def build_workspace_chat_prompt(
    *,
    user_message: str,
    workspace_context: Mapping[str, Any],
) -> str:
    return _prompt(
        "Answer as a Research Tree expert over this workspace.",
        {
            "user_message": user_message,
            "workspace_context": workspace_context,
            "rules": [
                "Do not mutate the workspace.",
                "Use visible papers and similar_papers as context.",
                "Be explicit about uncertainty when metadata is missing.",
                "Keep the answer useful for deciding what to read or edit next.",
            ],
        },
    )


def build_workspace_critique_prompt(
    *,
    user_message: str,
    workspace_context: Mapping[str, Any],
) -> str:
    return _prompt(
        "Critique the Research Tree workspace without mutating it.",
        {
            "user_message": user_message,
            "workspace_context": workspace_context,
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
        "Modify an existing Research Tree workspace and return the complete workspace JSON.",
        {
            "user_message": user_message,
            "agent_instruction": agent_instruction,
            "target_branch_id": target_branch_id,
            "target_paper_ids": target_paper_ids,
            "workspace": workspace,
            "workspace_context": workspace_context,
            "candidate_artifact": candidate_artifact or {},
            "similar_papers_context": similar_papers_context or {},
            "rules": _workspace_mutation_rules(),
        },
    )


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
        "Repair only the invalid parts of a proposed Research Tree workspace JSON.",
        {
            "user_message": user_message,
            "original_instruction": original_instruction,
            "base_workspace": base_workspace,
            "proposed_workspace": proposed_workspace,
            "validation_errors": validation_errors,
            "operation_history": operation_history,
            "candidate_artifact": candidate_artifact or {},
            "rules": [
                *_workspace_mutation_rules(),
                "Repair only validation failures.",
                "Do not broaden the edit.",
                "Do not rewrite unrelated branches.",
            ],
        },
    )


def _workspace_mutation_rules() -> list[str]:
    return [
        "Existing workspace JSON is the source of truth.",
        "The user instruction is bounded; preserve unrelated branches.",
        "Preserve paper IDs unless the structure intentionally changes them.",
        "Similar papers are context only unless explicitly promoted.",
        "New visible papers must come from the candidate artifact.",
        "Prefer minimal valid changes.",
        "Return structured JSON only, with no Markdown or commentary.",
        "Do not hallucinate papers or paper metadata.",
        "Do not exceed the visible paper budget without an explicit reason.",
        "Preserve provenance where possible.",
    ]


def _prompt(instruction: str, payload: Mapping[str, Any]) -> str:
    return (
        f"# Instruction\n{instruction}\n\n# Payload\n"
        + json.dumps(payload, ensure_ascii=True, separators=(",", ":"), default=str)
    )

