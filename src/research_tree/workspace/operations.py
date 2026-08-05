from __future__ import annotations

import copy
from datetime import UTC, datetime
from typing import Any, Mapping

from research_tree.workspace.context import workspace_version_hash


ALLOWED_SET_FIELDS: dict[str, set[str]] = {
    "workspace": {"title", "topic"},
    "root": {
        "overview",
        "suggested_reading_direction",
        "key_terms",
        "open_questions",
    },
    "branch": {
        "label",
        "description",
        "why_it_matters",
        "tags",
        "open_questions",
    },
    "paper_card": {
        "title",
        "tldr",
        "tldr_source",
        "paper_role",
        "importance",
        "concise_importance",
        "summary",
        "read_before",
        "read_after",
        "similar_papers",
    },
}


class WorkspacePatchError(ValueError):
    """Raised when a structured workspace patch cannot be applied atomically."""


def apply_structured_workspace_patch(
    *,
    base_workspace: Mapping[str, Any],
    operations: list[Mapping[str, Any]],
    removed_by: str = "workspace_agent",
) -> dict[str, Any]:
    if not isinstance(operations, list) or not operations:
        raise WorkspacePatchError("structured workspace patch requires operations.")
    proposed = copy.deepcopy(dict(base_workspace))
    base_hash = workspace_version_hash(base_workspace)
    for index, operation in enumerate(operations):
        if not isinstance(operation, Mapping):
            raise WorkspacePatchError(f"operation {index} is not an object.")
        normalized_operation = _normalize_operation(operation)
        _apply_structured_operation(
            proposed,
            normalized_operation,
            base_hash=base_hash,
            removed_by=removed_by,
            operation_index=index,
        )
    return proposed


def remove_visible_paper_operation(
    *,
    paper_id: str,
    branch_id: str | None = None,
) -> dict[str, Any]:
    operation: dict[str, Any] = {
        "op": "remove",
        "entity_type": "paper_placement",
        "paper_id": paper_id,
    }
    if branch_id:
        operation["branch_id"] = branch_id
    return operation


