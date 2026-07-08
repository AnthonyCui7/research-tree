from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from research_tree.workspace.schemas import (
    WORKSPACE_SCHEMA_VERSION,
    WorkspaceDocument,
    candidate_papers_from_artifact,
    survey_paper_ids_from_artifact,
)


@dataclass(frozen=True)
class WorkspaceValidationResult:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return not self.errors

    def to_json(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "error_count": len(self.errors),
            "warning_count": len(self.warnings),
            "errors": self.errors,
            "warnings": self.warnings,
        }


def validate_workspace(
    workspace_payload: Mapping[str, Any],
    candidate_artifact: Mapping[str, Any],
    *,
    require_empty_similar_papers: bool = True,
) -> WorkspaceValidationResult:
    workspace = WorkspaceDocument.from_mapping(workspace_payload)
    candidate_papers = candidate_papers_from_artifact(candidate_artifact)
    candidate_ids = set(candidate_papers)
    survey_ids = survey_paper_ids_from_artifact(candidate_artifact)
    errors: list[str] = []
    warnings: list[str] = []

    for field_name in (
        "scope",
        "root",
        "tree",
        "paper_paths",
        "paper_cards",
        "reading_order",
        "comparison_tables",
        "discarded_candidates",
        "provenance",
    ):
        if field_name not in workspace_payload:
            errors.append(f"workspace is missing top-level field {field_name!r}.")

    if workspace.schema_version != WORKSPACE_SCHEMA_VERSION:
        errors.append(
            "workspace schema_version must be "
            f"{WORKSPACE_SCHEMA_VERSION}; got {workspace.schema_version!r}."
        )

    if not workspace.root:
        errors.append("workspace root is missing.")
    if not workspace.tree:
        errors.append("workspace tree is missing.")
    if not workspace.paper_cards:
        errors.append("workspace paper_cards is empty.")
    tree_ids = _validate_tree(workspace, errors)
    visible_paper_ids = set(workspace.paper_cards)

    invalid_visible_ids = sorted(visible_paper_ids - candidate_ids)
    if invalid_visible_ids:
        errors.append(
            "paper_cards contains paper IDs not present in the candidate artifact: "
            f"{invalid_visible_ids}."
        )

    _validate_paper_cards(
        workspace=workspace,
        tree_ids=tree_ids,
        candidate_ids=candidate_ids,
        errors=errors,
        warnings=warnings,
        require_empty_similar_papers=require_empty_similar_papers,
    )
    _validate_paper_paths(
        workspace=workspace,
        tree_ids=tree_ids,
        visible_paper_ids=visible_paper_ids,
        errors=errors,
    )
    _validate_reading_order(
        workspace=workspace,
        visible_paper_ids=visible_paper_ids,
        errors=errors,
    )
    _validate_node_paper_references(
        workspace=workspace,
        visible_paper_ids=visible_paper_ids,
        errors=errors,
    )
    _validate_discarded_candidates(
        workspace=workspace,
        candidate_ids=candidate_ids,
        errors=errors,
    )
    _validate_survey_anchor_ids(
        workspace=workspace,
        candidate_ids=candidate_ids,
        survey_ids=survey_ids,
        errors=errors,
        warnings=warnings,
    )
    _validate_visible_paper_budget(workspace, warnings)

    return WorkspaceValidationResult(errors=errors, warnings=warnings)


def _validate_tree(
    workspace: WorkspaceDocument,
    errors: list[str],
) -> set[str]:
    root_node_id = str(workspace.tree.get("root_node_id") or "")
    if not root_node_id:
        errors.append("tree.root_node_id is missing.")
    node_ids: set[str] = {root_node_id} if root_node_id else set()
    parent_by_node: dict[str, str] = {}

    for node in workspace.tree.get("nodes") or []:
        if not isinstance(node, Mapping):
            errors.append("tree.nodes contains a non-object entry.")
            continue
        node_id = str(node.get("node_id") or "")
        parent_id = str(node.get("parent_id") or "")
        if not node_id:
            errors.append("tree node is missing node_id.")
            continue
        if node_id in node_ids:
            errors.append(f"duplicate tree node_id: {node_id}.")
        node_ids.add(node_id)
        parent_by_node[node_id] = parent_id

    for node_id, parent_id in parent_by_node.items():
        if not parent_id:
            errors.append(f"tree node {node_id} is missing parent_id.")
        elif parent_id not in node_ids:
            errors.append(
                f"tree node {node_id} has unknown parent_id {parent_id!r}."
            )

    for node in workspace.tree.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        for child_id in node.get("child_node_ids") or []:
            if child_id not in node_ids:
                errors.append(
                    f"tree node {node_id} references unknown child_node_id {child_id!r}."
                )

    for node_id in parent_by_node:
        seen: set[str] = set()
        current = node_id
        while current in parent_by_node:
            if current in seen:
                errors.append(f"tree contains a cycle involving node {node_id}.")
                break
            seen.add(current)
            current = parent_by_node[current]

    return node_ids


