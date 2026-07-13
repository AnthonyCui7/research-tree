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
        "include_similar_papers": _needs_similar_paper_context(state),
    }
    return _stable_key("workspace-context", payload)


def _target_branch_id(state: Mapping[str, Any]) -> str | None:
    for key in ("next_action", "intent"):
        value = state.get(key)
        if isinstance(value, Mapping) and value.get("target_branch_id"):
            return str(value["target_branch_id"])
    return None


def _target_paper_ids(state: Mapping[str, Any]) -> list[str]:
    paper_ids: set[str] = set()
    for key in ("next_action", "intent"):
        value = state.get(key)
        if isinstance(value, Mapping):
            paper_ids.update(str(item) for item in value.get("target_paper_ids") or [])
    return sorted(paper_ids)


def _needs_similar_paper_context(state: Mapping[str, Any]) -> bool:
    message = str(state.get("user_message") or "").casefold()
    next_action = state.get("next_action")
    instruction = (
        str(next_action.get("modification_instruction") or "").casefold()
        if isinstance(next_action, Mapping)
        else ""
    )
    text = f"{message} {instruction}"
    return "similar paper" in text or "related paper" in text


def _stable_key(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{prefix}:{digest}"