def operation_target_ids(operations: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize which branches, papers, and paths a set of operations touches.

    Events and reviews are indexed by these ids, so the same summary has to come
    out of the agent graph and the review service alike.
    """

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


def _apply_structured_operation(
    workspace: dict[str, Any],
    operation: Mapping[str, Any],
    *,
    base_hash: str,
    removed_by: str,
    operation_index: int,
) -> None:
    verb = str(operation.get("op") or "")
    entity_type = str(operation.get("entity_type") or "")
    if verb == "set":
        _apply_set(workspace, operation)
    elif verb == "insert":
        _apply_insert(workspace, operation)
    elif verb == "remove":
        _apply_remove(
            workspace,
            operation,
            base_hash=base_hash,
            removed_by=removed_by,
        )
    elif verb == "move":
        _apply_move(workspace, operation)
    elif verb == "reorder":
        _apply_reorder(workspace, operation)
    else:
        raise WorkspacePatchError(
            f"operation {operation_index} has unsupported op {verb!r}."
        )
    if entity_type and entity_type not in {
        "workspace",
        "root",
        "branch",
        "paper_card",
        "paper_placement",
        "branch_papers",
        "paper_path",
        "reading_order",
    }:
        raise WorkspacePatchError(
            f"operation {operation_index} has unsupported entity_type {entity_type!r}."
        )


def _normalize_operation(operation: Mapping[str, Any]) -> dict[str, Any]:
    normalized = copy.deepcopy(dict(operation))
    target = normalized.get("target")
    if not isinstance(target, Mapping):
        return normalized

    target_type = str(target.get("type") or "")
    if target_type and not normalized.get("entity_type"):
        normalized["entity_type"] = target_type
    for field_name in (
        "paper_id",
        "branch_id",
        "path_id",
        "parent_id",
        "from_branch_id",
        "to_branch_id",
        "to_parent_id",
        "field",
    ):
        if field_name in target and not normalized.get(field_name):
            normalized[field_name] = target[field_name]
    target_id = target.get("id")
    if target_id and target_type == "branch" and not normalized.get("branch_id"):
        normalized["branch_id"] = target_id
    elif target_id and target_type == "paper_card" and not normalized.get("paper_id"):
        normalized["paper_id"] = target_id
    elif target_id and target_type == "paper_path" and not normalized.get("path_id"):
        normalized["path_id"] = target_id

    value = normalized.get("value")
    if target_type == "branch" and isinstance(value, Mapping):
        branch_value = copy.deepcopy(dict(value))
        if branch_value.get("id") and not branch_value.get("node_id"):
            branch_value["node_id"] = branch_value["id"]
        normalized["value"] = branch_value
    return normalized


def _apply_set(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    entity_type = str(operation.get("entity_type") or "")
    field_name = str(operation.get("field") or "")
    if field_name not in ALLOWED_SET_FIELDS.get(entity_type, set()):
        raise WorkspacePatchError(
            f"set is not allowed for {entity_type}.{field_name}."
        )
    target = _target_object(workspace, operation, entity_type)
    target[field_name] = copy.deepcopy(operation.get("value"))


def _apply_insert(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    entity_type = str(operation.get("entity_type") or "")
    if entity_type == "branch":
        _insert_branch(workspace, operation)
    elif entity_type == "paper_placement":
        _insert_paper_placement(workspace, operation)
    else:
        raise WorkspacePatchError(f"insert does not support {entity_type!r}.")


def _apply_remove(
    workspace: dict[str, Any],
    operation: Mapping[str, Any],
    *,
    base_hash: str,
    removed_by: str,
) -> None:
    entity_type = str(operation.get("entity_type") or "")
    if entity_type == "paper_placement":
        _remove_paper_placement(
            workspace,
            str(operation.get("paper_id") or ""),
            branch_id=_optional_str(operation.get("branch_id")),
            base_hash=base_hash,
            removed_by=removed_by,
        )
    elif entity_type == "branch":
        _remove_branch(workspace, str(operation.get("branch_id") or ""))
    else:
        raise WorkspacePatchError(f"remove does not support {entity_type!r}.")


def _apply_move(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    entity_type = str(operation.get("entity_type") or "")
    if entity_type == "paper_placement":
        paper_id = str(operation.get("paper_id") or "")
        to_branch_id = str(operation.get("to_branch_id") or "")
        if not paper_id or not to_branch_id:
            raise WorkspacePatchError("move paper_placement requires paper_id and to_branch_id.")
        if to_branch_id not in _nodes_by_id(workspace):
            raise WorkspacePatchError(f"destination branch does not exist: {to_branch_id}.")
        _remove_paper_references(workspace, paper_id)
        _place_visible_paper(
            workspace,
            paper_id=paper_id,
            branch_id=to_branch_id,
            index=_optional_int(operation.get("index")),
            path_id=_optional_str(operation.get("path_id")),
        )
    elif entity_type == "branch":
        _move_branch(workspace, operation)
    else:
        raise WorkspacePatchError(f"move does not support {entity_type!r}.")


def _apply_reorder(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    entity_type = str(operation.get("entity_type") or "")
    ordered_ids = _string_list(operation.get("ordered_ids"))
    if entity_type == "branch_papers":
        branch_id = str(operation.get("branch_id") or "")
        node = _required_node(workspace, branch_id)
        current = _string_list(node.get("primary_paper_ids"))
        _require_same_members(current, ordered_ids, "branch primary papers")
        node["primary_paper_ids"] = ordered_ids
    elif entity_type == "paper_path":
        path_id = str(operation.get("path_id") or "")
        path = _required_path(workspace, path_id)
        current = _string_list(path.get("paper_ids"))
        _require_same_members(current, ordered_ids, "paper path")
        path["paper_ids"] = ordered_ids
        steps_by_id = {
            str(step.get("paper_id")): step
            for step in path.get("paper_steps") or []
            if isinstance(step, dict) and step.get("paper_id")
        }
        if steps_by_id:
            path["paper_steps"] = [steps_by_id[paper_id] for paper_id in ordered_ids]
    elif entity_type == "reading_order":
        current = [
            str(item.get("paper_id"))
            for item in workspace.get("reading_order") or []
            if isinstance(item, dict) and item.get("paper_id")
        ]
        _require_same_members(current, ordered_ids, "reading order")
        entries = {
            str(item.get("paper_id")): item
            for item in workspace.get("reading_order") or []
            if isinstance(item, dict) and item.get("paper_id")
        }
        workspace["reading_order"] = [
            {**copy.deepcopy(entries[paper_id]), "order": index}
            for index, paper_id in enumerate(ordered_ids, start=1)
        ]
    else:
        raise WorkspacePatchError(f"reorder does not support {entity_type!r}.")


def _target_object(
    workspace: dict[str, Any],
    operation: Mapping[str, Any],
    entity_type: str,
) -> dict[str, Any]:
    if entity_type == "workspace":
        return workspace
    if entity_type == "root":
        root = workspace.get("root")
        if not isinstance(root, dict):
            raise WorkspacePatchError("workspace root is missing.")
        return root
    if entity_type == "branch":
        return _required_node(workspace, str(operation.get("branch_id") or ""))
    if entity_type == "paper_card":
        cards = _required_mapping(workspace.get("paper_cards"), "paper_cards")
        paper_id = str(operation.get("paper_id") or "")
        card = cards.get(paper_id)
        if not isinstance(card, dict):
            raise WorkspacePatchError(f"visible paper card does not exist: {paper_id}.")
        return card
    raise WorkspacePatchError(f"set does not support {entity_type!r}.")


def _insert_branch(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    value = operation.get("value")
    if not isinstance(value, Mapping):
        raise WorkspacePatchError("insert branch requires value object.")
    node = copy.deepcopy(dict(value))
    node_id = str(node.get("node_id") or operation.get("branch_id") or "")
    parent_id = str(node.get("parent_id") or operation.get("parent_id") or "")
    if not node_id or not parent_id:
        raise WorkspacePatchError("insert branch requires node_id and parent_id.")
    nodes_by_id = _nodes_by_id(workspace)
    if node_id in nodes_by_id:
        raise WorkspacePatchError(f"branch already exists: {node_id}.")
    if parent_id != _root_id(workspace) and parent_id not in nodes_by_id:
        raise WorkspacePatchError(f"parent branch does not exist: {parent_id}.")
    node["node_id"] = node_id
    node["parent_id"] = parent_id
    node.setdefault("child_node_ids", [])
    node.setdefault("primary_paper_ids", [])
    node.setdefault("secondary_paper_ids", [])
    tree = _required_mapping(workspace.get("tree"), "tree")
    nodes = tree.setdefault("nodes", [])
    if not isinstance(nodes, list):
        raise WorkspacePatchError("tree.nodes must be a list.")
    nodes.insert(_bounded_index(operation.get("index"), len(nodes)), node)
    parent = nodes_by_id.get(parent_id)
    if isinstance(parent, dict):
        children = parent.setdefault("child_node_ids", [])
        if isinstance(children, list) and node_id not in children:
            children.insert(_bounded_index(operation.get("index"), len(children)), node_id)


def _insert_paper_placement(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    paper_id = str(operation.get("paper_id") or "")
    branch_id = str(operation.get("branch_id") or "")
    if not paper_id or not branch_id:
        raise WorkspacePatchError("insert paper_placement requires paper_id and branch_id.")
    if branch_id not in _nodes_by_id(workspace):
        raise WorkspacePatchError(f"branch does not exist: {branch_id}.")
    cards = _required_mapping(workspace.get("paper_cards"), "paper_cards")
    if paper_id not in cards:
        restored = _removed_placement_for_paper(workspace, paper_id)
        value = operation.get("value")
        card = (
            copy.deepcopy(restored.get("paper_card"))
            if restored and isinstance(restored.get("paper_card"), Mapping)
            else copy.deepcopy(_mapping(value).get("paper_card"))
        )
        if not card:
            raise WorkspacePatchError(f"paper card data is required for {paper_id}.")
        card["paper_id"] = card.get("paper_id") or paper_id
        cards[paper_id] = card
    _place_visible_paper(
        workspace,
        paper_id=paper_id,
        branch_id=branch_id,
        index=_optional_int(operation.get("index")),
        path_id=_optional_str(operation.get("path_id")),
    )


def _remove_branch(workspace: dict[str, Any], branch_id: str) -> None:
    if not branch_id:
        raise WorkspacePatchError("remove branch requires branch_id.")
    node = _required_node(workspace, branch_id)
    if _string_list(node.get("primary_paper_ids")) or _string_list(node.get("secondary_paper_ids")):
        raise WorkspacePatchError("cannot remove branch while it still contains papers.")
    if _string_list(node.get("child_node_ids")):
        raise WorkspacePatchError("cannot remove branch while it still has children.")
    tree = _required_mapping(workspace.get("tree"), "tree")
    tree["nodes"] = [
        item
        for item in tree.get("nodes") or []
        if not (isinstance(item, dict) and str(item.get("node_id") or "") == branch_id)
    ]
    for item in tree.get("nodes") or []:
        if isinstance(item, dict):
            item["child_node_ids"] = [
                child_id
                for child_id in _string_list(item.get("child_node_ids"))
                if child_id != branch_id
            ]


def _move_branch(workspace: dict[str, Any], operation: Mapping[str, Any]) -> None:
    branch_id = str(operation.get("branch_id") or "")
    parent_id = str(operation.get("to_parent_id") or "")
    node = _required_node(workspace, branch_id)
    nodes_by_id = _nodes_by_id(workspace)
    if parent_id != _root_id(workspace) and parent_id not in nodes_by_id:
        raise WorkspacePatchError(f"destination parent does not exist: {parent_id}.")
    if branch_id == parent_id:
        raise WorkspacePatchError("branch cannot be moved under itself.")
    old_parent_id = str(node.get("parent_id") or "")
    node["parent_id"] = parent_id
    for item in nodes_by_id.values():
        if isinstance(item, dict):
            item["child_node_ids"] = [
                child_id
                for child_id in _string_list(item.get("child_node_ids"))
                if child_id != branch_id
            ]
    parent = nodes_by_id.get(parent_id)
    if isinstance(parent, dict):
        children = parent.setdefault("child_node_ids", [])
        if isinstance(children, list):
            children.insert(_bounded_index(operation.get("index"), len(children)), branch_id)
    if old_parent_id and parent_id == _root_id(workspace):
        return


def _remove_paper_placement(
    workspace: dict[str, Any],
    paper_id: str,
    *,
    branch_id: str | None,
    base_hash: str,
    removed_by: str,
) -> None:
    if not paper_id:
        raise WorkspacePatchError("remove paper_placement requires paper_id.")
    cards = _required_mapping(workspace.get("paper_cards"), "paper_cards")
    card = cards.get(paper_id)
    if not isinstance(card, dict):
        raise WorkspacePatchError(f"visible paper does not exist: {paper_id}.")
    existing_branch_ids = _branch_ids_for_paper(workspace, paper_id)
    if branch_id and branch_id not in existing_branch_ids:
        raise WorkspacePatchError(
            f"paper {paper_id} is not placed in branch {branch_id}."
        )
    placement = _removed_paper_placement_record(
        workspace,
        paper_id=paper_id,
        card=card,
        base_hash=base_hash,
        removed_by=removed_by,
    )
    _store_removed_paper_placement(workspace, placement)
    cards.pop(paper_id)
    _remove_paper_references(workspace, paper_id)


def _place_visible_paper(
    workspace: dict[str, Any],
    *,
    paper_id: str,
    branch_id: str,
    index: int | None,
    path_id: str | None,
) -> None:
    node = _required_node(workspace, branch_id)
    cards = _required_mapping(workspace.get("paper_cards"), "paper_cards")
    card = cards.get(paper_id)
    if not isinstance(card, dict):
        raise WorkspacePatchError(f"visible paper card does not exist: {paper_id}.")
    card["primary_tree_location"] = {
        "node_id": branch_id,
        "label": node.get("label"),
    }
    paper_ids = _string_list(node.get("primary_paper_ids"))
    paper_ids = [item for item in paper_ids if item != paper_id]
    paper_ids.insert(_bounded_index(index, len(paper_ids)), paper_id)
    node["primary_paper_ids"] = paper_ids
    if path_id:
        path = _required_path(workspace, path_id)
        path_ids = _string_list(path.get("paper_ids"))
        path_ids = [item for item in path_ids if item != paper_id]
        path_ids.insert(_bounded_index(index, len(path_ids)), paper_id)
        path["paper_ids"] = path_ids


def _removed_paper_placement_record(
    workspace: dict[str, Any],
    *,
    paper_id: str,
    card: dict[str, Any],
    base_hash: str,
    removed_by: str,
) -> dict[str, Any]:
    branch_records: list[dict[str, Any]] = []
    for node in _nodes_by_id(workspace).values():
        if not isinstance(node, Mapping):
            continue
        for field_name in ("primary_paper_ids", "secondary_paper_ids"):
            paper_ids = _string_list(node.get(field_name))
            if paper_id in paper_ids:
                branch_records.append(
                    {
                        "branch_id": node.get("node_id"),
                        "field": field_name,
                        "position": paper_ids.index(paper_id),
                    }
                )
    path_records: list[dict[str, Any]] = []
    for path in workspace.get("paper_paths") or []:
        if not isinstance(path, Mapping):
            continue
        paper_ids = _string_list(path.get("paper_ids"))
        if paper_id not in paper_ids:
            continue
        step = next(
            (
                copy.deepcopy(item)
                for item in path.get("paper_steps") or []
                if isinstance(item, Mapping)
                and str(item.get("paper_id") or "") == paper_id
            ),
            None,
        )
        path_records.append(
            {
                "path_id": path.get("path_id"),
                "branch_id": path.get("branch_node_id"),
                "position": paper_ids.index(paper_id),
                "paper_step": step,
            }
        )
    reading_order_entry = next(
        (
            copy.deepcopy(item)
            for item in workspace.get("reading_order") or []
            if isinstance(item, Mapping) and str(item.get("paper_id") or "") == paper_id
        ),
        None,
    )
    root = _mapping(workspace.get("root"))
    root_references = {
        field_name: _string_list(root.get(field_name))
        for field_name in ("representative_paper_ids", "survey_anchor_paper_ids")
        if paper_id in _string_list(root.get(field_name))
    }
    return {
        "paper_id": paper_id,
        "paper_card": copy.deepcopy(card),
        "branch_placements": branch_records,
        "path_placements": path_records,
        "reading_order_entry": reading_order_entry,
        "root_references": root_references,
        "removed_at": datetime.now(UTC).isoformat(),
        "removed_by": removed_by,
        "removed_from_workspace_version": base_hash,
    }


def _store_removed_paper_placement(
    workspace: dict[str, Any],
    placement: dict[str, Any],
) -> None:
    records = workspace.setdefault("removed_paper_placements", [])
    if not isinstance(records, list):
        raise WorkspacePatchError("removed_paper_placements must be a list.")
    paper_id = str(placement.get("paper_id") or "")
    records[:] = [
        record
        for record in records
        if not (isinstance(record, Mapping) and str(record.get("paper_id") or "") == paper_id)
    ]
    records.append(placement)


def _remove_paper_references(workspace: dict[str, Any], paper_id: str) -> None:
    for node in _nodes_by_id(workspace).values():
        if not isinstance(node, dict):
            continue
        for field_name in ("primary_paper_ids", "secondary_paper_ids"):
            node[field_name] = [
                item
                for item in _string_list(node.get(field_name))
                if item != paper_id
            ]
    root = workspace.get("root")
    if isinstance(root, dict):
        for field_name in ("representative_paper_ids", "survey_anchor_paper_ids"):
            root[field_name] = [
                item
                for item in _string_list(root.get(field_name))
                if item != paper_id
            ]
    kept_paths: list[dict[str, Any]] = []
    for path in workspace.get("paper_paths") or []:
        if not isinstance(path, dict):
            continue
        path["paper_ids"] = [
            item
            for item in _string_list(path.get("paper_ids"))
            if item != paper_id
        ]
        if isinstance(path.get("paper_steps"), list):
            path["paper_steps"] = [
                step
                for step in path["paper_steps"]
                if isinstance(step, dict) and str(step.get("paper_id") or "") != paper_id
            ]
        if path["paper_ids"]:
            kept_paths.append(path)
    workspace["paper_paths"] = kept_paths
    workspace["reading_order"] = [
        copy.deepcopy(item)
        for item in workspace.get("reading_order") or []
        if isinstance(item, dict) and str(item.get("paper_id") or "") != paper_id
    ]
    for index, item in enumerate(workspace["reading_order"], start=1):
        item["order"] = index
    for card in _required_mapping(workspace.get("paper_cards"), "paper_cards").values():
        if not isinstance(card, dict):
            continue
        for field_name in ("read_before", "read_after"):
            card[field_name] = [
                item
                for item in _string_list(card.get(field_name))
                if item != paper_id
            ]


def _branch_ids_for_paper(workspace: dict[str, Any], paper_id: str) -> set[str]:
    branch_ids: set[str] = set()
    for node in _nodes_by_id(workspace).values():
        if not isinstance(node, Mapping):
            continue
        if paper_id in _string_list(node.get("primary_paper_ids")) or paper_id in _string_list(node.get("secondary_paper_ids")):
            branch_ids.add(str(node.get("node_id") or ""))
    return branch_ids


def _required_node(workspace: dict[str, Any], node_id: str) -> dict[str, Any]:
    if not node_id:
        raise WorkspacePatchError("branch_id is required.")
    node = _nodes_by_id(workspace).get(node_id)
    if not isinstance(node, dict):
        raise WorkspacePatchError(f"branch does not exist: {node_id}.")
    return node


def _required_path(workspace: dict[str, Any], path_id: str) -> dict[str, Any]:
    if not path_id:
        raise WorkspacePatchError("path_id is required.")
    for path in workspace.get("paper_paths") or []:
        if isinstance(path, dict) and str(path.get("path_id") or "") == path_id:
            return path
    raise WorkspacePatchError(f"paper path does not exist: {path_id}.")


def _removed_placement_for_paper(
    workspace: dict[str, Any],
    paper_id: str,
) -> dict[str, Any] | None:
    for record in reversed(workspace.get("removed_paper_placements") or []):
        if isinstance(record, dict) and str(record.get("paper_id") or "") == paper_id:
            return record
    return None


def _nodes_by_id(workspace: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    return {
        str(node.get("node_id")): node
        for node in tree.get("nodes") or []
        if isinstance(node, Mapping) and node.get("node_id")
    }


def _root_id(workspace: Mapping[str, Any]) -> str:
    tree = workspace.get("tree") if isinstance(workspace.get("tree"), Mapping) else {}
    return str(tree.get("root_node_id") or "root")


def _require_same_members(
    current: list[str],
    ordered: list[str],
    label: str,
) -> None:
    if sorted(current) != sorted(ordered) or len(current) != len(ordered):
        raise WorkspacePatchError(f"reorder {label} must contain exactly the current members.")


def _required_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise WorkspacePatchError(f"{field_name} must be an object.")
    return value


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item)]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise WorkspacePatchError(f"index must be an integer; got {value!r}.") from None


def _bounded_index(value: Any, length: int) -> int:
    index = _optional_int(value)
    if index is None:
        return length
    return max(0, min(index, length))


