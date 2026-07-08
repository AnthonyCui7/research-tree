from __future__ import annotations

from typing import Any, Mapping

from research_tree.workspace.context import workspace_version_hash


def derive_operations_and_diff_summary(
    *,
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], list[str]]:
    operations: list[dict[str, Any]] = []
    warnings: list[str] = []
    changed_top_level = [
        key
        for key in sorted(set(workspace) | set(proposed_workspace))
        if workspace.get(key) != proposed_workspace.get(key)
    ]

    operations.extend(_branch_operations(workspace, proposed_workspace))
    operations.extend(_paper_card_operations(workspace, proposed_workspace))
    operations.extend(_paper_path_operations(workspace, proposed_workspace))
    operations.extend(_reading_order_operations(workspace, proposed_workspace))
    operations.extend(_root_operations(workspace, proposed_workspace))

    if not operations and changed_top_level:
        operations.append(
            {
                "operation_type": "update_workspace_subtree",
                "target_ids": {"top_level_fields": changed_top_level},
                "before": {"fields": {key: workspace.get(key) for key in changed_top_level}},
                "after": {
                    "fields": {key: proposed_workspace.get(key) for key in changed_top_level}
                },
                "rationale": "The proposal changes workspace fields that are not yet mapped to a narrower operation.",
                "confidence": 0.4,
                "source": "agent",
                "requires_approval": True,
            }
        )

    if len(changed_top_level) > 5:
        warnings.append(
            "Proposed workspace changes many top-level fields; review for unrelated rewrites."
        )

    diff_summary = {
        "workspace_version_hash_before": workspace_version_hash(workspace),
        "workspace_version_hash_after": workspace_version_hash(proposed_workspace),
        "changed_top_level_fields": changed_top_level,
        "operation_count": len(operations),
        "operation_types": sorted({operation["operation_type"] for operation in operations}),
        "visible_paper_count_before": len((workspace.get("paper_cards") or {})),
        "visible_paper_count_after": len((proposed_workspace.get("paper_cards") or {})),
        "appears_global": len(changed_top_level) > 5,
    }
    return operations, diff_summary, warnings


