from __future__ import annotations

from typing import Any, Callable, Mapping

from research_tree.workspace.validation import validate_workspace


ValidatorPayload = Mapping[str, Any]
WorkspaceValidator = Callable[[ValidatorPayload], dict[str, Any]]


def select_workspace_validators(
    *,
    proposed_operations: list[Mapping[str, Any]],
    diff_summary: Mapping[str, Any] | None,
) -> list[str]:
    selected = {
        "schema_validator",
        "paper_reference_validator",
        "branch_integrity_validator",
        "paper_path_validator",
        "visible_budget_validator",
        "similar_papers_context_validator",
        "operation_target_validator",
    }
    operation_types = {
        str(operation.get("operation_type")) for operation in proposed_operations
    }
    if "update_workspace_subtree" in operation_types or (
        diff_summary and diff_summary.get("appears_global")
    ):
        selected.add("unrelated_rewrite_validator")
    if "promote_candidate_paper" in operation_types:
        selected.add("paper_reference_validator")
    return sorted(selected)


def run_workspace_validator(
    *,
    validator_name: str,
    payload: ValidatorPayload,
) -> dict[str, Any]:
    validator = WORKSPACE_VALIDATORS.get(validator_name)
    if validator is None:
        return _result(
            validator_name,
            valid=False,
            errors=[f"unknown workspace validator: {validator_name}"],
        )
    return validator(payload)


def schema_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    candidate_artifact = _candidate_artifact_for_validation(payload)
    validation = validate_workspace(
        proposed,
        candidate_artifact,
        require_empty_similar_papers=False,
    )
    return _result(
        "schema_validator",
        valid=validation.is_valid,
        errors=validation.errors,
        warnings=validation.warnings,
        stats={
            "visible_paper_count": len(proposed.get("paper_cards") or {}),
        },
    )


def paper_reference_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    candidate_ids = _candidate_ids(payload)
    errors: list[str] = []
    warnings: list[str] = []
    if not candidate_ids:
        warnings.append("No candidate artifact was available for strict paper reference validation.")
    for paper_id in _mapping(proposed.get("paper_cards")):
        if candidate_ids and paper_id not in candidate_ids:
            errors.append(f"visible paper {paper_id!r} is not in the candidate artifact.")
    return _result("paper_reference_validator", valid=not errors, errors=errors, warnings=warnings)


def branch_integrity_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    tree = _mapping(proposed.get("tree"))
    root_id = str(tree.get("root_node_id") or "")
    errors: list[str] = []
    node_ids = {root_id} if root_id else set()
    parent_by_node: dict[str, str] = {}
    if not root_id:
        errors.append("tree.root_node_id is missing.")
    for node in tree.get("nodes") or []:
        if not isinstance(node, Mapping):
            errors.append("tree.nodes contains a non-object entry.")
            continue
        node_id = str(node.get("node_id") or "")
        if not node_id:
            errors.append("tree node is missing node_id.")
            continue
        if node_id in node_ids:
            errors.append(f"duplicate tree node_id: {node_id}.")
        node_ids.add(node_id)
        parent_by_node[node_id] = str(node.get("parent_id") or "")
    for node_id, parent_id in parent_by_node.items():
        if parent_id not in node_ids:
            errors.append(f"tree node {node_id} has unknown parent_id {parent_id!r}.")
    return _result("branch_integrity_validator", valid=not errors, errors=errors)


def paper_path_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    tree = _mapping(proposed.get("tree"))
    tree_ids = {str(tree.get("root_node_id") or "")}
    tree_ids.update(
        str(node.get("node_id"))
        for node in tree.get("nodes") or []
        if isinstance(node, Mapping) and node.get("node_id")
    )
    visible_ids = set(_mapping(proposed.get("paper_cards")))
    errors: list[str] = []
    for path in proposed.get("paper_paths") or []:
        if not isinstance(path, Mapping):
            errors.append("paper_paths contains a non-object entry.")
            continue
        path_id = str(path.get("path_id") or "<missing>")
        if path.get("branch_node_id") not in tree_ids:
            errors.append(f"paper path {path_id} has invalid branch_node_id.")
        for paper_id in path.get("paper_ids") or []:
            if paper_id not in visible_ids:
                errors.append(f"paper path {path_id} references non-visible paper {paper_id!r}.")
    return _result("paper_path_validator", valid=not errors, errors=errors)


def visible_budget_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    visible_count = len(_mapping(proposed.get("paper_cards")))
    budget = _mapping(_mapping(proposed.get("scope")).get("visible_paper_budget"))
    target_max = _int_or_default(budget.get("target_max"), 25)
    hard_max = _int_or_default(budget.get("hard_max_default"), 30)
    warnings: list[str] = []
    errors: list[str] = []
    if visible_count > target_max:
        warnings.append(f"visible paper count {visible_count} is above target_max {target_max}.")
    if visible_count > hard_max:
        errors.append(f"visible paper count {visible_count} exceeds hard_max_default {hard_max}.")
    return _result(
        "visible_budget_validator",
        valid=not errors,
        errors=errors,
        warnings=warnings,
        stats={"visible_paper_count": visible_count},
    )


