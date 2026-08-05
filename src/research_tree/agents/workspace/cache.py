from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def workspace_context_cache_key(state: Mapping[str, Any]) -> str:
    payload = {
        "context_schema": "workspace-context.v2",
        "workspace_version_hash": state.get("workspace_version_hash"),
        "target_branch_id": _target_branch_id(state),
        "target_paper_ids": _target_paper_ids(state),
        "include_similar_papers": needs_similar_paper_context(state),
    }
    return _stable_key("workspace-context", payload)


def _target_branch_id(state: Mapping[str, Any]) -> str | None:
    next_action = state.get("next_action")
    if isinstance(next_action, Mapping) and next_action.get("target_branch_id"):
        return str(next_action["target_branch_id"])
    return None


def _target_paper_ids(state: Mapping[str, Any]) -> list[str]:
    next_action = state.get("next_action")
    if not isinstance(next_action, Mapping):
        return []
    return sorted(str(item) for item in next_action.get("target_paper_ids") or [])


def needs_similar_paper_context(state: Mapping[str, Any]) -> bool:
    """Similar-paper context is built only when the model asked for that edit.

    The model declares the edit kind on its tool call; message wording never
    routes an edit.
    """

    next_action = state.get("next_action")
    return (
        isinstance(next_action, Mapping)
        and next_action.get("edit_kind") == "refresh_similar_papers"
    )


def _stable_key(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{prefix}:{digest}"