def _branch_operations(
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before_nodes = _nodes_by_id(workspace)
    after_nodes = _nodes_by_id(proposed_workspace)
    operations: list[dict[str, Any]] = []

    for node_id, before in before_nodes.items():
        after = after_nodes.get(node_id)
        if after is None:
            continue
        if before.get("label") != after.get("label"):
            operations.append(
                _operation(
                    "rename_branch",
                    {"branch_id": node_id},
                    before={"label": before.get("label")},
                    after={"label": after.get("label")},
                    rationale="Branch label changed.",
                    confidence=0.8,
                )
            )
        if before.get("parent_id") != after.get("parent_id"):
            operations.append(
                _operation(
                    "update_workspace_subtree",
                    {"branch_id": node_id},
                    before={"parent_id": before.get("parent_id")},
                    after={"parent_id": after.get("parent_id")},
                    rationale="Branch parent changed.",
                    confidence=0.5,
                )
            )

    added_nodes = sorted(set(after_nodes) - set(before_nodes))
    removed_nodes = sorted(set(before_nodes) - set(after_nodes))
    if added_nodes and not removed_nodes:
        operations.append(
            _operation(
                "split_branch",
                {"added_branch_ids": added_nodes},
                before=None,
                after={"branch_ids": added_nodes},
                rationale="Proposal adds branch nodes.",
                confidence=0.5,
            )
        )
    elif removed_nodes and not added_nodes:
        operations.append(
            _operation(
                "merge_branches",
                {"removed_branch_ids": removed_nodes},
                before={"branch_ids": removed_nodes},
                after=None,
                rationale="Proposal removes branch nodes.",
                confidence=0.5,
            )
        )
    elif added_nodes or removed_nodes:
        operations.append(
            _operation(
                "update_workspace_subtree",
                {"added_branch_ids": added_nodes, "removed_branch_ids": removed_nodes},
                before={"removed_branch_ids": removed_nodes},
                after={"added_branch_ids": added_nodes},
                rationale="Proposal changes branch structure.",
                confidence=0.4,
            )
        )
    return operations


def _paper_card_operations(
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before_cards = _mapping(workspace.get("paper_cards"))
    after_cards = _mapping(proposed_workspace.get("paper_cards"))
    operations: list[dict[str, Any]] = []

    for paper_id in sorted(set(after_cards) - set(before_cards)):
        operations.append(
            _operation(
                "promote_candidate_paper",
                {"paper_id": paper_id},
                before=None,
                after=dict(after_cards[paper_id]),
                rationale="Proposal adds a visible paper card.",
                confidence=0.7,
            )
        )
    for paper_id in sorted(set(before_cards) - set(after_cards)):
        operations.append(
            _operation(
                "demote_visible_paper",
                {"paper_id": paper_id},
                before=dict(before_cards[paper_id]),
                after=None,
                rationale="Proposal removes a visible paper card.",
                confidence=0.7,
            )
        )

    for paper_id in sorted(set(before_cards) & set(after_cards)):
        before = before_cards[paper_id]
        after = after_cards[paper_id]
        before_location = _node_id_from_card(before)
        after_location = _node_id_from_card(after)
        if before_location != after_location:
            operations.append(
                _operation(
                    "move_paper",
                    {"paper_id": paper_id, "from_branch_id": before_location, "to_branch_id": after_location},
                    before={"primary_tree_location": before.get("primary_tree_location")},
                    after={"primary_tree_location": after.get("primary_tree_location")},
                    rationale="Paper primary tree location changed.",
                    confidence=0.8,
                )
            )
        if before != after:
            operations.append(
                _operation(
                    "update_paper_card",
                    {"paper_id": paper_id},
                    before=dict(before),
                    after=dict(after),
                    rationale="Paper card content changed.",
                    confidence=0.6,
                )
            )
    return operations


def _paper_path_operations(
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before_paths = {str(path.get("path_id")): path for path in workspace.get("paper_paths") or [] if isinstance(path, Mapping)}
    after_paths = {str(path.get("path_id")): path for path in proposed_workspace.get("paper_paths") or [] if isinstance(path, Mapping)}
    operations: list[dict[str, Any]] = []
    for path_id in sorted(set(after_paths) - set(before_paths)):
        operations.append(
            _operation(
                "create_paper_path",
                {"path_id": path_id, "branch_id": after_paths[path_id].get("branch_node_id")},
                before=None,
                after=dict(after_paths[path_id]),
                rationale="Proposal adds a paper path.",
                confidence=0.7,
            )
        )
    for path_id in sorted(set(before_paths) & set(after_paths)):
        if before_paths[path_id] != after_paths[path_id]:
            operations.append(
                _operation(
                    "update_workspace_subtree",
                    {"path_id": path_id},
                    before=dict(before_paths[path_id]),
                    after=dict(after_paths[path_id]),
                    rationale="Proposal changes a paper path.",
                    confidence=0.5,
                )
            )
    return operations


def _reading_order_operations(
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    if workspace.get("reading_order") == proposed_workspace.get("reading_order"):
        return []
    return [
        _operation(
            "update_reading_order",
            {},
            before={"reading_order": workspace.get("reading_order") or []},
            after={"reading_order": proposed_workspace.get("reading_order") or []},
            rationale="Proposal changes global reading order.",
            confidence=0.7,
        )
    ]


def _root_operations(
    workspace: Mapping[str, Any],
    proposed_workspace: Mapping[str, Any],
) -> list[dict[str, Any]]:
    before_root = _mapping(workspace.get("root"))
    after_root = _mapping(proposed_workspace.get("root"))
    root_fields = ("overview", "suggested_reading_direction", "key_terms", "open_questions")
    changed = {
        field: {"before": before_root.get(field), "after": after_root.get(field)}
        for field in root_fields
        if before_root.get(field) != after_root.get(field)
    }
    if not changed:
        return []
    return [
        _operation(
            "update_root_overview",
            {"root_id": after_root.get("node_id") or before_root.get("node_id") or "root"},
            before={field: values["before"] for field, values in changed.items()},
            after={field: values["after"] for field, values in changed.items()},
            rationale="Proposal updates the root overview.",
            confidence=0.7,
        )
    ]


def _operation(
    operation_type: str,
    target_ids: dict[str, Any],
    *,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    rationale: str,
    confidence: float,
) -> dict[str, Any]:
    return {
        "operation_type": operation_type,
        "target_ids": target_ids,
        "before": before,
        "after": after,
        "rationale": rationale,
        "confidence": confidence,
        "source": "agent",
        "requires_approval": True,
    }


def _nodes_by_id(workspace: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    return {
        str(node.get("node_id")): node
        for node in tree.get("nodes") or []
        if isinstance(node, Mapping) and node.get("node_id")
    }


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _node_id_from_card(card: Mapping[str, Any]) -> str | None:
    location = card.get("primary_tree_location")
    if isinstance(location, Mapping):
        node_id = location.get("node_id")
        return str(node_id) if node_id else None
    return None
