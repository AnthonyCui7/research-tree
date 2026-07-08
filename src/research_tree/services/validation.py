from __future__ import annotations

import re
from typing import Any, Mapping

from research_tree.services.errors import InvalidResourceIdError


_SAFE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_.:-]{1,180}$")


def validate_resource_id(value: str, *, field_name: str) -> str:
    resource_id = value.strip()
    if resource_id in {"", ".", ".."} or not _SAFE_ID_PATTERN.fullmatch(resource_id):
        raise InvalidResourceIdError(
            f"{field_name} must contain only letters, numbers, '.', '_', ':', or '-'."
        )
    return resource_id


def as_mapping(value: Any, *, field_name: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        from research_tree.services.errors import InvalidPayloadError

        raise InvalidPayloadError(f"{field_name} must be a JSON object.")
    return dict(value)


def operation_target_ids(operations: list[dict[str, Any]]) -> dict[str, Any]:
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