def similar_papers_context_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    similar_count = 0
    warnings: list[str] = []
    for paper_id, card in _mapping(proposed.get("paper_cards")).items():
        if not isinstance(card, Mapping):
            continue
        similar = card.get("similar_papers")
        if not isinstance(similar, list):
            warnings.append(f"paper card {paper_id} has no similar_papers list.")
            continue
        similar_count += len(similar)
    return _result(
        "similar_papers_context_validator",
        valid=True,
        warnings=warnings,
        stats={"similar_papers_count": similar_count},
    )


def operation_target_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    branch_ids = _branch_ids(proposed)
    paper_ids = set(_mapping(proposed.get("paper_cards")))
    errors: list[str] = []
    for operation in payload.get("proposed_operations") or []:
        if not isinstance(operation, Mapping):
            errors.append("proposed operation is not an object.")
            continue
        target_ids = _mapping(operation.get("target_ids"))
        branch_id = target_ids.get("branch_id") or target_ids.get("to_branch_id")
        paper_id = target_ids.get("paper_id")
        if branch_id and branch_id not in branch_ids:
            errors.append(f"operation targets unknown branch {branch_id!r}.")
        if paper_id and paper_id not in paper_ids and operation.get("operation_type") != "demote_visible_paper":
            errors.append(f"operation targets non-visible paper {paper_id!r}.")
    return _result("operation_target_validator", valid=not errors, errors=errors)


def unrelated_rewrite_validator(payload: ValidatorPayload) -> dict[str, Any]:
    diff_summary = _mapping(payload.get("diff_summary"))
    warnings: list[str] = []
    errors: list[str] = []
    if diff_summary.get("appears_global"):
        warnings.append("Proposal appears global; human review should check unrelated rewrites.")
    if diff_summary.get("operation_count") == 0:
        errors.append("Proposal has no detectable operations.")
    return _result("unrelated_rewrite_validator", valid=not errors, errors=errors, warnings=warnings)


def pipeline_rerun_guardrail_validator(payload: ValidatorPayload) -> dict[str, Any]:
    guardrail = _mapping(payload.get("retrieval_guardrail_result"))
    errors = [] if guardrail.get("allowed") else [str(guardrail.get("rejection_reason") or "pipeline rerun was rejected")]
    return _result(
        "pipeline_rerun_guardrail_validator",
        valid=not errors,
        errors=errors,
        warnings=[str(item) for item in guardrail.get("warnings") or []],
    )


WORKSPACE_VALIDATORS: dict[str, WorkspaceValidator] = {
    "schema_validator": schema_validator,
    "paper_reference_validator": paper_reference_validator,
    "branch_integrity_validator": branch_integrity_validator,
    "paper_path_validator": paper_path_validator,
    "visible_budget_validator": visible_budget_validator,
    "similar_papers_context_validator": similar_papers_context_validator,
    "operation_target_validator": operation_target_validator,
    "unrelated_rewrite_validator": unrelated_rewrite_validator,
    "pipeline_rerun_guardrail_validator": pipeline_rerun_guardrail_validator,
}


def _result(
    validator_name: str,
    *,
    valid: bool,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    stats: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "validator_name": validator_name,
        "valid": valid,
        "errors": errors or [],
        "warnings": warnings or [],
        "stats": stats or {},
    }


def _candidate_artifact_for_validation(payload: ValidatorPayload) -> dict[str, Any]:
    candidate_artifact = payload.get("candidate_artifact")
    if isinstance(candidate_artifact, Mapping):
        return dict(candidate_artifact)
    candidate_pool = [
        dict(item)
        for item in payload.get("candidate_pool") or []
        if isinstance(item, Mapping)
    ]
    proposed = _mapping(payload.get("proposed_workspace"))
    visible_fallback = [
        {
            "paper_id": paper_id,
            "title": card.get("title") or paper_id,
            "abstract": card.get("abstract") or "",
        }
        for paper_id, card in _mapping(proposed.get("paper_cards")).items()
        if isinstance(card, Mapping)
    ]
    return {
        "schema_version": "llm_candidate_papers.v1",
        "topic": proposed.get("topic") or "",
        "workspace": proposed.get("topic") or "",
        "non_survey_papers": candidate_pool or visible_fallback,
        "survey_papers": [],
    }


def _candidate_ids(payload: ValidatorPayload) -> set[str]:
    candidate_artifact = _candidate_artifact_for_validation(payload)
    ids: set[str] = set()
    for key in ("non_survey_papers", "survey_papers"):
        for item in candidate_artifact.get(key) or []:
            if isinstance(item, Mapping) and item.get("paper_id"):
                ids.add(str(item["paper_id"]))
    return ids


def _branch_ids(workspace: Mapping[str, Any]) -> set[str]:
    tree = _mapping(workspace.get("tree"))
    branch_ids = {str(tree.get("root_node_id") or "")}
    branch_ids.update(
        str(node.get("node_id"))
        for node in tree.get("nodes") or []
        if isinstance(node, Mapping) and node.get("node_id")
    )
    return branch_ids


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _int_or_default(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default

