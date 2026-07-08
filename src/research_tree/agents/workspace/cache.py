from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


def workspace_context_cache_key(state: Mapping[str, Any]) -> str:
    payload = {
        "workspace_version_hash": state.get("workspace_version_hash"),
        "target_branch_id": _target_branch_id(state),
        "target_paper_ids": _target_paper_ids(state),
        "include_similar_papers": True,
        "max_similar_per_paper": 10,
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


def _stable_key(prefix: str, payload: Mapping[str, Any]) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
            "utf-8"
        )
    ).hexdigest()
    return f"{prefix}:{digest}"