def _validate_paper_cards(
    workspace: WorkspaceDocument,
    tree_ids: set[str],
    candidate_ids: set[str],
    errors: list[str],
    warnings: list[str],
    require_empty_similar_papers: bool,
) -> None:
    for paper_id, card in workspace.paper_cards.items():
        if str(card.get("paper_id") or paper_id) != paper_id:
            errors.append(
                f"paper_cards key {paper_id!r} does not match card paper_id "
                f"{card.get('paper_id')!r}."
            )
        if paper_id not in candidate_ids:
            continue
        location = card.get("primary_tree_location")
        if not isinstance(location, Mapping):
            errors.append(f"paper card {paper_id} is missing primary_tree_location.")
        else:
            node_id = str(location.get("node_id") or "")
            if node_id not in tree_ids:
                errors.append(
                    f"paper card {paper_id} has invalid primary_tree_location node_id "
                    f"{node_id!r}."
                )
        similar_papers = card.get("similar_papers")
        if not isinstance(similar_papers, list):
            errors.append(f"paper card {paper_id} is missing similar_papers list.")
        elif require_empty_similar_papers and similar_papers:
            warnings.append(
                f"paper card {paper_id} has similar_papers before enrichment."
            )


def _validate_paper_paths(
    workspace: WorkspaceDocument,
    tree_ids: set[str],
    visible_paper_ids: set[str],
    errors: list[str],
) -> None:
    for path in workspace.paper_paths:
        path_id = str(path.get("path_id") or "")
        branch_node_id = str(path.get("branch_node_id") or "")
        if branch_node_id not in tree_ids:
            errors.append(
                f"paper path {path_id or '<missing>'} has invalid branch_node_id "
                f"{branch_node_id!r}."
            )
        for paper_id in path.get("paper_ids") or []:
            if paper_id not in visible_paper_ids:
                errors.append(
                    f"paper path {path_id or '<missing>'} references paper {paper_id!r} "
                    "without a paper card."
                )


def _validate_reading_order(
    workspace: WorkspaceDocument,
    visible_paper_ids: set[str],
    errors: list[str],
) -> None:
    for entry in workspace.reading_order:
        paper_id = entry.get("paper_id")
        if paper_id not in visible_paper_ids:
            errors.append(
                f"reading_order references paper {paper_id!r} without a paper card."
            )


def _validate_node_paper_references(
    workspace: WorkspaceDocument,
    visible_paper_ids: set[str],
    errors: list[str],
) -> None:
    for node in workspace.tree.get("nodes") or []:
        if not isinstance(node, Mapping):
            continue
        node_id = str(node.get("node_id") or "")
        for field_name in ("primary_paper_ids", "secondary_paper_ids"):
            for paper_id in node.get(field_name) or []:
                if paper_id not in visible_paper_ids:
                    errors.append(
                        f"tree node {node_id} {field_name} references paper "
                        f"{paper_id!r} without a paper card."
                    )


def _validate_discarded_candidates(
    workspace: WorkspaceDocument,
    candidate_ids: set[str],
    errors: list[str],
) -> None:
    for discarded in workspace.discarded_candidates:
        paper_id = discarded.get("paper_id")
        if paper_id not in candidate_ids:
            errors.append(
                f"discarded_candidates references unknown paper_id {paper_id!r}."
            )


def _validate_survey_anchor_ids(
    workspace: WorkspaceDocument,
    candidate_ids: set[str],
    survey_ids: set[str],
    errors: list[str],
    warnings: list[str],
) -> None:
    for paper_id in workspace.root.get("survey_anchor_paper_ids") or []:
        if paper_id not in candidate_ids:
            errors.append(f"root survey anchor {paper_id!r} is not a candidate paper.")
        elif paper_id not in survey_ids:
            warnings.append(
                f"root survey anchor {paper_id!r} did not come from survey_papers."
            )


def _validate_visible_paper_budget(
    workspace: WorkspaceDocument,
    warnings: list[str],
) -> None:
    visible_count = len(workspace.paper_cards)
    visible_budget = workspace.scope.get("visible_paper_budget")
    if not isinstance(visible_budget, Mapping):
        visible_budget = {}
    target_min = _int_or_default(visible_budget.get("target_min"), 10)
    target_max = _int_or_default(visible_budget.get("target_max"), 25)
    hard_max = _int_or_default(visible_budget.get("hard_max_default"), 30)

    if visible_count < target_min:
        warnings.append(
            f"visible paper count {visible_count} is below target_min {target_min}."
        )
    if visible_count > target_max:
        warnings.append(
            f"visible paper count {visible_count} is above target_max {target_max}."
        )
    if visible_count > hard_max:
        warnings.append(
            f"visible paper count {visible_count} exceeds hard_max_default {hard_max}."
        )


def _int_or_default(value: Any, default: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
