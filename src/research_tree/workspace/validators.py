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
    errors = list(validation.errors)
    warnings = list(validation.warnings)
    if _explicit_all_visible_papers_removed(payload):
        empty_error = "workspace paper_cards is empty."
        errors = [error for error in errors if error != empty_error]
        if empty_error in validation.errors:
            warnings.append("This proposal removes all visible papers from the workspace.")
    return _result(
        "schema_validator",
        valid=not errors,
        errors=errors,
        warnings=warnings,
        stats={
            "visible_paper_count": len(_mapping(proposed.get("paper_cards"))),
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
    for node in _entries(tree.get("nodes")):
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
        for node in _entries(tree.get("nodes"))
        if isinstance(node, Mapping) and node.get("node_id")
    )
    visible_ids = set(_mapping(proposed.get("paper_cards")))
    survey_ids = _survey_ids(payload)
    errors: list[str] = []
    for path in _entries(proposed.get("paper_paths")):
        if not isinstance(path, Mapping):
            errors.append("paper_paths contains a non-object entry.")
            continue
        path_id = str(path.get("path_id") or "<missing>")
        if str(path.get("branch_node_id") or "") not in tree_ids:
            errors.append(f"paper path {path_id} has invalid branch_node_id.")
        paper_ids = _string_list(path.get("paper_ids"))
        for paper_id in paper_ids:
            if paper_id not in visible_ids:
                errors.append(f"paper path {path_id} references non-visible paper {paper_id!r}.")
            elif paper_id in survey_ids:
                errors.append(
                    f"paper path {path_id} contains survey paper {paper_id!r}; "
                    "surveys belong only on overview anchors."
                )
        step_paper_ids = _paper_step_ids(path, path_id, visible_ids, survey_ids, errors)
        if step_paper_ids and paper_ids and step_paper_ids != paper_ids:
            errors.append(f"paper path {path_id} paper_ids do not match paper_steps order.")
    return _result("paper_path_validator", valid=not errors, errors=errors)


def _paper_step_ids(
    path: Mapping[str, Any],
    path_id: str,
    visible_ids: set[str],
    survey_ids: set[str],
    errors: list[str],
) -> list[str]:
    raw_steps = path.get("paper_steps")
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        errors.append(f"paper path {path_id} paper_steps must be a list.")
        return []
    paper_ids: list[str] = []
    seen_paper_ids: set[str] = set()
    for step in raw_steps:
        if not isinstance(step, Mapping):
            errors.append(f"paper path {path_id} contains a non-object paper_step.")
            continue
        paper_id = str(step.get("paper_id") or "")
        if paper_id not in visible_ids:
            errors.append(f"paper path {path_id} paper_step references non-visible paper {paper_id!r}.")
        elif paper_id in survey_ids:
            errors.append(
                f"paper path {path_id} contains survey paper {paper_id!r}; "
                "surveys belong only on overview anchors."
            )
        if paper_id in seen_paper_ids:
            errors.append(f"paper path {path_id} repeats paper {paper_id!r}.")
        seen_paper_ids.add(paper_id)
        if not str(step.get("why_read_here") or "").strip():
            errors.append(f"paper path {path_id} paper_step for {paper_id!r} is missing why_read_here.")
        paper_ids.append(paper_id)
    return paper_ids


def visible_budget_validator(payload: ValidatorPayload) -> dict[str, Any]:
    proposed = _mapping(payload.get("proposed_workspace"))
    visible_count = len(_mapping(proposed.get("paper_cards")))
    budget = _mapping(_mapping(proposed.get("scope")).get("visible_paper_budget"))
    target_max = _int_or_default(budget.get("target_max"), 25)
    warnings: list[str] = []
    if visible_count > target_max:
        warnings.append(f"visible paper count {visible_count} is above target_max {target_max}.")
    return _result(
        "visible_budget_validator",
        valid=True,
        errors=[],
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
        # Ids are read back from the proposed document, so they are whatever
        # it held; compared as text, never as the value itself.
        branch_id = str(target_ids.get("branch_id") or target_ids.get("to_branch_id") or "")
        paper_id = str(target_ids.get("paper_id") or "")
        # A removed path names the branch it belonged to, and a proposal that
        # removes a branch removes its paths with it: that branch is gone from
        # the proposed tree by design, not by mistake.
        if (
            branch_id
            and branch_id not in branch_ids
            and operation.get("operation_type") != "remove_paper_path"
        ):
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


WORKSPACE_VALIDATORS: dict[str, WorkspaceValidator] = {
    "schema_validator": schema_validator,
    "paper_reference_validator": paper_reference_validator,
    "branch_integrity_validator": branch_integrity_validator,
    "paper_path_validator": paper_path_validator,
    "visible_budget_validator": visible_budget_validator,
    "similar_papers_context_validator": similar_papers_context_validator,
    "operation_target_validator": operation_target_validator,
    "unrelated_rewrite_validator": unrelated_rewrite_validator,
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
    # Without the pipeline's artifact the document itself says which papers
    # exist, and a card's role says which of them are surveys: the placement
    # rule for surveys holds for a hand edit as it does for the assistant.
    visible_fallback = [
        {
            "paper_id": paper_id,
            "title": card.get("title") or paper_id,
            "abstract": card.get("abstract") or "",
            "is_survey": "survey" in str(card.get("paper_role") or "").casefold(),
        }
        for paper_id, card in _mapping(proposed.get("paper_cards")).items()
        # A blank key is the schema validator's to report; as a candidate it
        # would have no id at all.
        if isinstance(card, Mapping) and str(paper_id).strip()
    ]
    discarded_fallback = [
        {
            "paper_id": str(item.get("paper_id")).strip(),
            "title": item.get("title") or item.get("paper_id"),
            "abstract": item.get("abstract") or "",
            "is_survey": bool(item.get("is_survey")),
        }
        for item in _entries(proposed.get("discarded_candidates"))
        if isinstance(item, Mapping) and str(item.get("paper_id") or "").strip()
    ]
    # A survey anchor can be referenced by the root or a branch without a
    # card of its own. It is part of the document already, so it must not
    # make every later edit "reference a paper that is not a candidate".
    named = {paper["paper_id"] for paper in [*visible_fallback, *discarded_fallback]}
    anchor_fallback = [
        {"paper_id": anchor_id, "title": anchor_id, "abstract": "", "is_survey": True}
        for anchor_id in _survey_anchor_ids(proposed)
        if anchor_id not in named
    ]
    fallback = candidate_pool or [*visible_fallback, *discarded_fallback, *anchor_fallback]
    return {
        "schema_version": "llm_candidate_papers.v1",
        "topic": proposed.get("topic") or "",
        "workspace": proposed.get("topic") or "",
        "non_survey_papers": [paper for paper in fallback if not paper.get("is_survey")],
        "survey_papers": [paper for paper in fallback if paper.get("is_survey")],
    }


def _explicit_all_visible_papers_removed(payload: ValidatorPayload) -> bool:
    proposed = _mapping(payload.get("proposed_workspace"))
    if _mapping(proposed.get("paper_cards")):
        return False
    before_cards = _mapping(_mapping(payload.get("workspace")).get("paper_cards"))
    if not before_cards:
        return False
    removed = {
        str(operation.get("target_ids", {}).get("paper_id"))
        for operation in payload.get("proposed_operations") or []
        if isinstance(operation, Mapping)
        and operation.get("operation_type") == "demote_visible_paper"
        and isinstance(operation.get("target_ids"), Mapping)
        and operation.get("target_ids", {}).get("paper_id")
    }
    return set(before_cards) <= removed


def _candidate_ids(payload: ValidatorPayload) -> set[str]:
    candidate_artifact = _candidate_artifact_for_validation(payload)
    ids: set[str] = set()
    for key in ("non_survey_papers", "survey_papers"):
        for item in candidate_artifact.get(key) or []:
            if isinstance(item, Mapping) and item.get("paper_id"):
                ids.add(str(item["paper_id"]))
    return ids


def _survey_ids(payload: ValidatorPayload) -> set[str]:
    candidate_artifact = _candidate_artifact_for_validation(payload)
    return {
        str(item["paper_id"])
        for item in candidate_artifact.get("survey_papers") or []
        if isinstance(item, Mapping) and item.get("paper_id")
    }


def _survey_anchor_ids(workspace: Mapping[str, Any]) -> list[str]:
    anchors = _string_list(_mapping(workspace.get("root")).get("survey_anchor_paper_ids"))
    for node in _entries(_mapping(workspace.get("tree")).get("nodes")):
        if isinstance(node, Mapping) and node.get("survey_anchor_paper_id"):
            anchors.append(str(node["survey_anchor_paper_id"]))
    return list(dict.fromkeys(anchor for anchor in anchors if anchor))


def _branch_ids(workspace: Mapping[str, Any]) -> set[str]:
    tree = _mapping(workspace.get("tree"))
    branch_ids = {str(tree.get("root_node_id") or "")}
    branch_ids.update(
        str(node.get("node_id"))
        for node in _entries(tree.get("nodes"))
        if isinstance(node, Mapping) and node.get("node_id")
    )
    return branch_ids


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _entries(value: Any) -> list[Any]:
    """A field that should be a list; anything else reads as empty here and is
    reported by the schema validator, rather than raised on iteration."""

    return value if isinstance(value, list) else []


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
