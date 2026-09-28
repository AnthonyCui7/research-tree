from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping as MappingABC
from datetime import UTC, datetime
from typing import Any, Mapping

from langgraph.cache.base import FullKey
from langgraph.cache.memory import InMemoryCache


class SweptInMemoryCache(InMemoryCache):
    """LangGraph's in-memory cache, with expired entries dropped on every write.

    The base class drops an expired entry only when its own key is read again.
    A workspace's context key changes with every version, so a context built
    for an old version is never read again, and in a long-lived API process
    those entries, each a copy of a workspace, would only accumulate.
    """

    def set(self, keys: MappingABC[FullKey, tuple[Any, int | None]]) -> None:
        now = datetime.now(UTC).timestamp()
        with self._lock:
            for namespace in self._cache.values():
                expired = [
                    key
                    for key, (_encoding, _value, expiry) in namespace.items()
                    if expiry is not None and expiry <= now
                ]
                for key in expired:
                    del namespace[key]
        super().set(keys)


def workspace_context_cache_key(state: Mapping[str, Any], *, owner_id: str = "") -> str:
    """What one cached workspace context is good for.

    The cache is process-wide, so the key carries the account and the workspace
    as well as the document's content hash: the context holds full text read
    through that account's repository, and identical documents in two accounts
    must not share a store entry. The action is in the key too. The node's
    writes are what the cache replays, and a context built for a critique is
    not the one an edit reads.
    """

    next_action = state.get("next_action")
    action = dict(next_action) if isinstance(next_action, Mapping) else {}
    payload = {
        "context_schema": "workspace-context.v3",
        "owner_id": owner_id,
        "workspace_id": state.get("workspace_id"),
        "workspace_version_hash": state.get("workspace_version_hash"),
        "action_type": action.get("action_type"),
        "edit_kind": action.get("edit_kind"),
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
